"""One-shot data bundle / restore for device migration + safe backup.

Goal: one command produces a single tar.gz containing everything a new
machine needs to "be the same memoryd user" — scopes, profile, index, audit
chain — without dragging along the venv, OS keychain, or any host-specific
launchd / hook state.

Pairs with ``memoryd setup auto-install`` on the receiving side: restore
the bundle to reseat the data, then run auto-install to wire CC / Codex /
cron / MCP / launchd back up on the new machine.

Design choices:
  * Default output goes to ``~/Desktop/`` with a timestamp so multiple
    snapshots don't clobber each other.
  * ``.md.enc`` files (encrypted sensitive scopes) are EXCLUDED by default
    because their encryption keys live in the OS keychain — moving them
    across machines is useless without ``set-passphrase`` mode being on.
    Override via ``--include-encrypted``.
  * The SQLite ``index.db`` is bundled — restore can either use it directly
    or call ``memoryd rebuild-index`` to regenerate from the markdown.
"""
from __future__ import annotations

import os
import sys
import tarfile
from datetime import datetime
from pathlib import Path
from typing import NamedTuple


class BundleStats(NamedTuple):
    """Counts surfaced to the user after a successful bundle / restore."""
    files_total: int
    scopes_md: int
    profile_files: int
    has_index_db: bool
    has_audit_log: bool
    encrypted_skipped: int
    output_path: Path
    size_bytes: int


def _data_root() -> Path:
    override = os.environ.get("MEMORYD_DATA_ROOT")
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "memoryd"


