"""Run DURA 4-criteria extraction on a single session.

Strategy:
- load session.md
- query existing long-term titles in same scope (for U criterion)
- call LLM with bundled prompt template
- parse JSON candidates; filter by DURA >= 0.6 all four
- write each as a row in promotions table (status=pending)

Never raises into caller — best-effort daemon. On LLM failure logs +
skips. Session capture path keeps working regardless.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..config import load_config
from ..index import open_index
from ..schema import SessionMemory


_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "dura_extract.txt"


def build_dura_prompt(
    session: SessionMemory,
    *,
    scope_root: str,
    existing_titles: list[str],
) -> str:
    cfg = load_config()
    override = cfg.get("prompts", {}).get("dura_extract", "")
    if override and Path(override).exists():
        template = Path(override).read_text(encoding="utf-8")
    else:
        template = _PROMPT_PATH.read_text(encoding="utf-8")
    body_clip = session.body[:8000]
    return (
        template
        .replace("{{session_body}}", body_clip)
        .replace("{{scope_root}}", scope_root)
        .replace("{{existing_titles}}", "\n".join(f"- {t}" for t in existing_titles) or "(none)")
    )


def parse_candidates(raw: str) -> list[dict]:
    """Robust JSON parse: strip fences, accept array."""
    stripped = raw.strip()
    if stripped.startswith("```"):
        # strip leading fence (with optional 'json' tag) and trailing fence
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        # try to find an array somewhere
        m = re.search(r"\[.*\]", stripped, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        dura = item.get("dura") or {}
        if not all(k in dura for k in ("D", "U", "R", "A")):
            continue
        if not all(isinstance(dura[k], (int, float)) and dura[k] >= 0.6 for k in ("D", "U", "R", "A")):
            continue
        out.append(item)
    return out


def analyze_session(
    memory_root: Path,
    *,
    session_slug: str,
    provider,
) -> None:
    """Best-effort: read session, ask LLM, write promotions. Never raises."""
    try:
        idx = open_index(memory_root / "index.db")
    except Exception:
        return
    try:
        row = idx.get_memory(session_slug)
        if row is None:
            return
        sess_path = memory_root / row["body_path"]
        if not sess_path.exists():
            return
        from ..storage import load_session
        session = load_session(sess_path)
        scope_hash = row["scope_hash"]

        # existing titles in same scope (long-term only — exclude sessions)
        existing_titles = []
        for t in ("decision", "preference", "fact", "playbook", "warning"):
            for r in idx.list_by_type(t, scope_hash=scope_hash):
                existing_titles.append(r["title"])

        prompt = build_dura_prompt(session, scope_root=scope_hash, existing_titles=existing_titles)
        try:
            raw = provider.complete(system="Extract durable insights.", user=prompt)
        except Exception:
            return
        candidates = parse_candidates(raw)

        now = datetime.now(timezone.utc).isoformat()
        for c in candidates:
            idx.conn.execute(
                """
                INSERT INTO promotions (
                  source_session_slug, proposed_type, proposed_title,
                  proposed_body, proposed_triggers, dura_score, reasoning,
                  proposed_supersedes, scope_hash, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    session_slug,
                    c["type"],
                    c["title"][:200],
                    c["body"][:5000],
                    json.dumps(c.get("triggers", [])),
                    json.dumps(c["dura"]),
                    c.get("reasoning", "")[:500],
                    json.dumps(c.get("supersedes", [])),
                    scope_hash,
                    now,
                ),
            )
        idx.conn.commit()
    finally:
        idx.close()
