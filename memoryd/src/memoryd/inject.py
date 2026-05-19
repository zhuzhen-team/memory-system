"""SessionStart context injection — render a small markdown snippet that
gives an AI client a one-glance picture of the user before the first
turn.

This is the read-side counterpart to ``capture`` / ``analyze-session``:
the latter writes memories; this reads the most useful subset
(identity.md + top entities + recent long-term) and renders it as a
short markdown block suitable for piping into CC's SessionStart
``additionalContext`` (or any other client that can inject prompt
fragments).

Contract:
- Pure read; no LLM, no writes.
- ``render_session_context`` must NEVER raise — hooks must stay
  graceful. On total failure it returns a single-line fallback so the
  hook can still emit something non-empty if desired.
- Sensitive scopes are silently skipped — they never leak into the
  global picture.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_DEFAULT_RECENT_TYPES: tuple[str, ...] = ("decision", "preference", "fact")
_EMPTY_FALLBACK = "_(memoryd 未启用 / 数据为空)_"


def _data_root() -> Path:
    """Mirror cli._data_root so this module can be imported standalone."""
    override = os.environ.get("MEMORYD_DATA_ROOT")
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "memoryd"


def _open_index_conn(data_root: Path) -> sqlite3.Connection | None:
    """Open the SQLite index with row factory set, or None if missing."""
    db = data_root / "index.db"
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.DatabaseError:
        return None


def _read_identity_snippet(max_chars: int) -> str:
    """Best-effort wrapper around :func:`profile.identity.read_current_identity`."""
    try:
        from .profile.identity import read_current_identity
    except Exception:
        return ""
    try:
        text = read_current_identity(max_chars=max_chars)
    except Exception:
        return ""
    return (text or "").strip()


def _quote_block(text: str) -> str:
    """Convert a multi-line block into a Markdown blockquote (`> ...`)."""
    if not text:
        return ""
    out: list[str] = []
    for line in text.splitlines():
        if line.strip():
            out.append(f"> {line.rstrip()}")
        else:
            out.append(">")
    return "\n".join(out)


def _is_scope_sensitive(conn: sqlite3.Connection, scope_hash: str | None) -> bool:
    """Cheap lookup into sensitive_scopes table; missing table → False."""
    if not scope_hash:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM sensitive_scopes WHERE scope_hash = ?",
            (scope_hash,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _top_entities_rows(
    conn: sqlite3.Connection,
    *,
    scope: str | None,
    window_days: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Pull top entities for the window, skipping sensitive-scope rows.

    Uses a direct SELECT (rather than going through KnowledgeGraphStore)
    so this helper stays cheap and tolerant of missing tables.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        sql = (
            "SELECT name, type, mention_count, scope_hash, last_seen_at "
            "FROM entities WHERE last_seen_at >= ?"
        )
        args: list[Any] = [cutoff]
        if scope is not None:
            sql += " AND scope_hash = ?"
            args.append(scope)
        sql += " ORDER BY mention_count DESC, last_seen_at DESC LIMIT ?"
        args.append(max(limit * 2, limit))  # over-fetch so sensitive filter doesn't starve
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []

    filtered: list[dict[str, Any]] = []
    for r in rows:
        if _is_scope_sensitive(conn, r.get("scope_hash")):
            continue
        filtered.append(r)
        if len(filtered) >= limit:
            break
    return filtered


def _recent_memories_rows(
    conn: sqlite3.Connection,
    *,
    scope: str | None,
    types: list[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Pull recent long-term memory titles (decision/preference/fact by default)."""
    if not types:
        return []
    try:
        placeholders = ",".join("?" for _ in types)
        sql = (
            "SELECT slug, type, title, scope_hash, created_at FROM memories "
            f"WHERE type IN ({placeholders}) "
            "AND decay_state != 'soft-forgotten' "
            "AND COALESCE(scope_sensitive, 0) = 0"
        )
        args: list[Any] = list(types)
        if scope is not None:
            sql += " AND scope_hash = ?"
            args.append(scope)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []


def _format_date(value: Any) -> str:
    """Render an ISO timestamp as ``YYYY-MM-DD``; return empty string on parse failure."""
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    s = str(value)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, TypeError):
        return s[:10]


