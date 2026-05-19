"""Tests for ``memoryd.mcp_tools.*`` handlers.

These tests exercise the handlers as plain async functions (no MCP
machinery) so we can:

- monkeypatch the LLM / hybrid_search / sqlite stack via ``MEMORYD_DATA_ROOT``;
- assert the response envelopes (``{ok, ...}`` / ``{ok: False, error}``);
- verify that admin-only handlers behave correctly even though they would
  normally be hidden behind the MCP gate.

Conftest already provides a ``memory_root`` (tmp_path-backed) fixture.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from memoryd.mcp_tools import admin as admin_tools
from memoryd.mcp_tools import judge as judge_tools
from memoryd.mcp_tools import memory as memory_tools
from memoryd.mcp_tools import session as session_tools
from memoryd.mcp_tools import util as tool_util
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_memory


SCOPE = "test_scope_aa11"


@pytest.fixture(autouse=True)
def _data_root(memory_root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point handlers at the temp memory_root for every test in this file."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(memory_root))
    monkeypatch.setenv("MEMORYD_DEFAULT_SCOPE", SCOPE)
    return memory_root


def _make_memory(
    memory_root: Path,
    slug: str,
    *,
    type_: str = "session",
    title: str = "test",
    body: str = "hello world\n",
    triggers: tuple[str, ...] = (),
    scope: str = SCOPE,
    created_at: datetime | None = None,
) -> Path:
    """Helper to drop a markdown memory + index row at a known slug."""
    mem = SessionMemory(
        frontmatter=Frontmatter(
            title=title,
            slug=slug,
            type=type_,
            scope_hash=scope,
            triggers=list(triggers),
            source="test",
            created_at=created_at or datetime(2026, 5, 18, 12, 0),
            ttl_days=None if type_ != "session" else 7,
        ),
        body=body,
    )
    return save_memory(memory_root, mem)


# ---------------------------------------------------------------------------
# util
# ---------------------------------------------------------------------------


def test_util_ok_fail_envelopes() -> None:
    assert tool_util.ok(x=1) == {"ok": True, "x": 1}
    fail = tool_util.fail("nope", code="bad", hint="check")
    assert fail["ok"] is False
    assert fail["error"]["code"] == "bad"
    assert fail["error"]["message"] == "nope"
    assert fail["error"]["hint"] == "check"


def test_util_derive_scope_passthrough() -> None:
    assert tool_util.derive_scope("explicit_hash") == "explicit_hash"


def test_util_derive_scope_falls_back_to_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No .git anywhere + MEMORYD_DEFAULT_SCOPE set → env wins."""
    monkeypatch.setenv("MEMORYD_DEFAULT_SCOPE", "env_scope")
    out = tool_util.derive_scope("auto", cwd=tmp_path)
    assert out == "env_scope"


def test_util_safe_slug_is_unique_and_safe() -> None:
    s = tool_util.safe_slug("Hello / World!!")
    # Format: YYYY-MM-DD-{sanitised}-{unix-ts}. Only [A-Za-z0-9_-] is allowed,
    # trailing underscores trimmed so the slug doesn't look like padding.
    import re
    m = re.match(r"^(\d{4}-\d{2}-\d{2})-([A-Za-z0-9_-]+)-(\d+)$", s)
    assert m is not None, f"slug {s!r} doesn't match expected shape"
    assert "/" not in s
    assert "!" not in s


# ---------------------------------------------------------------------------
# memory tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mem_save_persists_and_indexes(memory_root: Path) -> None:
    out = await memory_tools.save(
        content="项目 logo 方向：深蓝+银灰\n第二行细节",
        type="decision",
        scope=SCOPE,
        triggers=["logo", "wolin"],
        tags=["design"],
    )
    assert out["ok"] is True
    assert out["scope_hash"] == SCOPE
    assert out["type"] == "decision"
    assert Path(out["path"]).exists()

    # Round-trip via mem_get
    got = await memory_tools.get(out["memory_id"])
    assert got["ok"] is True
    assert "深蓝" in got["body"]
    assert got["row"]["type"] == "decision"


