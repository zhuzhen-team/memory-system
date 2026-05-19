"""Weekly LLM-driven rewrite of the user's ``identity.md`` profile.

Pipeline (called by cron every Mon 02:00):
    1. Collect signals from SQLite:
       - this week's new long-term entries (decision / preference / fact / playbook / warning)
       - long-term entries with ``recall_count >= 2`` (durable knowledge)
       - top-N entities from the last 30 days (best-effort via knowledge_graph)
       - supersede events from the last 14 days
       - top triggers from trigger_stats (last 7 days)
    2. Skip anything tagged ``scope_sensitive`` — sensitive scopes never
       leak into the global identity.
    3. Build an LLM prompt that includes the *previous* identity.md and
       asks for an *incremental patch-style rewrite* (LLM 看上一版 + 本周
       新增, 输出新版本; 不是全文重写).
    4. Persist the new identity.md to
       ``~/.local/share/memoryd/profile/identity.md`` (atomic write),
       snapshot the previous version to ``identity.md.history/<isoweek>.md``,
       store the unified diff + row in ``profile_versions``.

LLM calls are async. Pass a mock provider in tests; default uses the
prompt module ``memoryd.prompts.rewrite_identity`` (sub-agent B contract)
plus :func:`memoryd.llm.get_provider` for the actual completion call.
"""
from __future__ import annotations

import asyncio
import difflib
import inspect
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .store import ProfileStore, ProfileVersion


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _profile_dir() -> Path:
    """Return ``<data_root>/profile`` (creates parent on write)."""
    override = os.environ.get("MEMORYD_PROFILE_DIR")
    if override:
        return Path(override)
    data_root = os.environ.get("MEMORYD_DATA_ROOT")
    if data_root:
        return Path(data_root) / "profile"
    return Path.home() / ".local" / "share" / "memoryd" / "profile"


def identity_path() -> Path:
    return _profile_dir() / "identity.md"


def identity_history_dir() -> Path:
    return _profile_dir() / "identity.md.history"


def change_reports_dir() -> Path:
    return _profile_dir() / "change-reports"


# ---------------------------------------------------------------------------
# Public reader (SessionStart hook injection target)
# ---------------------------------------------------------------------------


def read_current_identity(*, max_chars: int = 2000) -> str:
    """Return the current ``identity.md`` content, truncated to ``max_chars``.

    Returns the empty string if no identity file exists yet. Truncation is
    paragraph-aware: we cut at the last blank-line boundary that fits.
    """
    p = identity_path()
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8")
    if len(text) <= max_chars:
        return text
    return _truncate_by_paragraph(text, max_chars)


def _truncate_by_paragraph(text: str, max_chars: int) -> str:
    """Truncate ``text`` at a paragraph boundary <= ``max_chars``.

    Falls back to a hard character cut if no paragraph break fits.
    """
    if len(text) <= max_chars:
        return text
    snippet = text[:max_chars]
    # Prefer the last double-newline split point.
    cut = snippet.rfind("\n\n")
    if cut > max_chars // 2:
        return snippet[:cut].rstrip() + "\n"
    # Fall back to last single-newline.
    cut = snippet.rfind("\n")
    if cut > 0:
        return snippet[:cut].rstrip() + "\n"
    return snippet


def _truncate_by_words(text: str, max_words: int) -> str:
    """Truncate ``text`` paragraph-by-paragraph until word budget is met.

    "Words" approximates CJK + ASCII by counting whitespace-split tokens
    plus character count for non-whitespace dense runs (so 800 ≈ a page
    of mixed Chinese/English).
    """
    paragraphs = text.split("\n\n")
    out: list[str] = []
    used = 0
    for para in paragraphs:
        wc = _count_words(para)
        if used + wc > max_words and out:
            break
        out.append(para)
        used += wc
        if used >= max_words:
            break
    return "\n\n".join(out).rstrip() + "\n"


def _count_words(s: str) -> int:
    """Rough word-count helper (works for mixed CJK + Latin)."""
    if not s:
        return 0
    parts = s.split()
    latin = sum(1 for p in parts if any(ord(c) < 128 for c in p))
    cjk = sum(1 for c in s if "一" <= c <= "鿿")
    return latin + cjk


# ---------------------------------------------------------------------------
# Signal collection (SQLite-side; no LLM)
# ---------------------------------------------------------------------------


