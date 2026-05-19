"""Multi-device sync.

This package houses two coexisting sync paths:

* **Path A — legacy markdown mirror** (defined directly in this module).
  Mirrors ``scopes/**/*.md`` to a user-chosen sync directory and tracks
  per-file fingerprints in ``.memoryd-sync-state.json``.  Callers keep
  using ``from memoryd.sync import export, import_, status, ...`` exactly
  as before.
* **Path B — ``memories.json`` cross-device bundle**.  Lives in
  :mod:`memoryd.sync.memories_json`, :mod:`memoryd.sync.conflict`, and
  :mod:`memoryd.sync.schema`, and is re-exported here so callers can do
  ``from memoryd.sync import export_to_memories_json``.

Both paths are independent and operate on different transports; choosing
between them is a deployment decision (continuous folder sync vs ad-hoc
JSON bundle).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..config import load_config

log = logging.getLogger(__name__)

# Files / dirs that must NEVER enter sync dir
_SYNC_BLACKLIST_NAMES = {"index.db", "index.db-wal", "index.db-shm"}
_SYNC_BLACKLIST_DIRS = {"audit", "grants", "logs", "probe"}
_STATE_FILENAME = ".memoryd-sync-state.json"


def expand_sync_dir(raw: str) -> Path:
    """Expand ~ and env vars; return absolute resolved Path."""
    return Path(raw).expanduser().resolve()


def iter_local_markdown(data_root: Path) -> Iterable[Path]:
    """Yield every .md / .md.enc / .memoryd-sensitive under scopes/, skipping blacklist."""
    scopes = data_root / "scopes"
    if not scopes.exists():
        return
    for path in scopes.rglob("*"):
        if not path.is_file():
            continue
        if path.name in _SYNC_BLACKLIST_NAMES:
            continue
        if any(part in _SYNC_BLACKLIST_DIRS for part in path.parts):
            continue
        if (path.suffix == ".md"
            or path.name.endswith(".md.enc")
            or path.name == ".memoryd-sensitive"):
            yield path


def read_state(sync_dir: Path) -> dict:
    f = sync_dir / _STATE_FILENAME
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text("utf-8"))
    except Exception:
        log.warning("corrupt sync state; ignoring")
        return {}


def write_state(sync_dir: Path, state: dict) -> None:
    sync_dir.mkdir(parents=True, exist_ok=True)
    (sync_dir / _STATE_FILENAME).write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True),
        "utf-8",
    )


def relative_key(data_root: Path, path: Path) -> str:
    """Stable key for state manifest: scope_hash/type/slug.ext."""
    return str(path.relative_to(data_root / "scopes")).replace("\\", "/")


@dataclass
class ExportReport:
    copied: int = 0
    skipped: int = 0
    dry_run: bool = False
    files: list[str] = field(default_factory=list)


def export(
    data_root: Path,
    sync_dir: Path,
    *,
    scope_hash: str | None = None,
    dry_run: bool = False,
) -> ExportReport:
    """Mirror local markdown to sync dir; incremental via fingerprint state."""
    state = read_state(sync_dir)
    new_state = dict(state)
    report = ExportReport(dry_run=dry_run)
    for src in iter_local_markdown(data_root):
        key = relative_key(data_root, src)
        if scope_hash and not key.startswith(scope_hash + "/"):
            continue
        fp = _fingerprint(src)
        if state.get(key) == fp:
            report.skipped += 1
            continue
        dst = sync_dir / "scopes" / key
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
        new_state[key] = fp
        report.copied += 1
        report.files.append(key)
    if not dry_run:
        write_state(sync_dir, new_state)
    return report


def _fingerprint(path: Path) -> str:
    """sha256 of file bytes; cheap, deterministic, no SQLite round-trip needed."""
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


@dataclass
class ImportReport:
    copied: int = 0
    skipped: int = 0
    conflicts: int = 0
    dry_run: bool = False


def import_(
    data_root: Path,
    sync_dir: Path,
    *,
    scope_hash: str | None = None,
    dry_run: bool = False,
) -> ImportReport:
    """Pull from sync dir to local; resolve conflicts into _conflicts/<slug>-<fp8>.md."""
    report = ImportReport(dry_run=dry_run)
    sync_scopes = sync_dir / "scopes"
    if not sync_scopes.exists():
        return report
    for src in sync_scopes.rglob("*"):
        if not src.is_file():
            continue
        if src.name in _SYNC_BLACKLIST_NAMES:
            continue
        if not (src.suffix == ".md"
                or src.name.endswith(".md.enc")
                or src.name == ".memoryd-sensitive"):
            continue
        rel = src.relative_to(sync_scopes)
        key = str(rel).replace("\\", "/")
        if scope_hash and not key.startswith(scope_hash + "/"):
            continue
        local = data_root / "scopes" / rel
        if not local.exists():
            if not dry_run:
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(src.read_bytes())
            report.copied += 1
            continue
        if _fingerprint(local) == _fingerprint(src):
            report.skipped += 1
            continue
        # conflict
        if not dry_run:
            local_fp = _fingerprint(local)[:8]
            conflicts_dir = data_root / "scopes" / "_conflicts"
            conflicts_dir.mkdir(parents=True, exist_ok=True)
            backup = conflicts_dir / f"{rel.name}-{local_fp}"
            backup.write_bytes(local.read_bytes())
            local.write_bytes(src.read_bytes())
        report.conflicts += 1
    if not dry_run and (report.copied > 0 or report.conflicts > 0):
        _rebuild_index_quiet(data_root)
    return report


def _rebuild_index_quiet(data_root: Path) -> None:
    """Best-effort rebuild_index; never raise.

    Wraps `memoryd.index.rebuild_index` so post-import sync stays robust even
    if SQLite is transiently locked or the migrations directory disappears.
    """
    try:
        from ..index import rebuild_index
        rebuild_index(data_root)
    except Exception as e:
        log.warning("post-import rebuild_index failed: %s", e)


def status(data_root: Path, sync_dir: Path) -> dict:
    """Return per-scope counts (local vs sync) plus _conflicts tally."""
    state = read_state(sync_dir)
    per_scope: dict[str, dict[str, int]] = {}
    for p in iter_local_markdown(data_root):
        parts = p.relative_to(data_root / "scopes").parts
        if not parts:
            continue
        h = parts[0]
        if h == "_conflicts":
            continue
        per_scope.setdefault(h, {"local": 0, "sync": 0})["local"] += 1
    sync_scopes = sync_dir / "scopes"
    if sync_scopes.exists():
        for p in sync_scopes.rglob("*"):
            if not p.is_file():
                continue
            if p.name == _STATE_FILENAME:
                continue
            if p.name in _SYNC_BLACKLIST_NAMES:
                continue
            if any(part in _SYNC_BLACKLIST_DIRS for part in p.parts):
                continue
            if not (p.suffix == ".md"
                    or p.name.endswith(".md.enc")
                    or p.name == ".memoryd-sensitive"):
                continue
            parts = p.relative_to(sync_scopes).parts
            if not parts:
                continue
            h = parts[0]
            if h == "_conflicts":
                continue
            per_scope.setdefault(h, {"local": 0, "sync": 0})["sync"] += 1
    conflicts = 0
    cdir = data_root / "scopes" / "_conflicts"
    if cdir.exists():
        conflicts = sum(1 for x in cdir.iterdir() if x.is_file())
    return {
        "sync_dir": str(sync_dir),
        "state_entries": len(state),
        "per_scope": per_scope,
        "conflicts": conflicts,
    }


# ---------------------------------------------------------------------------
# Path B re-exports (memories.json bundle).  Imported lazily-as-attributes
# so ``monkeypatch.setattr("memoryd.sync.X", ...)`` patterns keep working
# for both path-A and path-B callers.
# ---------------------------------------------------------------------------

from .conflict import merge_memory_fields  # noqa: E402
from .memories_json import (  # noqa: E402
    EXPORTER_VERSION,
    diff_with_remote,
    export_to_memories_json,
    import_from_memories_json,
)
from .schema import (  # noqa: E402
    AuditEntry,
    EntityEntry,
    ExportMetadata,
    MemoriesExport,
    MemoryEntry,
    RelationEntry,
)

__all__ = [
    # path A (legacy markdown mirror)
    "expand_sync_dir",
    "iter_local_markdown",
    "read_state",
    "write_state",
    "relative_key",
    "export",
    "import_",
    "status",
    "ExportReport",
    "ImportReport",
    # path B (memories.json bundle)
    "EXPORTER_VERSION",
    "export_to_memories_json",
    "import_from_memories_json",
    "diff_with_remote",
    "merge_memory_fields",
    "MemoriesExport",
    "MemoryEntry",
    "EntityEntry",
    "RelationEntry",
    "AuditEntry",
    "ExportMetadata",
]