@pytest.mark.asyncio
async def test_mem_save_rejects_empty_content() -> None:
    out = await memory_tools.save(content="", type="fact")
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_argument"


@pytest.mark.asyncio
async def test_mem_save_rejects_unknown_type() -> None:
    out = await memory_tools.save(content="something", type="bogus")
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_argument"
    assert "allowed" in out["error"]


@pytest.mark.asyncio
async def test_mem_update_patches_fields(memory_root: Path) -> None:
    _make_memory(memory_root, "to-update", body="old body\n", triggers=("a",))
    out = await memory_tools.update(
        "to-update",
        content="new body",
        triggers=["x", "y"],
        title="renamed",
    )
    assert out["ok"] is True
    got = await memory_tools.get("to-update")
    assert "new body" in got["body"]
    assert got["frontmatter"]["title"] == "renamed"
    assert got["frontmatter"]["triggers"] == ["x", "y"]


@pytest.mark.asyncio
async def test_mem_update_missing_returns_not_found() -> None:
    out = await memory_tools.update("does-not-exist", content="x")
    assert out["ok"] is False
    assert out["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_mem_delete_removes_file_and_index(memory_root: Path) -> None:
    path = _make_memory(memory_root, "to-delete", body="bye\n")
    out = await memory_tools.delete("to-delete")
    assert out["ok"] is True
    assert not path.exists()
    # second delete returns not_found
    out2 = await memory_tools.delete("to-delete")
    assert out2["ok"] is False
    assert out2["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_mem_get_unknown_id() -> None:
    out = await memory_tools.get("nope")
    assert out["ok"] is False
    assert out["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_mem_search_calls_hybrid(memory_root: Path) -> None:
    """``mem_search`` should delegate to hybrid_search and pass scope through."""
    captured: dict[str, Any] = {}

    class FakeHit:
        memory_id = "hit-1"
        chunk_id = "c0"
        content = "matched"
        score = 0.9
        source = "vector"
        heading = ""
        start_line = 0
        end_line = 1
        metadata: dict[str, Any] = {}

    def fake_hybrid(query, scope_hash, **kw):
        captured["query"] = query
        captured["scope_hash"] = scope_hash
        captured["kwargs"] = kw
        return [FakeHit()]

    with patch("memoryd.search.hybrid.hybrid_search", side_effect=fake_hybrid):
        out = await memory_tools.search("design", scope=SCOPE, top_k=5)
    assert out["ok"] is True
    assert captured["query"] == "design"
    assert captured["scope_hash"] == SCOPE
    assert captured["kwargs"]["top_k"] == 5
    assert out["hits"][0]["memory_id"] == "hit-1"
    assert out["hits"][0]["score"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_mem_search_filters_types(memory_root: Path) -> None:
    _make_memory(memory_root, "want", type_="decision", body="x\n")
    _make_memory(memory_root, "skip", type_="session", body="y\n")

    class Hit:
        def __init__(self, mid: str) -> None:
            self.memory_id = mid
            self.chunk_id = ""
            self.content = ""
            self.score = 0.5
            self.source = ""
            self.heading = ""
            self.start_line = 0
            self.end_line = 0
            self.metadata: dict[str, Any] = {}

    with patch("memoryd.search.hybrid.hybrid_search", return_value=[Hit("want"), Hit("skip")]):
        out = await memory_tools.search("q", scope=SCOPE, types=["decision"])
    assert out["ok"] is True
    assert [h["memory_id"] for h in out["hits"]] == ["want"]


@pytest.mark.asyncio
async def test_mem_search_empty_query_returns_empty() -> None:
    out = await memory_tools.search("   ", scope=SCOPE)
    assert out["ok"] is True
    assert out["hits"] == []


@pytest.mark.asyncio
async def test_mem_context_returns_neighbors(memory_root: Path) -> None:
    for slug, day in [
        ("first", 10),
        ("second", 11),
        ("anchor", 12),
        ("fourth", 13),
        ("fifth", 14),
    ]:
        _make_memory(
            memory_root, slug,
            created_at=datetime(2026, 5, day),
            body=f"{slug} body\n",
        )
    out = await memory_tools.context("anchor", neighbors=2)
    assert out["ok"] is True
    before_slugs = [r["slug"] for r in out["before"]]
    after_slugs = [r["slug"] for r in out["after"]]
    assert before_slugs == ["second", "first"]
    assert after_slugs == ["fourth", "fifth"]


@pytest.mark.asyncio
async def test_mem_timeline_window_filter(memory_root: Path) -> None:
    from datetime import timedelta, timezone
    now = datetime.now(timezone.utc)
    _make_memory(
        memory_root, "recent",
        body="recent\n",
        created_at=now - timedelta(days=2),
    )
    _make_memory(
        memory_root, "ancient",
        body="ancient\n",
        created_at=now - timedelta(days=60),
    )
    out = await memory_tools.timeline(scope=SCOPE, since="7d")
    assert out["ok"] is True
    slugs = [e["slug"] for e in out["entries"]]
    assert "recent" in slugs
    assert "ancient" not in slugs


@pytest.mark.asyncio
async def test_mem_timeline_type_filter(memory_root: Path) -> None:
    _make_memory(memory_root, "a-session", type_="session", body="s\n")
    _make_memory(memory_root, "a-decision", type_="decision", body="d\n")
    out = await memory_tools.timeline(scope=SCOPE, since="30d", types=["decision"])
    assert out["ok"] is True
    slugs = [e["slug"] for e in out["entries"]]
    assert slugs == ["a-decision"]


# ---------------------------------------------------------------------------
# session tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_creates_session(memory_root: Path) -> None:
    out = await session_tools.session_start(scope=SCOPE, source="test")
    assert out["ok"] is True
    sid = out["session_id"]
    assert (memory_root / "scopes" / SCOPE / "sessions" / f"{sid}.md").exists()


@pytest.mark.asyncio
async def test_session_end_appends_summary(memory_root: Path) -> None:
    started = await session_tools.session_start(scope=SCOPE)
    sid = started["session_id"]
    ended = await session_tools.session_end(sid, summary="we finished")
    assert ended["ok"] is True
    got = await session_tools.session_summary(sid)
    assert "we finished" in got["summary"]
    assert "Session ended at" in got["summary"]


@pytest.mark.asyncio
async def test_session_end_rejects_wrong_type(memory_root: Path) -> None:
    _make_memory(memory_root, "not-a-session", type_="fact", body="x\n")
    out = await session_tools.session_end("not-a-session", summary="hi")
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_argument"


@pytest.mark.asyncio
async def test_capture_passive_writes_long_term(memory_root: Path) -> None:
    out = await session_tools.capture_passive(
        content="user prefers tabs over spaces",
        source="codex",
        scope=SCOPE,
        type="preference",
    )
    assert out["ok"] is True
    assert out["type"] == "preference"
    got = await memory_tools.get(out["memory_id"])
    assert "tabs" in got["body"]


@pytest.mark.asyncio
async def test_capture_passive_rejects_session_type() -> None:
    out = await session_tools.capture_passive(
        content="x",
        source="x",
        type="session",
    )
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_argument"


# ---------------------------------------------------------------------------
# judge tools
# ---------------------------------------------------------------------------


class _MockJudgmentResult:
    """Return value from a mocked ``generate_json`` call."""

    def __init__(self, **payload: Any) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return dict(self._payload)


@pytest.fixture
def mock_llm(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub ``get_llm`` so judge_tools doesn't touch real providers."""
    captured: dict[str, Any] = {}

    class FakeLLM:
        name = "mock"
        model = "mock-1"

        async def generate(self, messages, **kw):
            captured["messages"] = messages
            captured["kw"] = kw
            return "mock_topic"

        async def generate_json(self, messages, schema, **kw):
            captured["judge_messages"] = messages
            return _MockJudgmentResult(
                candidate_old_id="anchor",
                is_superseded=True,
                confidence=0.92,
                reason="LLM mock says yes",
            )

    def fake_get_llm(*args, **kwargs):
        return FakeLLM()

    monkeypatch.setattr("memoryd.llm.get_llm", fake_get_llm)
    return captured


@pytest.mark.asyncio
async def test_mem_judge_high_confidence(memory_root: Path, mock_llm: dict[str, Any]) -> None:
    _make_memory(memory_root, "anchor", type_="preference", body="prefers vim\n")
    out = await judge_tools.judge(
        new_text="actually now prefers zed",
        old_memory_id="anchor",
    )
    assert out["ok"] is True
    assert out["band"] == "auto"
    assert out["judgment"]["is_superseded"] is True
    assert out["judgment"]["confidence"] == pytest.approx(0.92)
    # Make sure prompt was constructed.
    assert "judge_messages" in mock_llm


@pytest.mark.asyncio
async def test_mem_judge_falls_back_on_llm_failure(memory_root: Path, monkeypatch) -> None:
    _make_memory(memory_root, "anchor2", body="x\n")

    def raise_get_llm(*args, **kw):
        raise RuntimeError("no key")
    monkeypatch.setattr("memoryd.llm.get_llm", raise_get_llm)

    out = await judge_tools.judge(new_text="new", old_memory_id="anchor2")
    assert out["ok"] is True
    assert out["judgment"]["is_superseded"] is False
    assert out["judgment"]["llm_available"] is False
    assert out["band"] == "ignore"


@pytest.mark.asyncio
async def test_mem_judge_missing_old() -> None:
    out = await judge_tools.judge(new_text="x", old_memory_id="no-such")
    assert out["ok"] is False
    assert out["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_mem_compare_diff_and_judgment(memory_root: Path, mock_llm) -> None:
    _make_memory(memory_root, "old-mem", body="line1\nline2\nold\n")
    _make_memory(memory_root, "new-mem", body="line1\nline2\nnew\n")
    out = await judge_tools.compare("old-mem", "new-mem")
    assert out["ok"] is True
    assert out["a"]["memory_id"] == "old-mem"
    assert out["b"]["memory_id"] == "new-mem"
    # Diff should include the two diverging lines
    assert any("-old" in line for line in out["diff_lines"])
    assert any("+new" in line for line in out["diff_lines"])
    assert out["judgment"]["is_superseded"] is True


# ---------------------------------------------------------------------------
# admin tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mem_stats_aggregates(memory_root: Path) -> None:
    _make_memory(memory_root, "s1", type_="session", body="a\n")
    _make_memory(memory_root, "s2", type_="session", body="b\n")
    _make_memory(memory_root, "d1", type_="decision", body="c\n")
    out = await admin_tools.stats()
    assert out["ok"] is True
    assert out["total"] == 3
    assert out["by_type"]["session"] == 2
    assert out["by_type"]["decision"] == 1


@pytest.mark.asyncio
async def test_mem_stats_scoped(memory_root: Path) -> None:
    _make_memory(memory_root, "s1", scope=SCOPE, body="a\n")
    _make_memory(memory_root, "s2", scope="other_scope", body="b\n")
    out = await admin_tools.stats(scope=SCOPE)
    assert out["ok"] is True
    assert out["total"] == 1


@pytest.mark.asyncio
async def test_mem_merge_projects_dry_run(memory_root: Path) -> None:
    _make_memory(memory_root, "x", scope="scope_b", body="x\n")
    out = await admin_tools.merge_projects("scope_a", "scope_b", dry_run=True)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["affected"] == 1
    # No actual write happened — DB still shows scope_b for the row.
    conn = tool_util.open_db()
    try:
        row = conn.execute("SELECT scope_hash FROM memories WHERE slug = 'x'").fetchone()
    finally:
        conn.close()
    assert row["scope_hash"] == "scope_b"


@pytest.mark.asyncio
async def test_mem_merge_projects_executes(memory_root: Path) -> None:
    _make_memory(memory_root, "y", scope="scope_b", body="y\n")
    out = await admin_tools.merge_projects("scope_a", "scope_b", dry_run=False)
    assert out["ok"] is True
    assert out["affected"] == 1
    conn = tool_util.open_db()
    try:
        row = conn.execute("SELECT scope_hash FROM memories WHERE slug = 'y'").fetchone()
    finally:
        conn.close()
    assert row["scope_hash"] == "scope_a"


@pytest.mark.asyncio
async def test_mem_merge_projects_validation() -> None:
    out = await admin_tools.merge_projects("same", "same")
    assert out["ok"] is False
    assert out["error"]["code"] == "invalid_argument"


@pytest.mark.asyncio
async def test_mem_current_project_with_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    out = await admin_tools.current_project(cwd=str(repo))
    assert out["ok"] is True
    assert out["source"] == "git"
    assert out["scope_root"] == str(repo)


@pytest.mark.asyncio
async def test_mem_current_project_env_fallback(tmp_path: Path, monkeypatch) -> None:
    """No git anywhere + env set → source=env."""
    monkeypatch.setenv("MEMORYD_DEFAULT_SCOPE", "env-x")
    out = await admin_tools.current_project(cwd=str(tmp_path))
    assert out["ok"] is True
    assert out["source"] == "env"
    assert out["scope_hash"] == "env-x"


@pytest.mark.asyncio
async def test_mem_doctor_runs(memory_root: Path) -> None:
    # At minimum, doctor should run end-to-end and produce a checks dict.
    out = await admin_tools.doctor()
    assert out["ok"] is True
    assert "checks" in out
    assert isinstance(out["healthy"], bool)
    # data_root probe should be ok (memory_root is created by fixture)
    assert out["checks"]["data_root"]["ok"] is True


@pytest.mark.asyncio
async def test_mem_save_prompt_writes_file(memory_root: Path) -> None:
    out = await admin_tools.save_prompt("my-prompt", "Hello\n")
    assert out["ok"] is True
    saved = memory_root / "prompts" / "my-prompt.md"
    assert saved.exists()
    assert saved.read_text(encoding="utf-8") == "Hello\n"


@pytest.mark.asyncio
async def test_mem_save_prompt_validation() -> None:
    out = await admin_tools.save_prompt("", "x")
    assert out["ok"] is False
    out2 = await admin_tools.save_prompt("x", "")
    assert out2["ok"] is False


@pytest.mark.asyncio
async def test_mem_suggest_topic_key_uses_llm(monkeypatch) -> None:
    class FakeLLM:
        name = "fake"
        model = "fake-1"
        async def generate(self, messages, **kw):
            return "  My_Key  \n"

    monkeypatch.setattr("memoryd.llm.get_llm", lambda *a, **k: FakeLLM())
    out = await admin_tools.suggest_topic_key("about implementing X")
    assert out["ok"] is True
    assert out["topic_key"] == "my_key"
    assert out["source"] == "llm"


@pytest.mark.asyncio
async def test_mem_suggest_topic_key_falls_back_to_heuristic(monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("no llm")
    monkeypatch.setattr("memoryd.llm.get_llm", boom)
    out = await admin_tools.suggest_topic_key("design system overhaul plan")
    assert out["ok"] is True
    assert out["source"] == "heuristic"
    assert out["topic_key"].startswith("design_")


@pytest.mark.asyncio
async def test_mem_suggest_topic_key_empty_rejected() -> None:
    out = await admin_tools.suggest_topic_key("   ")
    assert out["ok"] is False