_LONG_TERM_TYPES = ("decision", "preference", "fact", "playbook", "warning")


def _collect_signals(
    conn,
    *,
    window_days: int,
    now: datetime,
) -> dict[str, Any]:
    """Pull every input the LLM needs for a weekly rewrite, sensitive-aware.

    Returns a dict with ``new_long_term``, ``recurring``, ``top_entities``,
    ``recent_supersedes``, ``top_triggers``, plus ``window_start`` /
    ``window_end`` and a sources count.
    """
    window_start = now - timedelta(days=window_days)

    placeholders = ",".join("?" * len(_LONG_TERM_TYPES))
    new_long_term = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT slug, type, title, scope_hash, created_at, recall_count,
                   triggers_inline
            FROM (
                SELECT m.slug, m.type, m.title, m.scope_hash, m.created_at,
                       m.recall_count,
                       (SELECT GROUP_CONCAT(t.trigger, ',')
                          FROM triggers t WHERE t.slug = m.slug) AS triggers_inline
                FROM memories m
                WHERE m.type IN ({placeholders})
                  AND m.created_at >= ?
                  AND m.decay_state != 'soft-forgotten'
                  AND COALESCE(m.scope_sensitive, 0) = 0
                ORDER BY m.created_at DESC
                LIMIT 50
            )
            """,
            (*_LONG_TERM_TYPES, window_start.isoformat()),
        ).fetchall()
    ]

    recurring = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT slug, type, title, recall_count, scope_hash
            FROM memories
            WHERE type IN ({placeholders})
              AND recall_count >= 2
              AND decay_state != 'soft-forgotten'
              AND COALESCE(scope_sensitive, 0) = 0
            ORDER BY recall_count DESC, last_recalled_at DESC
            LIMIT 20
            """,
            _LONG_TERM_TYPES,
        ).fetchall()
    ]

    # Best-effort: supersede chain. Frontmatter stores supersedes as YAML
    # list (Plan 3), promotions table stores them as JSON. The SQLite
    # index keeps memories.slug + body_path; the chain itself lives in the
    # .md frontmatter. For the LLM prompt we just need *titles* / *slugs*
    # of recently-superseded ancestors. We approximate via promotions.
    recent_supersedes: list[dict[str, Any]] = []
    try:
        promo_rows = conn.execute(
            """
            SELECT proposed_title, proposed_supersedes, created_at, scope_hash
            FROM promotions
            WHERE status = 'approved'
              AND created_at >= ?
              AND proposed_supersedes != '[]'
            ORDER BY created_at DESC
            LIMIT 20
            """,
            ((now - timedelta(days=14)).isoformat(),),
        ).fetchall()
        for r in promo_rows:
            try:
                supersedes = json.loads(r[1])
            except (TypeError, json.JSONDecodeError):
                supersedes = []
            if not supersedes:
                continue
            recent_supersedes.append(
                {
                    "new_title": r[0],
                    "supersedes": supersedes,
                    "at": r[2],
                    "scope_hash": r[3],
                }
            )
    except Exception:
        recent_supersedes = []

    # Top entities — sub-agent C's job; if module/table absent fall back
    # to top trigger words from trigger_stats (also a reasonable signal).
    top_entities = _collect_top_entities(conn, now=now)

    # Top triggers always come from trigger_stats (cheap + no LLM).
    from .trends import top_triggers as _top_triggers

    top_trigs = _top_triggers(conn, window_days=window_days, now=now, limit=15)

    return {
        "window_start": window_start,
        "window_end": now,
        "new_long_term": new_long_term,
        "recurring": recurring,
        "top_entities": top_entities,
        "recent_supersedes": recent_supersedes,
        "top_triggers": top_trigs,
        "sources_count": len(new_long_term) + len(recurring),
    }


def _collect_top_entities(conn, *, now: datetime) -> list[dict[str, Any]]:
    """Pull recent top entities from the knowledge_graph module if it exists.

    Sub-agent C owns ``memoryd.knowledge_graph``. We import lazily and fall
    back to ``[]`` so this module stays runnable before that lands.
    """
    try:
        from .. import knowledge_graph  # type: ignore  # pragma: no cover
    except ImportError:
        return []

    fn = getattr(knowledge_graph, "top_entities", None)
    if fn is None:
        return []
    try:
        result = fn(conn, since=now - timedelta(days=30), limit=15)
    except Exception:
        return []
    if not isinstance(result, list):
        return []
    return [r if isinstance(r, dict) else {"name": str(r)} for r in result]


