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


def _grey_zone_preview(
    data_root: Path,
    limit: int = 5,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` pending promotions, lowest DURA-avg first.

    Used by SessionStart inject when there are few enough pending entries to
    show each one inline (so the agent / user can act without leaving the
    conversation). Returns [] on any error — inject must never fail.

    Passes the caller's existing sqlite connection through when provided to
    avoid opening a second connection every SessionStart (inject's main
    ``render_session_context`` already has a live ``conn``).
    """
    db = data_root / "index.db"
    import json as _json
    own_conn = False
    try:
        if conn is None:
            if not db.exists():
                return []
            conn = sqlite3.connect(str(db))
            own_conn = True
        # Cap the SQL pull — backlogs of 100+ rows pull every row each
        # SessionStart without this. We sort by DURA in Python after pulling,
        # so a 200-row cap is a safety net (we only need the lowest 5).
        rows = conn.execute(
            "SELECT id, proposed_type, proposed_title, dura_score "
            "FROM promotions WHERE status='pending' "
            "ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    except Exception:  # noqa: BLE001 — best-effort, inject must not raise
        return []
    finally:
        if own_conn and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    enriched: list[dict[str, Any]] = []
    for row in rows:
        try:
            dura = _json.loads(row[3] or "{}")
        except (TypeError, _json.JSONDecodeError):
            dura = {}
        vals = [float(v) for v in dura.values() if isinstance(v, (int, float))]
        avg = round(sum(vals) / len(vals), 3) if vals else 0.0
        enriched.append({
            "id": row[0],
            "type": row[1],
            "title": row[2],
            "dura_avg": avg,
        })
    # Lowest avg first — that's where user judgment matters most.
    enriched.sort(key=lambda r: r["dura_avg"])
    return enriched[:limit]


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


def _local_handoff_block(cwd: Path | None, max_chars: int) -> str:
    """If cwd has a HANDOFF.md, render it as a quoted block. Returns "" if absent.

    HANDOFF.md is project-scoped (lives in the repo root), so it complements
    the user-scoped identity / entities snippet rendered elsewhere. Surfacing
    it in SessionStart means new sessions land with "what this project is at"
    context without the user having to paste it.
    """
    if cwd is None:
        return ""
    try:
        from .handoff import read_local_handoff
        text = read_local_handoff(cwd, max_chars=max_chars)
    except Exception:  # noqa: BLE001 — inject must never raise
        return ""
    if not text:
        return ""
    return _quote_block(text.rstrip())


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
    cwd: Path | None = None,
    handoff_max_chars: int = 3000,
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
    cwd
        Project directory used to surface the local ``HANDOFF.md`` (if any).
        ``None`` (default) auto-detects via ``CLAUDE_PROJECT_DIR`` env or
        the process cwd, so SessionStart hooks don't have to plumb it.
    handoff_max_chars
        Cap on the HANDOFF.md excerpt rendered inline. Default 3000.

    Returns
    -------
    str
        Markdown text suitable for ``additionalContext``. **Never
        raises** — on total failure returns the single line
        :data:`_EMPTY_FALLBACK`.
    """
    types = recent_memory_types if recent_memory_types is not None else list(_DEFAULT_RECENT_TYPES)
    root = data_root or _data_root()

    # Auto-detect project cwd from CC env if caller didn't pass it.
    # The CLI handler (cmd_inject) always passes ``cwd`` explicitly; this
    # block is the safety net for direct programmatic callers (tests, MCP
    # tools, future integrations) that don't resolve cwd themselves.
    if cwd is None:
        env_cwd = os.environ.get("CLAUDE_PROJECT_DIR")
        if env_cwd:
            try:
                cwd = Path(env_cwd).resolve()
            except OSError:
                cwd = None
        else:
            try:
                cwd = Path.cwd().resolve()
            except OSError:
                cwd = None

    try:
        identity = _read_identity_snippet(identity_max_chars)
        conn = _open_index_conn(root)

        top_entities: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        trends_line = ""
        pending_count = 0

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
                # Pending promotion backlog — surface when ≥ 5 so user
                # notices in-context. Silent when 0..4 to avoid noise.
                # We also grab the preview rows while the shared conn is
                # still open, so the helper doesn't have to reopen sqlite
                # below.
                pending_preview: list[dict[str, Any]] = []
                try:
                    pending_count = conn.execute(
                        "SELECT COUNT(*) FROM promotions WHERE status='pending'"
                    ).fetchone()[0]
                    if 1 <= pending_count <= 8:
                        pending_preview = _grey_zone_preview(root, limit=5, conn=conn)
                except Exception:  # noqa: BLE001 — table may not exist
                    pending_count = 0
            finally:
                conn.close()
        else:
            pending_preview = []

        handoff_block = _local_handoff_block(cwd, handoff_max_chars)

        if (
            not identity
            and not top_entities
            and not recent
            and not trends_line
            and pending_count == 0
            and not handoff_block
        ):
            return _EMPTY_FALLBACK

        parts: list[str] = ["## 与 abble 的最近上下文", ""]

        if handoff_block:
            parts.append("**本项目 HANDOFF.md**（项目根的交接快照，优先读这个）：")
            parts.append("")
            parts.append(handoff_block)
            parts.append("")

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

        if pending_count >= 1:
            # 灰区少（≤8）时直接展开预览，CC 可以一句话帮你过；
            # 灰区多时只提示数字 + 引导，避免淹没 inject 段。
            # preview_rows 在上面 conn 还活着时已经查过了，复用避免重开 sqlite。
            preview_rows = pending_preview
            if preview_rows:
                parts.append(
                    f"**待审批记忆 {pending_count} 条**（DURA 评分通过等你判断）："
                )
                for r in preview_rows:
                    avg = r.get("dura_avg", 0.0)
                    title = (r.get("title") or "")[:60]
                    parts.append(f"- `#{r['id']}` [{r['type']}] avg={avg:.2f} {title}")
                parts.append(
                    "→ 对我说「过一遍 pending」我用 mem_review_pending 列详情、"
                    "「批 #X #Y」/「拒 #Z」逐个处理；"
                    "或「批所有高分」一句话过完"
                )
            else:
                parts.append(
                    f"**有 {pending_count} 条待审批记忆**（DURA 评分通过等你过最后一关）—— "
                    "对我说「过一遍 pending」我帮你列出来逐个处理；"
                    "或「批所有高分」直接批 DURA≥0.85 的"
                )
            parts.append("")

        return "\n".join(parts).rstrip() + "\n"

    except Exception:
        # Hooks must be graceful. Any unexpected failure → fallback line.
        return _EMPTY_FALLBACK


__all__ = ["render_session_context"]
