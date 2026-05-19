"""Trigger frequency stats + digest "trends" rendering.

The ``trigger_stats`` table is incremented every time a memoryd surface
(MCP ``search_memory`` / ``get_memory`` / capture pipeline) hits a memory
matched by a particular trigger word. Aggregates feed both:

- the weekly digest (top triggers in the last 7 days),
- the LLM-driven weekly identity rewrite (signals what the user has been
  thinking about lately).

Pure SQLite + Python — no LLM, no async.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def increment_trigger(
    conn: sqlite3.Connection,
    trigger: str,
    scope_hash: str = "_global",
    *,
    day: str | None = None,
    hits: int = 1,
) -> None:
    """Bump the daily counter for ``(trigger, scope_hash, day)``.

    Uses ``INSERT ... ON CONFLICT DO UPDATE`` so the first call inserts
    and subsequent calls increment. ``day`` defaults to today (UTC) and
    is mainly overridden in tests.
    """
    if not trigger:
        return
    d = day or _today()
    conn.execute(
        """
        INSERT INTO trigger_stats (trigger, scope_hash, day, hits)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(trigger, scope_hash, day) DO UPDATE SET
            hits = hits + excluded.hits
        """,
        (trigger, scope_hash or "_global", d, hits),
    )
    conn.commit()


def increment_triggers(
    conn: sqlite3.Connection,
    triggers: Iterable[str],
    scope_hash: str = "_global",
    *,
    day: str | None = None,
) -> None:
    """Convenience helper — bumps a batch of trigger words once each."""
    for t in triggers:
        if t:
            increment_trigger(conn, t, scope_hash, day=day)


def top_triggers(
    conn: sqlite3.Connection,
    *,
    window_days: int = 7,
    scope_hash: str | None = None,
    limit: int = 10,
    now: datetime | None = None,
) -> list[tuple[str, int]]:
    """Return ``[(trigger, total_hits), ...]`` for the last ``window_days``.

    When ``scope_hash`` is None, sums across all scopes (including the
    sentinel ``_global``). ``now`` is injectable for deterministic tests.
    """
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=window_days)).date()
    sql = (
        "SELECT trigger, SUM(hits) AS total FROM trigger_stats "
        "WHERE day >= ?"
    )
    args: list[Any] = [cutoff.isoformat()]
    if scope_hash is not None:
        sql += " AND scope_hash = ?"
        args.append(scope_hash)
    sql += " GROUP BY trigger ORDER BY total DESC, trigger ASC LIMIT ?"
    args.append(limit)
    return [(r[0], int(r[1])) for r in conn.execute(sql, args).fetchall()]


def rising_triggers(
    conn: sqlite3.Connection,
    *,
    recent_days: int = 7,
    baseline_days: int = 21,
    scope_hash: str | None = None,
    limit: int = 5,
    now: datetime | None = None,
) -> list[tuple[str, int, int]]:
    """Return triggers whose recent activity exceeds the baseline window.

    Compares ``[now-recent_days, now)`` against the older
    ``[now-recent_days-baseline_days, now-recent_days)`` window. Returns
    ``[(trigger, recent_hits, prior_hits)]`` sorted by ``recent - prior``.
    """
    base_now = now or datetime.now(timezone.utc)
    recent_start = (base_now - timedelta(days=recent_days)).date()
    prior_start = (base_now - timedelta(days=recent_days + baseline_days)).date()
    prior_end = recent_start

    scope_clause = ""
    args_scope: list[Any] = []
    if scope_hash is not None:
        scope_clause = " AND scope_hash = ?"
        args_scope = [scope_hash]

    recent_rows = conn.execute(
        f"SELECT trigger, SUM(hits) FROM trigger_stats "
        f"WHERE day >= ?{scope_clause} GROUP BY trigger",
        [recent_start.isoformat(), *args_scope],
    ).fetchall()
    prior_rows = conn.execute(
        f"SELECT trigger, SUM(hits) FROM trigger_stats "
        f"WHERE day >= ? AND day < ?{scope_clause} GROUP BY trigger",
        [prior_start.isoformat(), prior_end.isoformat(), *args_scope],
    ).fetchall()

    prior: dict[str, int] = {r[0]: int(r[1]) for r in prior_rows}
    deltas: list[tuple[str, int, int]] = []
    for t, hits in recent_rows:
        h = int(hits)
        p = prior.get(t, 0)
        if h > p:
            deltas.append((t, h, p))
    deltas.sort(key=lambda x: (-(x[1] - x[2]), x[0]))
    return deltas[:limit]


def recall_hot(
    conn: sqlite3.Connection,
    *,
    limit: int = 5,
    min_recall_count: int = 2,
) -> list[dict[str, Any]]:
    """Top recalled long-term memories (``recall_count >= min_recall_count``).

    Filters out session-type rows so the digest highlights durable knowledge
    rather than ephemeral session captures. Excludes sensitive scopes.
    """
    rows = conn.execute(
        """
        SELECT slug, title, type, recall_count, last_recalled_at
        FROM memories
        WHERE recall_count >= ?
          AND type != 'session'
          AND decay_state != 'soft-forgotten'
          AND COALESCE(scope_sensitive, 0) = 0
        ORDER BY recall_count DESC, last_recalled_at DESC
        LIMIT ?
        """,
        (min_recall_count, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def render_trends_section(
    conn: sqlite3.Connection,
    window_days: int = 7,
    *,
    now: datetime | None = None,
) -> str:
    """Produce a markdown section suitable for embedding in the weekly digest.

    Three sub-sections: top triggers, rising triggers, recall-hot memories.
    Empty sub-sections are still rendered with a "(无)" placeholder so the
    template shape stays stable.
    """
    lines: list[str] = ["## 趋势 trends"]

    top = top_triggers(conn, window_days=window_days, now=now)
    lines.append("")
    lines.append(f"### 近 {window_days} 天 top triggers")
    if not top:
        lines.append("- (无)")
    else:
        for trig, hits in top:
            lines.append(f"- {trig}  ({hits})")

    rising = rising_triggers(conn, recent_days=window_days, now=now)
    lines.append("")
    lines.append("### 上升中的话题 rising")
    if not rising:
        lines.append("- (无)")
    else:
        for trig, recent, prior in rising:
            lines.append(f"- {trig}  {prior} → {recent}")

    hot = recall_hot(conn)
    lines.append("")
    lines.append("### 高频回忆 recall hot")
    if not hot:
        lines.append("- (无)")
    else:
        for m in hot:
            lines.append(
                f"- [{m['type']}] {m['title']}  (×{m['recall_count']})"
            )

    return "\n".join(lines) + "\n"
