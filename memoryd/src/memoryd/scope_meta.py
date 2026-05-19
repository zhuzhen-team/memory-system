"""Sensitive scope marker management.

`.memoryd-sensitive` is a plain text file at the scope root that signals
"this directory tree is sensitive". Children inherit unconditionally.
"""
from __future__ import annotations

from pathlib import Path


MARKER_FILENAME = ".memoryd-sensitive"


def find_sensitive_root(path: Path) -> Path | None:
    """Walk parents from `path`; return the first directory that contains
    `.memoryd-sensitive`. None if no ancestor is sensitive."""
    cur = Path(path).resolve()
    for ancestor in [cur, *cur.parents]:
        if (ancestor / MARKER_FILENAME).exists():
            return ancestor
    return None


def is_path_sensitive(path: Path) -> bool:
    return find_sensitive_root(path) is not None


def mark_sensitive(scope_root: Path) -> Path:
    """Create .memoryd-sensitive at scope_root. Errors if a parent is already sensitive."""
    scope_root = scope_root.resolve()
    existing = find_sensitive_root(scope_root)
    if existing is not None and existing != scope_root:
        raise ValueError(f"parent already sensitive: {existing}")
    marker = scope_root / MARKER_FILENAME
    marker.write_text(f"scope_root: {scope_root}\n", encoding="utf-8")
    return marker


def unmark_sensitive(scope_root: Path) -> None:
    """Remove .memoryd-sensitive. No-op if not present."""
    marker = Path(scope_root).resolve() / MARKER_FILENAME
    if marker.exists():
        marker.unlink()