def _default_bundle_path() -> Path:
    """Timestamped path on ~/Desktop so each snapshot has a unique name."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        # Headless / Linux without Desktop — fall back to cwd
        desktop = Path.cwd()
    return desktop / f"memoryd-snapshot-{ts}.tar.gz"


def bundle(
    *,
    out: Path | None = None,
    include_encrypted: bool = False,
    data_root: Path | None = None,
) -> BundleStats:
    """Pack the data dir into a single ``tar.gz`` for migration / backup.

    What goes in:
      * ``scopes/<hash>/**/*.md``           — all plaintext markdown
      * ``scopes/<hash>/**/*.md.enc``        — only if include_encrypted=True
      * ``scopes/<hash>/.scope-name``        — scope label, if present
      * ``profile/identity.md``             — current identity
      * ``profile/identity.md.history/*``   — version history
      * ``profile/change-reports/*``        — monthly evolution reports
      * ``index.db``                         — sqlite index (restore can use as-is or rebuild)
      * ``audit.log``                        — audit chain

    What stays behind:
      * ``logs/``                            — per-host noise
      * ``keyring`` / OS Keychain entries    — sensitive, not portable
      * ``.venv``                            — recreated by `uv pip install`
      * cron / launchd / hook state          — restored by auto-install on the new host
    """
    root = data_root or _data_root()
    if not root.exists():
        raise FileNotFoundError(
            f"no memoryd data at {root}; nothing to bundle"
        )

    target = out or _default_bundle_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    scopes_md = 0
    profile_files = 0
    has_index_db = False
    has_audit_log = False
    encrypted_skipped = 0
    files_total = 0

    def _arcname(p: Path) -> str:
        return str(p.relative_to(root))

    with tarfile.open(target, "w:gz", compresslevel=6) as tar:
        # scopes
        scopes_dir = root / "scopes"
        if scopes_dir.exists():
            for path in sorted(scopes_dir.rglob("*")):
                if path.is_dir():
                    continue
                if path.name.endswith(".md.enc"):
                    if not include_encrypted:
                        encrypted_skipped += 1
                        continue
                    tar.add(path, arcname=_arcname(path))
                    files_total += 1
                    continue
                if path.suffix == ".md" or path.name in (
                    ".scope-name", ".memoryd-sensitive",
                ):
                    tar.add(path, arcname=_arcname(path))
                    files_total += 1
                    scopes_md += 1 if path.suffix == ".md" else 0
        # profile
        profile_dir = root / "profile"
        if profile_dir.exists():
            for path in sorted(profile_dir.rglob("*")):
                if path.is_file():
                    tar.add(path, arcname=_arcname(path))
                    files_total += 1
                    profile_files += 1
        # index.db + audit chain (best-effort; missing is fine)
        index_db = root / "index.db"
        if index_db.exists():
            tar.add(index_db, arcname="index.db")
            has_index_db = True
            files_total += 1
        # Audit chain lives at root/audit/audit.jsonl per
        # governance.audit.audit_log_path(). Older versions of this bundle
        # function looked at root/audit.log which never existed on a real
        # install and silently dropped the chain on migration.
        audit_jsonl = root / "audit" / "audit.jsonl"
        if audit_jsonl.exists():
            tar.add(audit_jsonl, arcname="audit/audit.jsonl")
            has_audit_log = True
            files_total += 1

    size = target.stat().st_size
    return BundleStats(
        files_total=files_total,
        scopes_md=scopes_md,
        profile_files=profile_files,
        has_index_db=has_index_db,
        has_audit_log=has_audit_log,
        encrypted_skipped=encrypted_skipped,
        output_path=target,
        size_bytes=size,
    )


def restore(
    *,
    src: Path,
    data_root: Path | None = None,
    force: bool = False,
) -> BundleStats:
    """Inverse of :func:`bundle`. Extract everything into the data root.

    Refuses to overwrite an existing non-empty data root unless ``force=True``
    — the typical migration flow is "fresh machine → restore → auto-install",
    where the data root doesn't exist yet. ``force`` is for "I want to wipe
    and rehydrate from this snapshot" workflows.
    """
    root = data_root or _data_root()
    if not src.exists():
        raise FileNotFoundError(f"bundle file not found: {src}")
    if root.exists() and any(root.iterdir()) and not force:
        raise FileExistsError(
            f"data root {root} is not empty. "
            "Pass --force to overwrite (existing data will be replaced)."
        )
    root.mkdir(parents=True, exist_ok=True)

    files_total = 0
    scopes_md = 0
    profile_files = 0
    has_index_db = False
    has_audit_log = False

    with tarfile.open(src, "r:gz") as tar:
        # Safe-extract: refuse any member whose path escapes ``root``.
        # We use ``Path.relative_to`` (Python 3.9+) — string ``startswith``
        # is unsafe because ``root=/tmp/dst`` is a prefix of sibling
        # ``/tmp/dst2/x`` so member ``../dst2/x`` would slip through.
        root_resolved = root.resolve()
        members = tar.getmembers()
        for m in members:
            target = (root / m.name).resolve()
            try:
                target.relative_to(root_resolved)
            except ValueError as exc:
                raise ValueError(
                    f"refusing path-traversal member: {m.name!r} -> {target}"
                ) from exc
        tar.extractall(root, members=members)  # noqa: S202 — paths validated above

        for m in members:
            if m.isfile():
                files_total += 1
                if m.name.startswith("scopes/") and m.name.endswith(".md"):
                    scopes_md += 1
                elif m.name.startswith("profile/"):
                    profile_files += 1
                elif m.name == "index.db":
                    has_index_db = True
                elif m.name == "audit/audit.jsonl":
                    has_audit_log = True

    return BundleStats(
        files_total=files_total,
        scopes_md=scopes_md,
        profile_files=profile_files,
        has_index_db=has_index_db,
        has_audit_log=has_audit_log,
        encrypted_skipped=0,
        output_path=src,
        size_bytes=src.stat().st_size,
    )


def format_stats(stats: BundleStats, *, action: str) -> str:
    """Pretty-print bundle/restore stats for CLI output."""
    size_mb = stats.size_bytes / 1024 / 1024
    lines = [
        f"{action}: {stats.output_path}",
        f"  files: {stats.files_total}",
        f"  markdown: {stats.scopes_md}",
        f"  profile: {stats.profile_files}",
        f"  index.db: {'yes' if stats.has_index_db else 'no'}",
        f"  audit.log: {'yes' if stats.has_audit_log else 'no'}",
        f"  size: {size_mb:.2f} MB",
    ]
    if stats.encrypted_skipped:
        lines.append(
            f"  .md.enc skipped: {stats.encrypted_skipped} "
            "(re-run with --include-encrypted if you also moved keyring)"
        )
    return "\n".join(lines)


__all__ = ["BundleStats", "bundle", "restore", "format_stats"]
