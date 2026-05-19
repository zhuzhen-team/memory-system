"""Memory CRUD + retrieval handlers (7 tools).

Each function is async to match fastmcp's preferred style, even though most
of the underlying disk/SQLite work is synchronous — keeps the call surface
uniform and lets us add real awaits later (e.g. parallel hybrid search)
without changing tool signatures.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..schema import Frontmatter, SessionMemory
from ..storage import load_session, save_memory
from . import util


# --- mem_save ----------------------------------------------------------------


async def save(
    *,
    content: str,
    type: str = "session",  # noqa: A002 - matches MCP tool param name
    scope: str = "auto",
    tags: list[str] | None = None,
    triggers: list[str] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Persist a new memory.

    The first non-empty line of ``content`` doubles as the title when one
    isn't supplied — keeps the agent-facing API minimal.
    """
    if not content or not content.strip():
        return util.fail("content is empty", code="invalid_argument")
    if type not in util.all_types():
        return util.fail(
            f"unknown memory type: {type!r}",
            code="invalid_argument",
            allowed=list(util.all_types()),
        )

    sh = util.derive_scope(scope)
    body = content.strip()
    # First non-empty line → title (capped); fallback to a generic label.
    if title is None:
        first_line = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
        title = first_line[:80] or "untitled memory"

    slug = util.safe_slug(title)
    now = util.now_utc()
    mem = SessionMemory(
        frontmatter=Frontmatter(
            title=title,
            slug=slug,
            type=type,
            scope_hash=sh,
            triggers=list(triggers or []),
            tags=list(tags or []),
            source="mcp",
            created_at=now,
            ttl_days=None if util.is_long_term(type) else 7,
        ),
        body=body + ("\n" if not body.endswith("\n") else ""),
    )
    try:
        path = save_memory(util.data_root(), mem)
    except Exception as e:  # pragma: no cover - filesystem level
        return util.fail(f"save failed: {e}", code="storage_error")
    return util.ok(memory_id=slug, scope_hash=sh, path=str(path), type=type)


# --- mem_update --------------------------------------------------------------


