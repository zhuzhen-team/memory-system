"""Multi-device sync: raw .md mirror to user-configured sync dir."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import load_config

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
