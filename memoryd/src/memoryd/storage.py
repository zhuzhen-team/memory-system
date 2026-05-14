"""Markdown file storage for memory entries.

Plan 1: `save_session` writes to `<root>/scopes/<hash>/sessions/<slug>.md`.
Plan 3: `save_memory` is the generic helper that routes any of the 6 types
        to its own subdirectory (decisions/ preferences/ facts/ playbooks/
        warnings/ — sessions/ stays where Plan 1 put it). save_session is
        kept as backwards-compat shim → save_memory.

Both helpers also call Index.index_memory so the SQLite index stays in
sync with disk. Index opens lazily and is closed per call.
"""
from __future__ import annotations

import re
from pathlib import Path

from .index import open_index
from .schema import SessionMemory


_TYPE_TO_DIR = {
    "session": "sessions",
    "decision": "decisions",
    "preference": "preferences",
    "fact": "facts",
    "playbook": "playbooks",
    "warning": "warnings",
}


_SAFE_SLUG = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_slug(slug: str) -> None:
    if not _SAFE_SLUG.match(slug):
        raise ValueError(f"unsafe slug: {slug!r}")
    if ".." in slug:
        raise ValueError(f"slug contains ..: {slug!r}")


def _type_dir(root: Path, scope_hash: str, type_: str) -> Path:
    subdir = _TYPE_TO_DIR.get(type_)
    if subdir is None:
        raise ValueError(f"unknown memory type: {type_!r}")
    return root / "scopes" / scope_hash / subdir


def save_memory(root: Path, mem: SessionMemory) -> Path:
    """Write a memory to its type-specific subdirectory and index it."""
    _validate_slug(mem.frontmatter.slug)
    target_dir = _type_dir(root, mem.frontmatter.scope_hash, mem.frontmatter.type)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{mem.frontmatter.slug}.md"
    path.write_text(mem.to_markdown(), encoding="utf-8")

    # Update SQLite index. body_path is relative to data root for portability.
    body_rel = str(path.relative_to(root))
    idx = open_index(root / "index.db")
    try:
        idx.index_memory(mem, body_path=body_rel)
    finally:
        idx.close()
    return path


def save_session(root: Path, session: SessionMemory) -> Path:
    """Plan 1 entry; now routes through save_memory."""
    return save_memory(root, session)


def load_session(path: Path) -> SessionMemory:
    text = path.read_text(encoding="utf-8")
    return SessionMemory.from_markdown(text)


def list_sessions(root: Path, scope_hash: str) -> list[Path]:
    """List session `.md` files for a scope (Plan 1 API kept verbatim)."""
    scope_dir = root / "scopes" / scope_hash / "sessions"
    if not scope_dir.exists():
        return []
    return sorted(scope_dir.glob("*.md"))


def list_by_type(root: Path, scope_hash: str, type_: str) -> list[Path]:
    """List `.md` files of any single type for a scope."""
    d = _type_dir(root, scope_hash, type_)
    if not d.exists():
        return []
    return sorted(d.glob("*.md"))


# Backwards-compat: callers in mirror_codex.py / mirror_openclaw.py use
# this private helper to find scope dirs; keep export.
def _scope_dir(root: Path, scope_hash: str) -> Path:
    return root / "scopes" / scope_hash / "sessions"


# Backwards-compat alias used by search.py (Plan 1 name).
def _sessions_dir(root: Path, scope_hash: str) -> Path:
    return root / "scopes" / scope_hash / "sessions"
