"""Search over memoryd memory files.

Plan 3 prefers SQLite + triggers + LIKE-on-body for speed and structured
filters; falls back to ripgrep when needed for full-text patterns the
SQLite path can't express. Every hit bumps recall_count via record_recall.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .index import open_index
from .schema import SessionMemory
from .storage import load_session


@dataclass(frozen=True)
class SearchHit:
    path: Path
    title: str
    slug: str
    triggers: list[str]
    excerpt: str


def _hit_from_row(row: dict[str, Any], memory_root: Path, excerpt: str) -> SearchHit:
    return SearchHit(
        path=memory_root / row["body_path"],
        title=row["title"],
        slug=row["slug"],
        triggers=[],
        excerpt=excerpt,
    )


def search_sessions(
    root: Path,
    scope_hash: str,
    query: str,
    *,
    type_: str | None = None,
    include_decayed: bool = False,
    limit: int = 20,
) -> list[SearchHit]:
    """Search memories in a scope for `query` (case-insensitive substring).

    type_=None → all six types; otherwise restricts to that type.
    include_decayed=False → excludes soft-forgotten rows.
    Bumps recall_count on every hit via record_recall.
    """
    idx = open_index(root / "index.db")
    try:
        # First try trigger match (cheap, structured)
        sql_t = (
            "SELECT m.* FROM memories m JOIN triggers t ON m.slug = t.slug "
            "WHERE m.scope_hash = ? AND LOWER(t.trigger) LIKE LOWER(?)"
        )
        args: list[Any] = [scope_hash, f"%{query}%"]
        if type_ is not None:
            sql_t += " AND m.type = ?"
            args.append(type_)
        if not include_decayed:
            sql_t += " AND m.decay_state != 'soft-forgotten'"
        sql_t += " GROUP BY m.slug ORDER BY m.created_at DESC LIMIT ?"
        args.append(limit)
        trigger_rows = idx.conn.execute(sql_t, args).fetchall()

        # Then full-text on body via reading body_path (small per file)
        sql_a = "SELECT * FROM memories WHERE scope_hash = ?"
        a_args: list[Any] = [scope_hash]
        if type_ is not None:
            sql_a += " AND type = ?"
            a_args.append(type_)
        if not include_decayed:
            sql_a += " AND decay_state != 'soft-forgotten'"
        sql_a += " ORDER BY created_at DESC"
        all_rows = idx.conn.execute(sql_a, a_args).fetchall()

        seen: set[str] = set()
        hits: list[SearchHit] = []
        for row in trigger_rows:
            d = dict(row)
            if d["slug"] in seen:
                continue
            seen.add(d["slug"])
            excerpt = _excerpt_for(root / d["body_path"], query)
            hits.append(_hit_from_row(d, root, excerpt))

        for row in all_rows:
            d = dict(row)
            if d["slug"] in seen:
                continue
            md_path = root / d["body_path"]
            if not md_path.exists():
                continue
            text = md_path.read_text(encoding="utf-8", errors="replace")
            if query.lower() in text.lower():
                seen.add(d["slug"])
                excerpt = _excerpt_for(md_path, query)
                hits.append(_hit_from_row(d, root, excerpt))
            if len(hits) >= limit:
                break

        # Bump recall_count for each hit
        for h in hits:
            idx.record_recall(h.slug)
        return hits[:limit]
    finally:
        idx.close()


def _excerpt_for(md_path: Path, query: str) -> str:
    """Find the first line containing `query` in the .md; <= 200 chars."""
    try:
        for line in md_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if query.lower() in line.lower():
                return line[:200]
    except OSError:
        pass
    return ""