# ---------------------------------------------------------------------------
# Prompt assembly + LLM call
# ---------------------------------------------------------------------------


def _format_signals(signals: dict[str, Any]) -> str:
    """Render signals as a structured Markdown block for the LLM."""
    parts: list[str] = []
    parts.append(f"# 本周窗口")
    parts.append(
        f"{signals['window_start'].date().isoformat()} → "
        f"{signals['window_end'].date().isoformat()}"
    )
    parts.append("")

    parts.append("## 本周新增长期记忆")
    if signals["new_long_term"]:
        for m in signals["new_long_term"]:
            trigs = m.get("triggers_inline") or ""
            parts.append(
                f"- [{m['type']}] {m['title']}  (scope={m['scope_hash'][:8]}, "
                f"recall={m['recall_count']}, triggers={trigs})"
            )
    else:
        parts.append("(无)")

    parts.append("")
    parts.append("## 反复被召回的记忆 (recall_count >= 2)")
    if signals["recurring"]:
        for m in signals["recurring"]:
            parts.append(
                f"- [{m['type']}] {m['title']}  (×{m['recall_count']})"
            )
    else:
        parts.append("(无)")

    parts.append("")
    parts.append("## 近期被取代的旧观点 (supersedes)")
    if signals["recent_supersedes"]:
        for s in signals["recent_supersedes"]:
            old = ", ".join(s["supersedes"])
            parts.append(f"- {s['new_title']} <- supersedes [{old}]")
    else:
        parts.append("(无)")

    parts.append("")
    parts.append("## 近 30 天高频实体")
    if signals["top_entities"]:
        for e in signals["top_entities"]:
            name = e.get("name") or e.get("entity") or str(e)
            count = e.get("count") or e.get("hits") or ""
            parts.append(f"- {name}  {count}".rstrip())
    else:
        parts.append("(无)")

    parts.append("")
    parts.append("## 近 7 天 top triggers")
    if signals["top_triggers"]:
        for trig, hits in signals["top_triggers"]:
            parts.append(f"- {trig} ({hits})")
    else:
        parts.append("(无)")

    return "\n".join(parts)


def _build_default_prompt(
    prev_content: str,
    signals_md: str,
    *,
    max_words: int,
) -> tuple[str, str]:
    """Inline fallback prompt used when ``memoryd.prompts.rewrite_identity`` is absent.

    Sub-agent B owns the canonical template; this is just enough to keep
    the pipeline running standalone.
    """
    system = (
        "你是用户的画像维护助手。你的目标是基于上一版 identity.md 和本周新增信号，"
        "做**增量补丁式重写**：保留稳定的事实，补充新的偏好/决定/警示，删除已被 supersede 的旧观点。\n"
        "输出纯 Markdown（不要 fenced code block），主体不超过 "
        f"{max_words} 词。在正文末尾另起一段，写一行 `> change_summary: <一句中文概述本周变化>`。"
    )
    user = (
        "## 上一版 identity.md\n\n"
        f"{prev_content or '(尚无任何画像，请基于下方信号产出初版)'}\n\n"
        "## 本周信号\n\n"
        f"{signals_md}\n\n"
        "## 任务\n"
        "产出新版 identity.md（中文，Markdown）。"
    )
    return system, user


async def _maybe_await(value: Any) -> Any:
    """Allow LLM stubs to be sync or async callables."""
    if inspect.isawaitable(value):
        return await value
    return value


def _parse_change_summary(text: str) -> tuple[str, str | None]:
    """Pull the ``> change_summary: ...`` line out of LLM output.

    Returns ``(body_without_marker, summary_or_None)``. If no marker is
    present we return the original text and ``None``.
    """
    lines = text.splitlines()
    summary: str | None = None
    keep: list[str] = []
    for line in lines:
        stripped = line.lstrip("> ").strip()
        if stripped.lower().startswith("change_summary:"):
            summary = stripped.split(":", 1)[1].strip()
            continue
        keep.append(line)
    return ("\n".join(keep).rstrip() + "\n"), summary


