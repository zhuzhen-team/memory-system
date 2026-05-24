"""governance/analyze.py tests with mock LLM."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.governance.analyze import (
    analyze_session,
    build_dura_prompt,
    parse_candidates,
)
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_memory


class _FakeLLM:
    def __init__(self, returns: str) -> None:
        self.returns = returns
        self.called_with = None

    def complete(self, *, system: str, user: str, model: str | None = None) -> str:
        self.called_with = (system, user)
        return self.returns


def _build_session_in_root(memory_root: Path, body: str = "user said: 决定 logo 用深蓝色") -> SessionMemory:
    s = SessionMemory(
        frontmatter=Frontmatter(
            title="t",
            slug="2026-05-14-s",
            type="session",
            scope_hash="proj1",
            triggers=["logo"],
            source="manual",
            created_at=datetime(2026, 5, 14),
        ),
        body=body,
    )
    save_memory(memory_root, s)
    return s


def test_build_dura_prompt_substitutes_placeholders():
    s = SessionMemory(
        frontmatter=Frontmatter(
            title="t",
            slug="2026-05-14-x",
            type="session",
            scope_hash="proj1",
            source="manual",
            created_at=datetime(2026, 5, 14),
        ),
        body="HELLO BODY",
    )
    prompt = build_dura_prompt(s, scope_root="/path", existing_titles=["a", "b"])
    assert "HELLO BODY" in prompt
    assert "/path" in prompt
    assert "a" in prompt and "b" in prompt


def test_parse_candidates_strict_json():
    raw = '[{"type":"decision","title":"x","body":"y","triggers":["t1","t2"],"dura":{"D":0.7,"U":0.8,"R":0.7,"A":0.9},"reasoning":"r","supersedes":[]}]'
    out = parse_candidates(raw)
    assert len(out) == 1
    assert out[0]["type"] == "decision"


def test_parse_candidates_tolerates_fenced_output():
    raw = "```json\n[]\n```"
    out = parse_candidates(raw)
    assert out == []


def test_parse_candidates_returns_empty_on_garbage():
    raw = "not json at all"
    out = parse_candidates(raw)
    assert out == []


def test_parse_candidates_filters_below_threshold():
    raw = json.dumps([
        {
            "type": "decision", "title": "high", "body": "x", "triggers": ["a", "b"],
            "dura": {"D": 0.9, "U": 0.8, "R": 0.7, "A": 0.95}, "reasoning": "", "supersedes": []
        },
        {
            "type": "fact", "title": "low", "body": "x", "triggers": ["c", "d"],
            "dura": {"D": 0.9, "U": 0.5, "R": 0.7, "A": 0.95}, "reasoning": "", "supersedes": []
        },
    ])
    out = parse_candidates(raw)
    titles = [c["title"] for c in out]
    assert "high" in titles
    assert "low" not in titles  # U=0.5 < 0.6


def test_analyze_session_writes_promotion(memory_root: Path):
    """High DURA (avg >= 0.85) auto-promotes to status='approved'.

    This is the autonomous-by-default behaviour: the user trusts the LLM
    enough at this confidence to skip manual approval.
    """
    sess = _build_session_in_root(memory_root)
    fake = _FakeLLM(json.dumps([{
        "type": "decision",
        "title": "use deep blue for logo",
        "body": "decided deep blue + silver-gray",
        "triggers": ["logo", "blue"],
        "dura": {"D": 0.9, "U": 0.9, "R": 0.85, "A": 0.95},  # avg 0.9 → auto
        "reasoning": "user explicit",
        "supersedes": [],
    }]))
    analyze_session(memory_root, session_slug=sess.frontmatter.slug, provider=fake)

    from memoryd.index import open_index
    idx = open_index(memory_root / "index.db")
    rows = idx.conn.execute("SELECT proposed_type, proposed_title, status FROM promotions").fetchall()
    idx.close()
    assert len(rows) == 1
    assert rows[0][0] == "decision"
    assert rows[0][1] == "use deep blue for logo"
    assert rows[0][2] == "approved"  # was 'pending' pre-autonomous-default


def test_analyze_session_gray_zone_stays_pending(memory_root: Path):
    """Moderate DURA (0.5..0.85) writes status='pending' for manual review."""
    sess = _build_session_in_root(memory_root)
    fake = _FakeLLM(json.dumps([{
        "type": "fact",
        "title": "tentative observation",
        "body": "maybe a pattern",
        "triggers": ["maybe"],
        "dura": {"D": 0.7, "U": 0.7, "R": 0.6, "A": 0.6},  # avg 0.65 → pending
        "reasoning": "moderate signal",
        "supersedes": [],
    }]))
    analyze_session(memory_root, session_slug=sess.frontmatter.slug, provider=fake)
    from memoryd.index import open_index
    idx = open_index(memory_root / "index.db")
    rows = idx.conn.execute("SELECT proposed_type, status FROM promotions").fetchall()
    idx.close()
    assert len(rows) == 1
    assert tuple(rows[0]) == ("fact", "pending")


def test_analyze_session_low_dura_auto_rejects(memory_root: Path):
    """Very low DURA (< 0.5) is dropped entirely (no row inserted)."""
    sess = _build_session_in_root(memory_root)
    fake = _FakeLLM(json.dumps([{
        "type": "preference",
        "title": "noisy",
        "body": "low signal",
        "triggers": [],
        "dura": {"D": 0.3, "U": 0.4, "R": 0.4, "A": 0.5},  # avg 0.4 → reject
        "reasoning": "",
        "supersedes": [],
    }]))
    analyze_session(memory_root, session_slug=sess.frontmatter.slug, provider=fake)
    from memoryd.index import open_index
    idx = open_index(memory_root / "index.db")
    rows = idx.conn.execute("SELECT COUNT(*) FROM promotions").fetchall()
    idx.close()
    assert rows[0][0] == 0


def test_analyze_session_skips_when_session_not_found(memory_root: Path):
    """Missing session is a no-op (best-effort daemon)."""
    fake = _FakeLLM("[]")
    # Should not raise:
    analyze_session(memory_root, session_slug="no-such-slug", provider=fake)


def test_analyze_session_handles_llm_returning_empty(memory_root: Path):
    sess = _build_session_in_root(memory_root)
    fake = _FakeLLM("[]")
    analyze_session(memory_root, session_slug=sess.frontmatter.slug, provider=fake)
    from memoryd.index import open_index
    idx = open_index(memory_root / "index.db")
    rows = idx.conn.execute("SELECT * FROM promotions").fetchall()
    idx.close()
    assert rows == []