async def update(
    memory_id: str,
    *,
    content: str | None = None,
    tags: list[str] | None = None,
    triggers: list[str] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Rewrite a memory's body / tags / triggers in place.

    Loads the existing markdown, applies the patches, re-serializes, and
    re-indexes via ``save_memory`` (which is idempotent on slug). The
    ``updated_at`` field is bumped to now.
    """
    if not memory_id:
        return util.fail("memory_id required", code="invalid_argument")
    root = util.data_root()
    conn = util.open_db()
    try:
        row = conn.execute(
            "SELECT body_path, scope_hash FROM memories WHERE slug = ?", (memory_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return util.fail(f"memory not found: {memory_id}", code="not_found")
    path = root / row["body_path"]
    if not path.exists():
        return util.fail(f"memory body missing on disk: {memory_id}", code="not_found")
    try:
        mem = load_session(path, memory_root=root)
    except Exception as e:
        return util.fail(f"failed to load memory: {e}", code="storage_error")

    fm = mem.frontmatter
    new_body = content.strip() if content is not None else mem.body.rstrip()
    new_triggers = list(triggers) if triggers is not None else list(fm.triggers)
    new_tags = list(tags) if tags is not None else list(fm.tags)
    new_title = title if title is not None else fm.title

    fm = fm.model_copy(
        update={
            "title": new_title,
            "triggers": new_triggers,
            "tags": new_tags,
            "updated_at": util.now_utc(),
        }
    )
    updated = SessionMemory(frontmatter=fm, body=new_body + "\n")
    try:
        save_memory(root, updated)
    except Exception as e:
        return util.fail(f"update failed: {e}", code="storage_error")
    return util.ok(memory_id=memory_id, updated_at=fm.updated_at.isoformat())


# --- mem_delete --------------------------------------------------------------


async def delete(memory_id: str) -> dict[str, Any]:
    """Remove a memory from disk + SQLite. Idempotent."""
    if not memory_id:
        return util.fail("memory_id required", code="invalid_argument")
    root = util.data_root()
    conn = util.open_db()
    try:
        row = conn.execute(
            "SELECT body_path FROM memories WHERE slug = ?", (memory_id,)
        ).fetchone()
        if row is None:
            return util.fail(f"memory not found: {memory_id}", code="not_found")
        body_path = row["body_path"]
        conn.execute("DELETE FROM memories WHERE slug = ?", (memory_id,))
        conn.commit()
    finally:
        conn.close()
    p = root / body_path
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass
    return util.ok(memory_id=memory_id, deleted=True)


# --- mem_get -----------------------------------------------------------------


async def get(memory_id: str) -> dict[str, Any]:
    """Return the full memory: row + raw markdown body."""
    if not memory_id:
        return util.fail("memory_id required", code="invalid_argument")
    root = util.data_root()
    conn = util.open_db()
    try:
        row = conn.execute(
            "SELECT * FROM memories WHERE slug = ?", (memory_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return util.fail(f"memory not found: {memory_id}", code="not_found")
    row_dict = dict(row)
    path = root / row_dict["body_path"]
    body = ""
    frontmatter: dict[str, Any] = {}
    if path.exists():
        try:
            mem = load_session(path, memory_root=root)
            body = mem.body
            frontmatter = mem.frontmatter.model_dump(mode="json", exclude_none=True)
        except Exception:
            try:
                body = path.read_text(encoding="utf-8")
            except OSError:
                body = ""
    return util.ok(memory_id=memory_id, row=row_dict, frontmatter=frontmatter, body=body)


# --- mem_search --------------------------------------------------------------


async def search(
    query: str,
    *,
    scope: str = "auto",
    top_k: int = 10,
    types: list[str] | None = None,
    entity_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Hybrid (ripgrep + vector) search delegated to :mod:`memoryd.search.hybrid`.

    ``types`` is an optional post-filter on memory.type — applied after the
    hybrid retriever returns hits, because the underlying search index does
    not store type per chunk (only per memory).
    """
    if not query or not query.strip():
        return util.ok(hits=[])
    try:
        sh = util.derive_scope(scope)
    except ValueError as e:
        return util.fail(str(e), code="invalid_argument")

    try:
        from ..search.hybrid import hybrid_search
        results = hybrid_search(
            query=query,
            scope_hash=sh,
            top_k=max(1, int(top_k)),
            entity_ids=list(entity_ids or []),
            data_root=util.data_root(),
        )
    except Exception as e:
        return util.fail(f"search failed: {e}", code="search_error")

    type_filter: set[str] | None = set(types) if types else None
    hits: list[dict[str, Any]] = []
    if type_filter:
        # We need the memory.type for each hit → one round-trip SQLite query.
        conn = util.open_db()
        try:
            ids = list({r.memory_id for r in results})
            if ids:
                qs = ",".join("?" * len(ids))
                rows = conn.execute(
                    f"SELECT slug, type FROM memories WHERE slug IN ({qs})", ids
                ).fetchall()
                type_by_slug = {r["slug"]: r["type"] for r in rows}
            else:
                type_by_slug = {}
        finally:
            conn.close()
        for r in results:
            if type_by_slug.get(r.memory_id) in type_filter:
                hits.append(_hit_to_dict(r, type_by_slug.get(r.memory_id)))
    else:
        for r in results:
            hits.append(_hit_to_dict(r))
    return util.ok(scope_hash=sh, query=query, hits=hits)


def _hit_to_dict(r: Any, type_: str | None = None) -> dict[str, Any]:
    return {
        "memory_id": r.memory_id,
        "chunk_id": r.chunk_id,
        "score": float(r.score),
        "content": r.content,
        "source": r.source,
        "heading": r.heading,
        "start_line": r.start_line,
        "end_line": r.end_line,
        "type": type_,
        "metadata": dict(r.metadata or {}),
    }


# --- mem_context -------------------------------------------------------------


async def context(memory_id: str, neighbors: int = 3) -> dict[str, Any]:
    """Return the memory plus its temporally-adjacent neighbours in the same scope.

    Used by Claude Code / Codex on SessionStart to surface "the last few
    things you talked about in this project". Neighbours are siblings by
    ``created_at`` order, not by content similarity.
    """
    if not memory_id:
        return util.fail("memory_id required", code="invalid_argument")
    neighbors = max(0, min(int(neighbors), 20))
    conn = util.open_db()
    try:
        anchor = conn.execute(
            "SELECT slug, scope_hash, created_at, type FROM memories WHERE slug = ?",
            (memory_id,),
        ).fetchone()
        if anchor is None:
            return util.fail(f"memory not found: {memory_id}", code="not_found")
        sh = anchor["scope_hash"]
        created = anchor["created_at"]
        before = conn.execute(
            "SELECT slug, type, title, created_at FROM memories "
            "WHERE scope_hash = ? AND created_at < ? "
            "ORDER BY created_at DESC LIMIT ?",
            (sh, created, neighbors),
        ).fetchall()
        after = conn.execute(
            "SELECT slug, type, title, created_at FROM memories "
            "WHERE scope_hash = ? AND created_at > ? "
            "ORDER BY created_at ASC LIMIT ?",
            (sh, created, neighbors),
        ).fetchall()
    finally:
        conn.close()
    return util.ok(
        memory_id=memory_id,
        scope_hash=sh,
        before=[dict(r) for r in before],
        after=[dict(r) for r in after],
    )


# --- mem_timeline ------------------------------------------------------------


_TIMEWINDOW_RE = re.compile(r"^\s*(\d+)\s*([dwmy])\s*$", re.IGNORECASE)


def _parse_window(spec: str) -> timedelta:
    """Parse a duration string like ``30d`` / ``2w`` / ``6m`` / ``1y``.

    Falls back to 30 days on any parse error.
    """
    m = _TIMEWINDOW_RE.match(spec or "")
    if not m:
        return timedelta(days=30)
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "d":
        return timedelta(days=n)
    if unit == "w":
        return timedelta(weeks=n)
    if unit == "m":
        return timedelta(days=n * 30)
    if unit == "y":
        return timedelta(days=n * 365)
    return timedelta(days=30)


async def timeline(
    *,
    scope: str = "auto",
    since: str = "30d",
    types: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Chronologically ordered list of memories in a scope.

    Skips ``soft-forgotten`` entries — the timeline is for "recent activity"
    not "everything ever". Use ``mem_search`` for arbitrary recall.
    """
    try:
        sh = util.derive_scope(scope)
    except ValueError as e:
        return util.fail(str(e), code="invalid_argument")
    window = _parse_window(since)
    cutoff = (datetime.now(timezone.utc) - window).isoformat()
    type_list = list(types or [])
    conn = util.open_db()
    try:
        sql = (
            "SELECT slug, type, title, scope_hash, created_at, decay_state "
            "FROM memories WHERE scope_hash = ? AND created_at >= ? "
            "AND decay_state != 'soft-forgotten'"
        )
        args: list[Any] = [sh, cutoff]
        if type_list:
            sql += f" AND type IN ({','.join('?' * len(type_list))})"
            args.extend(type_list)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(int(limit), 500)))
        rows = conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError as e:
        return util.fail(f"index unavailable: {e}", code="index_error")
    finally:
        conn.close()
    return util.ok(scope_hash=sh, since=since, entries=[dict(r) for r in rows])


__all__ = [
    "context",
    "delete",
    "get",
    "save",
    "search",
    "timeline",
    "update",
]