# ---------------------------------------------------------------------------
# Filesystem (atomic write + snapshot)
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _isoweek_label(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _snapshot_previous(prev_text: str, *, now: datetime) -> Path | None:
    """Copy the previous identity.md into ``identity.md.history/<isoweek>.md``.

    Returns the snapshot path, or ``None`` if there's no previous content.
    """
    if not prev_text:
        return None
    dest_dir = identity_history_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{_isoweek_label(now)}.md"
    _atomic_write(dest, prev_text)
    return dest


def _make_diff(prev: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            prev.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="identity.prev.md",
            tofile="identity.new.md",
            n=2,
        )
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def rewrite_identity_weekly(
    conn,
    store: ProfileStore,
    llm: Any = None,
    *,
    sources_window_days: int = 7,
    max_words: int = 800,
    trigger: str = "weekly_cron",
    dry_run: bool = False,
    now: datetime | None = None,
) -> ProfileVersion | dict[str, Any]:
    """Run the weekly identity rewrite.

    Parameters
    ----------
    conn:
        sqlite3 connection backed by an opened :class:`memoryd.index.Index`.
    store:
        DAO wrapping the same connection — saves the new version row.
    llm:
        Optional override. Must be an object exposing
        ``complete(*, system, user, model=None)`` (sync or async). When
        ``None`` we use :func:`memoryd.llm.get_provider`.
    sources_window_days:
        How far back to look for "new long-term" entries (default 7).
    max_words:
        Soft cap on the rewritten profile body.
    trigger:
        Stored on the ``profile_versions`` row. ``"weekly_cron"`` for the
        scheduled job, ``"manual"`` for ad-hoc runs, ``"on_event"`` if
        triggered by a high-DURA promotion landing.
    dry_run:
        If True, returns ``{"content_md": ..., "diff": ..., "summary": ...}``
        without writing anywhere (used for preview).
    now:
        Injectable clock for tests.

    Returns the persisted :class:`ProfileVersion`, or a preview dict when
    ``dry_run=True``.
    """
    now = now or datetime.now(timezone.utc)

    signals = _collect_signals(conn, window_days=sources_window_days, now=now)
    signals_md = _format_signals(signals)

    prev_version = store.latest_version()
    prev_text = prev_version.content_md if prev_version else ""

    system, user = _resolve_prompt(prev_text, signals_md, max_words=max_words)

    provider = llm if llm is not None else _default_provider()

    raw = await _maybe_await(
        provider.complete(system=system, user=user)
    )
    body_no_summary, change_summary = _parse_change_summary(raw)
    new_content = _truncate_by_words(body_no_summary, max_words)
    diff = _make_diff(prev_text, new_content)

    if dry_run:
        return {
            "content_md": new_content,
            "diff": diff,
            "summary": change_summary,
            "sources_count": signals["sources_count"],
        }

    _snapshot_previous(prev_text, now=now)
    _atomic_write(identity_path(), new_content)

    version = store.save_version(
        new_content,
        trigger=trigger,
        prev_version=prev_version,
        diff_from_prev=diff or None,
        change_summary=change_summary,
        sources_count=signals["sources_count"],
        sources_window_start=signals["window_start"],
        sources_window_end=signals["window_end"],
        written_at=now,
    )
    return version


def _resolve_prompt(
    prev_text: str,
    signals_md: str,
    *,
    max_words: int,
) -> tuple[str, str]:
    """Use ``memoryd.prompts.rewrite_identity`` if available, else fallback."""
    try:
        from ..prompts import rewrite_identity as prompt_module  # type: ignore
    except ImportError:
        return _build_default_prompt(prev_text, signals_md, max_words=max_words)
    builder = getattr(prompt_module, "build", None)
    if builder is None:
        return _build_default_prompt(prev_text, signals_md, max_words=max_words)
    try:
        result = builder(prev_text, signals_md, max_words=max_words)
    except TypeError:
        result = builder(prev_text, signals_md)
    if isinstance(result, tuple) and len(result) == 2:
        return result
    if isinstance(result, dict):
        return result.get("system", ""), result.get("user", "")
    return _build_default_prompt(prev_text, signals_md, max_words=max_words)


def _default_provider():
    """Resolve the default LLM provider via :func:`memoryd.llm.get_provider`."""
    from ..llm import get_provider  # local import to keep test mockability

    return get_provider()