def _render_trends_block(conn: sqlite3.Connection, *, window_days: int) -> str:
    """Render a 1-line "top triggers" summary if trigger_stats has data."""
    try:
        from .profile.trends import top_triggers
    except Exception:
        return ""
    try:
        rows = top_triggers(conn, window_days=window_days, limit=8)
    except Exception:
        return ""
    if not rows:
        return ""
    parts = [f"{trig} ({hits})" for trig, hits in rows]
    return "**最近 trigger**：" + " · ".join(parts)


def render_session_context(
    *,
    scope: str | None = None,
    identity_max_chars: int = 500,
    top_entities_window_days: int = 30,
    top_entities_limit: int = 8,
    recent_memories_limit: int = 5,
    recent_memory_types: list[str] | None = None,
    include_trends: bool = False,
    data_root: Path | None = None,
) -> str:
    """Render the SessionStart context block as Markdown.

    Parameters
    ----------
    scope
        If set, restricts entity / memory queries to this ``scope_hash``.
        ``None`` (default) aggregates across all scopes — the global
        picture; sensitive scopes are always skipped.
    identity_max_chars
        Hard cap on the identity.md excerpt (paragraph-aware via
        :func:`profile.identity.read_current_identity`).
    top_entities_window_days, top_entities_limit
        Window + cap for the top-entities line.
    recent_memories_limit
        Cap on the "recent decisions / preferences" list.
    recent_memory_types
        Types to consider for the recent list. Defaults to
        ``["decision", "preference", "fact"]``.
    include_trends
        Append a top-triggers single-line block. Off by default to keep
        the context terse for SessionStart.
    data_root
        Override for the memoryd data root. ``None`` falls back to
        env var ``MEMORYD_DATA_ROOT`` or ``~/.local/share/memoryd``.

    Returns
    -------
    str
        Markdown text suitable for ``additionalContext``. **Never
        raises** — on total failure returns the single line
        :data:`_EMPTY_FALLBACK`.
    """
    types = recent_memory_types if recent_memory_types is not None else list(_DEFAULT_RECENT_TYPES)
    root = data_root or _data_root()

    try:
        identity = _read_identity_snippet(identity_max_chars)
        conn = _open_index_conn(root)

        top_entities: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        trends_line = ""

        if conn is not None:
            try:
                top_entities = _top_entities_rows(
                    conn,
                    scope=scope,
                    window_days=top_entities_window_days,
                    limit=top_entities_limit,
                )
                recent = _recent_memories_rows(
                    conn,
                    scope=scope,
                    types=types,
                    limit=recent_memories_limit,
                )
                if include_trends:
                    trends_line = _render_trends_block(
                        conn, window_days=top_entities_window_days
                    )
            finally:
                conn.close()

        if not identity and not top_entities and not recent and not trends_line:
            return _EMPTY_FALLBACK

        parts: list[str] = ["## 与 abble 的最近上下文", ""]

        if identity:
            parts.append("**画像摘要**（identity.md 节选）：")
            parts.append("")
            parts.append(_quote_block(identity))
            parts.append("")

        if top_entities:
            parts.append(f"**最近常提及的实体**（{top_entities_window_days} 天内）：")
            chips = [
                f"{r.get('name') or '?'} ({r.get('mention_count', 0)})"
                for r in top_entities
            ]
            parts.append("- " + " · ".join(chips))
            parts.append("")

        if recent:
            parts.append("**最近长期记忆**：")
            for r in recent:
                date_str = _format_date(r.get("created_at"))
                date_part = f"[{date_str}] " if date_str else ""
                type_part = r.get("type") or "?"
                title = r.get("title") or r.get("slug") or "?"
                parts.append(f"- {date_part}{type_part}: {title}")
            parts.append("")

        if trends_line:
            parts.append(trends_line)
            parts.append("")

        return "\n".join(parts).rstrip() + "\n"

    except Exception:
        # Hooks must be graceful. Any unexpected failure → fallback line.
        return _EMPTY_FALLBACK


__all__ = ["render_session_context"]
