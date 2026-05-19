"""Entity-conflict judging handlers (2 tools).

These are the only MCP tools that *can* call the LLM synchronously:

- ``mem_judge`` asks "does this new fact replace memory X?" — used when an
  agent wants to record a verdict on a pending conflict.
- ``mem_compare`` does a diff between two memories and runs the same
  judge prompt to classify the relationship — agents call this when they
  notice apparently-overlapping memories.

Both tools fail soft when no LLM is available: they return a structured
``SupersedeJudgment`` with ``confidence=0.0`` rather than crashing, so the
calling agent gets a deterministic envelope.
"""
from __future__ import annotations

import difflib
import os
from typing import Any

from ..storage import load_session
from . import util


# --- Internal helpers --------------------------------------------------------


def _load_memory_text(memory_id: str) -> tuple[dict[str, Any] | None, str]:
    """Return (row_dict, body_text) for a memory id, or (None, "") if missing."""
    root = util.data_root()
    conn = util.open_db()
    try:
        row = conn.execute(
            "SELECT * FROM memories WHERE slug = ?", (memory_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None, ""
    row_dict = dict(row)
    path = root / row_dict["body_path"]
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
    return row_dict, body


async def _judge_via_llm(
    new_text: str,
    old_text: str,
    *,
    old_id: str,
    scope_hint: str = "",
) -> dict[str, Any]:
    """Run the ``judge_supersedes`` prompt against the configured LLM.

    Returns a dict shaped like :class:`SupersedeJudgment`. Any provider
    error returns a stub verdict with ``decision=False`` so callers can
    distinguish "LLM said no" from "LLM unavailable" via ``llm_available``.
    """
    try:
        from ..llm import get_llm
        from ..llm.prompts import (
            SupersedeJudgment,
            render_judge_prompt,
        )
    except Exception as e:
        return {
            "candidate_old_id": old_id,
            "is_superseded": False,
            "confidence": 0.0,
            "reason": f"llm prompts unavailable: {e}",
            "llm_available": False,
        }

    try:
        provider = get_llm()
    except Exception as e:
        return {
            "candidate_old_id": old_id,
            "is_superseded": False,
            "confidence": 0.0,
            "reason": f"llm provider unavailable: {e}",
            "llm_available": False,
        }

    messages = render_judge_prompt(
        old_id=old_id,
        old_text=old_text,
        new_fact=new_text,
        scope_hint=scope_hint,
    )
    try:
        result = await provider.generate_json(messages, SupersedeJudgment)
    except Exception as e:
        return {
            "candidate_old_id": old_id,
            "is_superseded": False,
            "confidence": 0.0,
            "reason": f"llm error: {e}",
            "llm_available": True,
        }

    if hasattr(result, "model_dump"):
        out = result.model_dump()
    elif isinstance(result, dict):
        out = dict(result)
    else:
        out = {"candidate_old_id": old_id, "is_superseded": False, "confidence": 0.0,
               "reason": "unexpected llm result type"}
    out["llm_available"] = True
    return out


# --- mem_judge ---------------------------------------------------------------


async def judge(new_text: str, old_memory_id: str) -> dict[str, Any]:
    """Decide whether ``new_text`` supersedes the memory ``old_memory_id``.

    The decision is returned as-is — the agent should call ``mem_save`` /
    ``mem_capture_passive`` to actually record the supersede. (We don't
    auto-write supersedes_chain here: that's the responsibility of the
    governance digest pipeline, which catches them at write time via
    ``detect_supersedes_for_new_memory``.)
    """
    if not (new_text or "").strip():
        return util.fail("new_text is empty", code="invalid_argument")
    if not old_memory_id:
        return util.fail("old_memory_id required", code="invalid_argument")

    row, old_text = _load_memory_text(old_memory_id)
    if row is None:
        return util.fail(f"memory not found: {old_memory_id}", code="not_found")

    scope_hint = row.get("scope_hash", "") if row else ""
    verdict = await _judge_via_llm(
        new_text=new_text.strip(),
        old_text=old_text.strip(),
        old_id=old_memory_id,
        scope_hint=scope_hint,
    )

    # Classify the verdict into a band so agents can act without re-knowing
    # the threshold constants.
    band = _band_for(verdict.get("confidence", 0.0), verdict.get("is_superseded", False))
    return util.ok(
        judgment=verdict,
        band=band,
        old_memory_id=old_memory_id,
    )


def _band_for(confidence: float, is_superseded: bool) -> str:
    """Return one of ``auto`` / ``review`` / ``ignore`` for the verdict."""
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        c = 0.0
    if not is_superseded:
        return "ignore"
    # Mirror SUPERSEDE_AUTO_THRESHOLD / SUPERSEDE_REVIEW_THRESHOLD without
    # importing the constants at module load (they live in llm.prompts which
    # may fail to import in stripped test envs).
    if c >= 0.85:
        return "auto"
    if c >= 0.5:
        return "review"
    return "ignore"


# --- mem_compare -------------------------------------------------------------


async def compare(memory_id_a: str, memory_id_b: str) -> dict[str, Any]:
    """Diff two memories + ask the LLM whether they conflict.

    Returns: ``{judgment, diff_lines, a, b}``.

    ``diff_lines`` is a unified diff between bodies (≤200 lines) — handy
    for agents that want to render a side-by-side without re-fetching both
    memories.
    """
    if not memory_id_a or not memory_id_b:
        return util.fail("memory_id_a and memory_id_b required", code="invalid_argument")
    if memory_id_a == memory_id_b:
        return util.fail("compare requires two different memory ids", code="invalid_argument")

    row_a, body_a = _load_memory_text(memory_id_a)
    if row_a is None:
        return util.fail(f"memory not found: {memory_id_a}", code="not_found")
    row_b, body_b = _load_memory_text(memory_id_b)
    if row_b is None:
        return util.fail(f"memory not found: {memory_id_b}", code="not_found")

    diff = list(
        difflib.unified_diff(
            body_a.splitlines(),
            body_b.splitlines(),
            fromfile=memory_id_a,
            tofile=memory_id_b,
            lineterm="",
        )
    )[:200]

    # Use B as "newer fact" + A as "old memory" — convention: callers pass
    # (newer, older) but we don't actually trust which is which; the LLM
    # gets both texts in full so the verdict is symmetric over content.
    scope_hint = row_a.get("scope_hash", "") if row_a else ""
    verdict = await _judge_via_llm(
        new_text=body_b.strip(),
        old_text=body_a.strip(),
        old_id=memory_id_a,
        scope_hint=scope_hint,
    )
    band = _band_for(verdict.get("confidence", 0.0), verdict.get("is_superseded", False))

    return util.ok(
        a={"memory_id": memory_id_a, "title": row_a.get("title"),
           "type": row_a.get("type"), "created_at": row_a.get("created_at")},
        b={"memory_id": memory_id_b, "title": row_b.get("title"),
           "type": row_b.get("type"), "created_at": row_b.get("created_at")},
        diff_lines=diff,
        judgment=verdict,
        band=band,
    )


__all__ = ["compare", "judge"]
