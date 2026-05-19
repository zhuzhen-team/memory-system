"""Session lifecycle handlers (4 tools).

memoryd doesn't have a dedicated "sessions" table — instead each session is
a markdown file under ``scopes/<hash>/sessions/`` with type=``session``.
``mem_session_start`` returns a slug; ``mem_session_end`` writes the
summary back into the corresponding session memory.

``mem_capture_passive`` is the one path that **skips** working memory
entirely — used by harness mirrors (Codex transcript watcher, OpenClaw
session handler) when they want to drop a raw observation into long-term
storage without going through the DURA promotion gate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..schema import Frontmatter, SessionMemory
from ..storage import load_session, save_memory
from . import util


# --- mem_session_start -------------------------------------------------------


async def session_start(
    *,
    scope: str = "auto",
    source: str = "manual",
    title: str | None = None,
) -> dict[str, Any]:
    """Open a new session memory.

    Returns the slug — callers pass it back into ``mem_session_end`` /
    ``mem_session_summary``.
    """
    sh = util.derive_scope(scope)
    now = util.now_utc()
    label = title or f"session-{now.strftime('%H%M%S')}"
    slug = util.safe_slug(label)
    mem = SessionMemory(
        frontmatter=Frontmatter(
            title=label,
            slug=slug,
            type="session",
            scope_hash=sh,
            source=source,
            created_at=now,
            ttl_days=7,
        ),
        body=f"## Session started at {now.isoformat()}\n",
    )
    try:
        path = save_memory(util.data_root(), mem)
    except Exception as e:  # pragma: no cover
        return util.fail(f"session_start failed: {e}", code="storage_error")
    return util.ok(session_id=slug, scope_hash=sh, path=str(path), started_at=now.isoformat())


# --- mem_session_end ---------------------------------------------------------


async def session_end(session_id: str, summary: str = "") -> dict[str, Any]:
    """Append a summary block to a session and mark it closed.

    Idempotent — calling twice just appends a second summary section. The
    second call also overwrites ``updated_at`` so consumers can detect
    "this session is recent" without scanning the body.
    """
    if not session_id:
        return util.fail("session_id required", code="invalid_argument")
    root = util.data_root()
    conn = util.open_db()
    try:
        row = conn.execute(
            "SELECT body_path, scope_hash, type FROM memories WHERE slug = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return util.fail(f"session not found: {session_id}", code="not_found")
    if row["type"] != "session":
        return util.fail(
            f"memory {session_id} has type={row['type']!r}; not a session",
            code="invalid_argument",
        )
    path = root / row["body_path"]
    if not path.exists():
        return util.fail("session body missing on disk", code="not_found")
    try:
        mem = load_session(path, memory_root=root)
    except Exception as e:
        return util.fail(f"failed to load session: {e}", code="storage_error")

    now = util.now_utc()
    appended = mem.body.rstrip() + "\n\n"
    appended += f"## Session ended at {now.isoformat()}\n"
    if summary.strip():
        appended += f"\n{summary.strip()}\n"
    new_fm = mem.frontmatter.model_copy(update={"updated_at": now})
    closed = SessionMemory(frontmatter=new_fm, body=appended)
    try:
        save_memory(root, closed)
    except Exception as e:
        return util.fail(f"session_end save failed: {e}", code="storage_error")
    return util.ok(session_id=session_id, closed_at=now.isoformat())


# --- mem_session_summary -----------------------------------------------------


async def session_summary(session_id: str) -> dict[str, Any]:
    """Return the body of a session (raw markdown).

    Useful for SessionStart-style hooks that want to inject "what happened
    last time" into a new model context.
    """
    if not session_id:
        return util.fail("session_id required", code="invalid_argument")
    root = util.data_root()
    conn = util.open_db()
    try:
        row = conn.execute(
            "SELECT body_path, scope_hash, type, title, created_at, updated_at "
            "FROM memories WHERE slug = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return util.fail(f"session not found: {session_id}", code="not_found")
    path = root / row["body_path"]
    body = ""
    if path.exists():
        try:
            mem = load_session(path, memory_root=root)
            body = mem.body
        except Exception:
            try:
                body = path.read_text(encoding="utf-8")
            except OSError:
                body = ""
    return util.ok(
        session_id=session_id,
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        summary=body,
    )


# --- mem_capture_passive -----------------------------------------------------


async def capture_passive(
    content: str,
    source: str,
    *,
    scope: str = "auto",
    type: str = "fact",  # noqa: A002
    tags: list[str] | None = None,
    triggers: list[str] | None = None,
) -> dict[str, Any]:
    """Write a long-term memory directly, bypassing DURA + working-memory.

    Used by mirrors (codex / openclaw / claude-mem) when they want to
    persist an observation **as the agent says it** without LLM scoring.
    Therefore ``type`` defaults to ``fact`` — the most neutral long-term
    type — but accepts any of the long-term types.

    Sessions are explicitly disallowed: passive captures should not look
    like first-class chat sessions in the timeline.
    """
    if not content or not content.strip():
        return util.fail("content is empty", code="invalid_argument")
    if type not in util.long_term_types():
        return util.fail(
            f"capture_passive requires a long-term type, got {type!r}",
            code="invalid_argument",
            allowed=list(util.long_term_types()),
        )
    sh = util.derive_scope(scope)
    body = content.strip()
    first_line = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    title = first_line[:80] or f"passive-capture-from-{source}"

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
            source=source or "passive",
            created_at=now,
            # Long-term: TTL stays None (no auto-decay until governance touches it).
            ttl_days=None,
        ),
        body=body + ("\n" if not body.endswith("\n") else ""),
    )
    try:
        path = save_memory(util.data_root(), mem)
    except Exception as e:
        return util.fail(f"capture_passive failed: {e}", code="storage_error")
    return util.ok(memory_id=slug, scope_hash=sh, path=str(path), type=type)


__all__ = [
    "capture_passive",
    "session_end",
    "session_start",
    "session_summary",
]
