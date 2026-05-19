"""Monthly profile evolution report.

Pipeline (called by cron every 1st of month, 04:00):
    1. Pull all ``profile_versions`` written during the target month.
    2. Tally supersede events and entity additions / removals.
    3. Hand the timeline to an LLM and ask for a narrative report
       (中文, ~500-800 字) — what's stable, what shifted, what to watch.
    4. Persist as ``profile_change_reports`` (one row per period) and
       drop a markdown copy under ``profile/change-reports/YYYY-MM.md``.

Sensitive scopes are filtered out at signal-collection time (same rule
as :mod:`memoryd.profile.identity`).
"""
from __future__ import annotations

import calendar
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .identity import (
    _atomic_write,
    _maybe_await,
    change_reports_dir,
    _default_provider,
)
from .store import ProfileStore


# ---------------------------------------------------------------------------
# Window helpers
# ---------------------------------------------------------------------------


def _month_window(year: int, month: int) -> tuple[datetime, datetime]:
    """Return ``(start, end_exclusive)`` for the given month, both UTC."""
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    last_day = calendar.monthrange(year, month)[1]
    # End-exclusive: first second of the *next* month, normalized.
    end = datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return start, end


def _period_label(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


# ---------------------------------------------------------------------------
# Signal collection
# ---------------------------------------------------------------------------


def _collect_monthly_signals(
    conn,
    store: ProfileStore,
    *,
    year: int,
    month: int,
) -> dict[str, Any]:
    """Gather everything that fed the LLM rewrites this month."""
    start, end = _month_window(year, month)
    versions = store.list_versions(since=start, until=end + timedelta(seconds=1))

    # Supersede events approved this month (skip sensitive scopes).
    supersede_rows = conn.execute(
        """
        SELECT proposed_title, proposed_supersedes, created_at, scope_hash
        FROM promotions
        WHERE status = 'approved'
          AND created_at >= ? AND created_at < ?
          AND proposed_supersedes != '[]'
          AND scope_hash NOT IN (SELECT scope_hash FROM sensitive_scopes)
        ORDER BY created_at ASC
        """,
        (start.isoformat(), (end + timedelta(seconds=1)).isoformat()),
    ).fetchall()
    supersedes: list[dict[str, Any]] = []
    for r in supersede_rows:
        try:
            old = json.loads(r[1])
        except (TypeError, json.JSONDecodeError):
            old = []
        if not old:
            continue
        supersedes.append(
            {
                "new_title": r[0],
                "old_slugs": old,
                "at": r[2],
                "scope_hash": r[3],
            }
        )

    # New long-term memories this month vs same-window-last-month — used
    # as a proxy for "entities_added / entities_dropped" when the
    # knowledge_graph module isn't available.
    added_count = conn.execute(
        """
        SELECT COUNT(*) FROM memories
        WHERE type IN ('decision','preference','fact','playbook','warning')
          AND created_at >= ? AND created_at < ?
          AND COALESCE(scope_sensitive, 0) = 0
        """,
        (start.isoformat(), (end + timedelta(seconds=1)).isoformat()),
    ).fetchone()[0]

    dropped_count = conn.execute(
        """
        SELECT COUNT(*) FROM memories
        WHERE decay_state = 'soft-forgotten'
          AND COALESCE(last_recalled_at, created_at) >= ?
          AND COALESCE(last_recalled_at, created_at) < ?
          AND COALESCE(scope_sensitive, 0) = 0
        """,
        (start.isoformat(), (end + timedelta(seconds=1)).isoformat()),
    ).fetchone()[0]

    # If sub-agent C's knowledge_graph module is on disk, prefer its
    # entity lifecycle counts.
    entity_stats = _collect_entity_stats(conn, start=start, end=end)
    if entity_stats:
        added_count = entity_stats.get("added", added_count)
        dropped_count = entity_stats.get("dropped", dropped_count)

    return {
        "year": year,
        "month": month,
        "start": start,
        "end": end,
        "versions": versions,
        "supersedes": supersedes,
        "entities_added": int(added_count or 0),
        "entities_dropped": int(dropped_count or 0),
    }


def _collect_entity_stats(conn, *, start: datetime, end: datetime) -> dict[str, int]:
    """Optional hook into ``memoryd.knowledge_graph.lifecycle`` if it exists."""
    try:
        from .. import knowledge_graph  # type: ignore
    except ImportError:
        return {}
    fn = getattr(knowledge_graph, "lifecycle", None)
    if fn is None:
        return {}
    try:
        result = fn(conn, start=start, end=end)
    except Exception:
        return {}
    if not isinstance(result, dict):
        return {}
    return {
        "added": int(result.get("added", 0) or 0),
        "dropped": int(result.get("dropped", 0) or 0),
    }


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _format_monthly_signals(sig: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.append(f"# 月度窗口")
    parts.append(f"{_period_label(sig['year'], sig['month'])}")
    parts.append("")
    parts.append(f"## 本月 identity 重写次数: {len(sig['versions'])}")
    for v in sig["versions"]:
        line = (
            f"- v{v.version_num} @ {v.written_at.date().isoformat()} "
            f"({v.trigger}) — {v.change_summary or '(无摘要)'}"
        )
        parts.append(line)
    parts.append("")
    parts.append(f"## 取代事件: {len(sig['supersedes'])}")
    for s in sig["supersedes"]:
        parts.append(
            f"- {s['new_title']} <- {', '.join(s['old_slugs'])} "
            f"@ {s['at'][:10]}"
        )
    parts.append("")
    parts.append(
        f"## 实体生命周期: +{sig['entities_added']} / "
        f"-{sig['entities_dropped']}"
    )
    return "\n".join(parts)


def _build_default_report_prompt(
    period_label: str,
    signals_md: str,
) -> tuple[str, str]:
    system = (
        "你是用户画像演化的观察者。请基于这个月的 identity 重写历史、取代事件、"
        "实体生命周期，写一篇 500-800 字的中文月度报告（Markdown）。\n"
        "需要包括：1) 这个月画像的主要变化；2) 哪些信念被强化；3) 哪些被取代或淡出；"
        "4) 下个月值得关注的 1-2 件事。不要 fenced code block。"
    )
    user = (
        f"## 周期: {period_label}\n\n"
        f"## 信号\n\n{signals_md}\n\n"
        "请输出报告。"
    )
    return system, user


def _resolve_report_prompt(
    period_label: str,
    signals_md: str,
) -> tuple[str, str]:
    try:
        from ..prompts import profile_change_report as prompt_module  # type: ignore
    except ImportError:
        return _build_default_report_prompt(period_label, signals_md)
    builder = getattr(prompt_module, "build", None)
    if builder is None:
        return _build_default_report_prompt(period_label, signals_md)
    try:
        result = builder(period_label, signals_md)
    except TypeError:
        result = builder(signals_md)
    if isinstance(result, tuple) and len(result) == 2:
        return result
    if isinstance(result, dict):
        return result.get("system", ""), result.get("user", "")
    return _build_default_report_prompt(period_label, signals_md)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def generate_monthly_change_report(
    conn,
    store: ProfileStore,
    llm: Any = None,
    *,
    year: int,
    month: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Generate (and persist) the monthly change report for ``year-month``.

    Returns a dict with ``period``, ``content_md``, ``versions_count``,
    ``supersedes_count``, ``entities_added``, ``entities_dropped``, and
    ``path`` (markdown copy on disk, ``None`` for dry_run).
    """
    signals = _collect_monthly_signals(conn, store, year=year, month=month)
    signals_md = _format_monthly_signals(signals)
    period = _period_label(year, month)
    system, user = _resolve_report_prompt(period, signals_md)

    provider = llm if llm is not None else _default_provider()

    raw = await _maybe_await(provider.complete(system=system, user=user))
    content_md = (raw or "").strip() + "\n"

    result = {
        "period": period,
        "content_md": content_md,
        "versions_count": len(signals["versions"]),
        "supersedes_count": len(signals["supersedes"]),
        "entities_added": signals["entities_added"],
        "entities_dropped": signals["entities_dropped"],
        "path": None,
    }

    if dry_run:
        return result

    # Persist to disk + SQLite.
    md_path = change_reports_dir() / f"{period}.md"
    _atomic_write(md_path, content_md)
    result["path"] = str(md_path)

    store.save_change_report(
        period,
        content_md,
        versions_count=result["versions_count"],
        supersedes_count=result["supersedes_count"],
        entities_added=result["entities_added"],
        entities_dropped=result["entities_dropped"],
    )
    return result
