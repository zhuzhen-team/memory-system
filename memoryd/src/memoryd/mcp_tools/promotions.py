"""MCP tools for in-conversation pending-promotion review.

The CLI has ``memoryd promote --all/--auto-high`` since a couple of releases
back, but it forced users to leave their CC conversation to use it. These
three tools let an agent walk the user through pending review **inline**:

  1. ``mem_review_pending`` — list grey-zone (0.5–0.85) candidates with DURA
     scores so the agent can summarize for the user
  2. ``mem_promote`` — approve one or many promotion ids in a single call
  3. ``mem_reject`` — same, opposite direction

Typical flow inside CC::

    you:  "过一遍 pending"
    cc:   [calls mem_review_pending → gets 8 grey-zone rows]
    cc:   "你有 8 条灰区。最高分的是 X (D 0.85 U 0.7 ...)，最低的 Y...
           我建议批 #87 #88 #91，拒 #80 #82。"
    you:  "嗯就这样"
    cc:   [calls mem_promote(ids=[87,88,91]) + mem_reject(ids=[80,82])]
    cc:   "已处理：批准 3 条，拒绝 2 条。剩 3 条没处理。"

This module deliberately re-uses ``approve_promotion`` from
``governance.analyze`` instead of duplicating SQL — the audit/storage
side-effects (writing the ``.md`` file, flipping status, updating the
audit chain) live there.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import util


def _dura_avg(dura: dict[str, Any] | None) -> float:
    if not dura:
        return 0.0
    vals = [float(v) for v in dura.values() if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 3) if vals else 0.0


async def review_pending(
    *,
    scope: str = "global",
    limit: int = 10,
    min_score: float = 0.0,
    max_score: float = 1.0,
    types: list[str] | None = None,
) -> dict[str, Any]:
    """List pending promotions so the agent can summarize for the user.

    Args:
      scope:     ``"global"`` (default) for every project, or a literal
                 ``scope_hash``. ``"auto"`` resolves to current cwd.
      limit:     cap on rows returned (default 10).
      min_score: lower bound on DURA 4-dim average (default 0.0).
      max_score: upper bound (default 1.0). Combined with ``min_score`` you
                 can ask "give me grey zone only" via ``0.5..0.85``.
      types:     optional whitelist; e.g. ``["decision", "playbook"]``.

    Returns ``{ok, scope_hash, total_pending, hits: [...]}`` where each hit
    is ``{id, type, title, dura_avg, dura, source, created_at}`` sorted
    ascending by ``dura_avg`` so the most uncertain candidates show first
    — that's where human judgment actually matters.
    """
    try:
        sh = util.derive_scope(scope) if scope != "global" else "global"
    except ValueError as exc:
        return util.fail(str(exc), code="invalid_argument")

    is_global = util.is_global_scope(sh)
    conn = util.open_db()
    try:
        sql = (
            "SELECT id, source_session_slug, proposed_type, proposed_title, "
            "       proposed_body, dura_score, reasoning, scope_hash, created_at "
            "FROM promotions WHERE status = 'pending'"
        )
        args: list[Any] = []
        if not is_global:
            sql += " AND scope_hash = ?"
            args.append(sh)
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql, args).fetchall()
        total = len(rows)
    finally:
        conn.close()

    type_set = set(types) if types else None
    candidates: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        if type_set and d["proposed_type"] not in type_set:
            continue
        try:
            dura = json.loads(d.get("dura_score") or "{}")
        except (TypeError, json.JSONDecodeError):
            dura = {}
        avg = _dura_avg(dura)
        if not (min_score <= avg <= max_score):
            continue
        candidates.append({
            "id": d["id"],
            "type": d["proposed_type"],
            "title": (d.get("proposed_title") or "")[:140],
            "dura_avg": avg,
            "dura": dura,
            "source": d.get("source_session_slug"),
            "scope_hash": d.get("scope_hash"),
            "created_at": d.get("created_at"),
            "reasoning": (d.get("reasoning") or "")[:240],
        })

    # Ascending by avg — most ambiguous first; the agent should triage
    # those before the obvious-keep / obvious-drop tails.
    candidates.sort(key=lambda c: c["dura_avg"])
    return util.ok(
        scope_hash=sh,
        total_pending=total,
        filtered=len(candidates),
        hits=candidates[: max(1, min(int(limit), 100))],
    )


async def promote(
    *,
    promotion_ids: list[int] | None = None,
    auto_high: bool = False,
    threshold: float = 0.85,
) -> dict[str, Any]:
    """Approve one or many pending promotions in a single call.

    Args:
      promotion_ids: explicit list of ids to approve. Required unless
                     ``auto_high=True``.
      auto_high:     if True, ignore ``promotion_ids`` and approve every
                     pending row whose DURA 4-dim average ≥ ``threshold``.
      threshold:     DURA cutoff for auto_high mode (default 0.85).

    Returns ``{ok, approved: [ids], skipped: [{id, reason}], errors: [...]}``.
    """
    from ..governance.analyze import approve_promotion, list_pending_promotions

    data_root = util.data_root()
    if auto_high:
        pending = list_pending_promotions(data_root)
        targets: list[int] = []
        for p in pending:
            try:
                dura = json.loads(p.get("dura_score") or "{}")
            except (TypeError, json.JSONDecodeError):
                dura = {}
            if _dura_avg(dura) >= threshold:
                targets.append(int(p["id"]))
    else:
        if not promotion_ids:
            return util.fail(
                "either promotion_ids or auto_high=True required",
                code="invalid_argument",
            )
        targets = [int(i) for i in promotion_ids]

    approved: list[int] = []
    errors: list[dict[str, Any]] = []
    for pid in targets:
        try:
            approve_promotion(data_root, pid)
            approved.append(pid)
        except Exception as exc:  # noqa: BLE001 — best-effort batch
            errors.append({"id": pid, "reason": str(exc)[:200]})
    return util.ok(approved=approved, errors=errors, count=len(approved))


async def reject(*, promotion_ids: list[int]) -> dict[str, Any]:
    """Reject one or many pending promotions (sets ``status='rejected'``).

    Rejected rows stay in the table for audit, but ``mem_review_pending`` /
    ``digest --tui`` no longer surface them. They do **not** get a ``.md``
    written to the long-term store.
    """
    if not promotion_ids:
        return util.fail("promotion_ids required", code="invalid_argument")

    targets = [int(i) for i in promotion_ids]
    data_root = util.data_root()
    db = data_root / "index.db"
    if not db.exists():
        return util.fail(f"index.db missing at {db}", code="not_found")

    rejected: list[int] = []
    not_found: list[int] = []
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(db))
    try:
        for pid in targets:
            cur = conn.execute(
                "UPDATE promotions SET status='rejected', decided_at=? "
                "WHERE id=? AND status='pending'",
                (now, pid),
            )
            if cur.rowcount == 0:
                not_found.append(pid)
            else:
                rejected.append(pid)
        conn.commit()
    finally:
        conn.close()
    return util.ok(rejected=rejected, not_found=not_found, count=len(rejected))


__all__ = ["promote", "reject", "review_pending"]
