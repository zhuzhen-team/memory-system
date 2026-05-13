"""Search tests."""
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.schema import Frontmatter, SessionMemory
from memoryd.search import SearchHit, search_sessions
from memoryd.storage import save_session


@pytest.fixture
def populated_root(memory_root: Path) -> Path:
    sessions = [
        SessionMemory(
            frontmatter=Frontmatter(
                title="logo 讨论",
                slug="2026-05-09-logo",
                type="session",
                scope_hash="scope_a",
                triggers=["logo", "wolin"],
                source="claude-code",
                created_at=datetime(2026, 5, 9),
            ),
            body="深蓝+银灰方向\n",
        ),
        SessionMemory(
            frontmatter=Frontmatter(
                title="API 调试",
                slug="2026-05-08-api",
                type="session",
                scope_hash="scope_a",
                triggers=["stripe", "webhook"],
                source="claude-code",
                created_at=datetime(2026, 5, 8),
            ),
            body="stripe webhook 排错\n",
        ),
        SessionMemory(
            frontmatter=Frontmatter(
                title="不相关项目",
                slug="2026-05-07-other",
                type="session",
                scope_hash="scope_other",
                triggers=["other"],
                source="claude-code",
                created_at=datetime(2026, 5, 7),
            ),
            body="其他项目话题\n",
        ),
    ]
    for s in sessions:
        save_session(memory_root, s)
    return memory_root


def test_search_finds_match_in_body(populated_root: Path):
    hits = search_sessions(populated_root, scope_hash="scope_a", query="深蓝")
    assert len(hits) == 1
    assert hits[0].title == "logo 讨论"


def test_search_finds_match_in_triggers(populated_root: Path):
    hits = search_sessions(populated_root, scope_hash="scope_a", query="stripe")
    assert len(hits) == 1
    assert hits[0].title == "API 调试"


def test_search_filters_by_scope(populated_root: Path):
    """Searching scope_a should not return scope_other matches."""
    hits = search_sessions(populated_root, scope_hash="scope_a", query="项目")
    titles = [h.title for h in hits]
    assert "不相关项目" not in titles


def test_search_returns_empty_for_no_match(populated_root: Path):
    hits = search_sessions(populated_root, scope_hash="scope_a", query="不存在的关键词xyz123")
    assert hits == []


def test_search_hit_includes_path_and_excerpt(populated_root: Path):
    hits = search_sessions(populated_root, scope_hash="scope_a", query="深蓝")
    h = hits[0]
    assert isinstance(h, SearchHit)
    assert h.path.suffix == ".md"
    assert "深蓝" in h.excerpt


def test_search_respects_limit_parameter(memory_root: Path):
    """Limit truncates results across files."""
    for i in range(5):
        save_session(
            memory_root,
            SessionMemory(
                frontmatter=Frontmatter(
                    title=f"会话 {i}",
                    slug=f"2026-05-09-session-{i}",
                    type="session",
                    scope_hash="scope_l",
                    triggers=[],
                    source="claude-code",
                    created_at=datetime(2026, 5, 9),
                ),
                body="共享关键词 SHARED_KW\n",
            ),
        )
    hits = search_sessions(memory_root, scope_hash="scope_l", query="SHARED_KW", limit=2)
    assert len(hits) == 2


def test_search_skips_corrupt_files(memory_root: Path, sample_session: SessionMemory):
    """Files that fail to parse as SessionMemory are skipped, not raised."""
    save_session(memory_root, sample_session)
    # Drop a corrupt .md file alongside the good one
    from memoryd.storage import _sessions_dir
    corrupt = _sessions_dir(memory_root, sample_session.frontmatter.scope_hash) / "corrupt.md"
    corrupt.write_text("not a valid memory file\nno frontmatter\n", encoding="utf-8")

    hits = search_sessions(
        memory_root,
        scope_hash=sample_session.frontmatter.scope_hash,
        query="logo",  # matches sample_session
    )
    # Should find the good session, ignoring the corrupt one
    assert len(hits) == 1
    assert hits[0].title == sample_session.frontmatter.title
