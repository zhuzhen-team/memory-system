"""Scope = directory unit. One scope = one memory collection.

Resolution rule (per spec § 3):
1. Walk parents from given path; first dir containing `.git` wins.
2. If no `.git` ancestor, the given path itself is the scope root.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def resolve_scope_root(start: Path) -> Path:
    """Find scope root for `start`. Returns absolute resolved path."""
    cur = Path(start).resolve()
    for ancestor in [cur, *cur.parents]:
        if (ancestor / ".git").exists():
            return ancestor
    return cur


def scope_hash(path: str | Path) -> str:
    """Stable 12-char sha1 prefix of the given path after resolution.

    NOTE: does NOT call `resolve_scope_root` internally. Callers that want
    the hash of a scope root must pre-resolve, e.g.
    `scope_hash(resolve_scope_root(cwd))`. Passing different non-root paths
    yields different hashes — this is intentional but the caller's
    responsibility to align with the scope concept.
    """
    abs_path = str(Path(path).resolve())
    return hashlib.sha1(abs_path.encode("utf-8")).hexdigest()[:12]
