"""Tests for memoryd.handoff.generator.

Covers:
- gather_signals returns empty struct when index.db missing
- gather_signals pulls decisions / warnings / sessions / entities
- generate_handoff fallback (no LLM) renders structured markdown
- generate_handoff with LLM provider routes through .complete()
- generate_handoff falls back gracefully when LLM raises
- prompt content includes 6-block markers + anti-pattern rules
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memoryd.handoff import gather_signals, generate_handoff
from memoryd.handoff.prompt import HANDOFF_SYSTEM, render_handoff_prompt
from memoryd.index import open_index


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MEMORYD_PROFILE_DIR", str(tmp_path / "profile"))
    idx = open_index(tmp_path / "index.db")
    idx.close()
    return tmp_path


def _insert(
    root: Path,
    *,
    slug: str,
    type_: str,
    title: str,
    scope_hash: str = "abc123",
    days_ago: int = 0,
    body_text: str = "",
) -> None:
    created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    body_path = f"scopes/{scope_hash}/{type_}/{slug}.md"
    # Materialize body file too so generator can read it
    full_path = root / body_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(
        f"---\ntitle: {title}\n---\n\n{body_text}", encoding="utf-8"
    )
    with sqlite3.connect(str(root / "index.db")) as conn:
        conn.execute(
            """
            INSERT INTO memories
                (slug, type, scope_hash, title, source, created_at, updated_at,
                 ttl_days, decay_state, last_recalled_at, recall_count,
                 fingerprint, body_path, scope_sensitive)
            VALUES (?, ?, ?, ?, 'test', ?, NULL, NULL, 'fresh', NULL, 0, ?, ?, 0)
            """,
            (slug, type_, scope_hash, title, created, slug, body_path),
        )
        conn.commit()


# ---------- gather_signals ----------


def test_gather_signals_handles_missing_db(tmp_path: Path):
    """No index.db → empty structure, no exception."""
    out = gather_signals(tmp_path, scope_hash="anything", window_days=7)
    assert out["decisions"] == []
    assert out["warnings"] == []
    assert out["sessions"] == []
    assert out["entities"] == []


def test_gather_signals_pulls_by_type(isolated_root: Path):
    _insert(isolated_root, slug="d1", type_="decision", title="切到 Solid",
            days_ago=1, body_text="为什么：性能 + 体积")
    _insert(isolated_root, slug="w1", type_="warning", title="Safari 不支持 X")
    _insert(isolated_root, slug="s1", type_="session", title="工作日 1")

    out = gather_signals(isolated_root, scope_hash="abc123", window_days=7)
    assert len(out["decisions"]) == 1
    assert out["decisions"][0]["title"] == "切到 Solid"
    assert "性能 + 体积" in out["decisions"][0]["body"]
    assert len(out["warnings"]) == 1
    assert len(out["sessions"]) == 1


def test_gather_signals_respects_window(isolated_root: Path):
    _insert(isolated_root, slug="fresh", type_="decision", title="新", days_ago=2)
    _insert(isolated_root, slug="old", type_="decision", title="旧", days_ago=30)
    out = gather_signals(isolated_root, scope_hash="abc123", window_days=7)
    titles = [d["title"] for d in out["decisions"]]
    assert "新" in titles
    assert "旧" not in titles


def test_gather_signals_global_scope_aggregates(isolated_root: Path):
    _insert(isolated_root, slug="a", type_="decision", title="A", scope_hash="s1")
    _insert(isolated_root, slug="b", type_="decision", title="B", scope_hash="s2")
    out = gather_signals(isolated_root, scope_hash=None, window_days=7)
    titles = sorted(d["title"] for d in out["decisions"])
    assert titles == ["A", "B"]


# ---------- generate_handoff fallback path ----------


def test_generate_handoff_no_llm_renders_fallback(isolated_root: Path, tmp_path: Path):
    _insert(isolated_root, slug="d1", type_="decision", title="选 pnpm")
    _insert(isolated_root, slug="w1", type_="warning", title="Firefox bug")

    project = tmp_path / "my-proj"
    project.mkdir()

    result = generate_handoff(
        cwd=project,
        scope_hash="abc123",
        data_root=isolated_root,
        window_days=7,
        with_llm=False,
    )
    assert result["used_llm"] is False
    assert result["project_name"] == "my-proj"
    assert "HANDOFF — my-proj" in result["content"]
    assert "fallback 模板" in result["content"]
    assert "选 pnpm" in result["content"]
    assert "Firefox bug" in result["content"]


# ---------- generate_handoff LLM path ----------


_DEFAULT_STUB_RESPONSE = (
    "# HANDOFF — test (2026-05-24)\n\n"
    "## 1. TL;DR\nA fake response.\n\n"
    "## 2. 当前状态\n- ✅ done\n\n"
    "## 3. 下一步立即要做的事\n**优先级 1**: x\n\n"
    "## 4. 关键决策记录\n- foo → why\n\n"
    "## 5. 文件结构 / 入口\n- file.py\n\n"
    "## 6. 已知坑 / 待办\n- ⚠️ y\n"
)


class _StubProvider:
    """Fake LLM that captures the prompt and returns a canned 6-block body.

    Pass ``response=None`` to use the default canned 6-block response.
    Pass any string (including ``""`` or whitespace) verbatim — useful for
    testing the empty-response fallback path.
    """

    def __init__(self, response: str | None = None):
        self.response = _DEFAULT_STUB_RESPONSE if response is None else response
        self.captured: dict[str, str] = {}

    def complete(self, *, system: str, user: str, model: str | None = None) -> str:
        self.captured = {"system": system, "user": user}
        return self.response


def test_generate_handoff_uses_llm_when_provided(isolated_root: Path, tmp_path: Path):
    _insert(isolated_root, slug="d1", type_="decision", title="切 pnpm")
    project = tmp_path / "proj"
    project.mkdir()

    llm = _StubProvider()
    result = generate_handoff(
        cwd=project,
        scope_hash="abc123",
        data_root=isolated_root,
        with_llm=True,
        llm=llm,
    )
    assert result["used_llm"] is True
    assert "fake response" in result["content"]
    # Captured prompt: user payload includes decisions from sqlite
    assert "切 pnpm" in llm.captured["user"]
    # System prompt mandates 6 blocks + anti-patterns
    assert "6 区块" in llm.captured["system"]
    assert "反模式" in llm.captured["system"]


def test_generate_handoff_falls_back_when_llm_returns_empty(isolated_root: Path, tmp_path: Path):
    """LLM returning empty / whitespace must not silently overwrite HANDOFF with nothing."""
    project = tmp_path / "p"
    project.mkdir()
    for raw in ("", "   ", "\n\n", "\t"):
        result = generate_handoff(
            cwd=project,
            scope_hash="abc123",
            data_root=isolated_root,
            with_llm=True,
            llm=_StubProvider(response=raw),
        )
        assert result["used_llm"] is False, f"empty raw={raw!r} should fall back"
        assert "fallback 模板" in result["content"]


def test_generate_handoff_falls_back_when_llm_raises(isolated_root: Path, tmp_path: Path):
    class BoomProvider:
        def complete(self, **kw):
            raise RuntimeError("simulated outage")

    project = tmp_path / "p"
    project.mkdir()
    result = generate_handoff(
        cwd=project,
        scope_hash="abc123",
        data_root=isolated_root,
        with_llm=True,
        llm=BoomProvider(),
    )
    assert result["used_llm"] is False
    assert "fallback 模板" in result["content"]


def test_generate_handoff_strips_markdown_code_fence(isolated_root: Path, tmp_path: Path):
    """LLMs sometimes wrap output in ```markdown ... ``` despite instructions."""
    project = tmp_path / "p"
    project.mkdir()
    wrapped = (
        "```markdown\n"
        "# HANDOFF — test (2026-05-24)\n"
        "## 1. TL;DR\nx\n"
        "```\n"
    )
    result = generate_handoff(
        cwd=project,
        scope_hash="abc123",
        data_root=isolated_root,
        with_llm=True,
        llm=_StubProvider(response=wrapped),
    )
    assert result["content"].startswith("# HANDOFF")
    assert "```markdown" not in result["content"]


# ---------- prompt content ----------


def test_prompt_includes_six_blocks_and_anti_patterns():
    msgs = render_handoff_prompt(
        project_name="x", today_iso="2026-05-24",
        identity_snippet="", decisions=[], warnings=[], sessions=[], entities=[],
    )
    sys_msg = msgs[0].content
    # 6 blocks named
    for block in ("TL;DR", "当前状态", "下一步立即要做的事", "关键决策记录",
                  "文件结构", "已知坑"):
        assert block in sys_msg
    # Anti-patterns mentioned
    assert "反模式" in sys_msg
    # Output format directive
    assert "严格 markdown" in sys_msg


def test_prompt_user_block_lists_signals():
    msgs = render_handoff_prompt(
        project_name="myapp", today_iso="2026-05-24",
        identity_snippet="(画像)",
        decisions=[{"title": "选 pnpm", "created_at": "2026-05-20T00:00:00"}],
        warnings=[{"title": "Safari 限制", "created_at": "2026-05-21T00:00:00"}],
        sessions=[{"title": "工作日", "created_at": "2026-05-22T00:00:00"}],
        entities=[{"name": "Solid", "mention_count": 5}],
    )
    user = msgs[1].content
    assert "myapp" in user
    assert "选 pnpm" in user
    assert "Safari 限制" in user
    assert "Solid (5)" in user
