"""MCP server tests (in-process).

API adaptation note (mcp v1.27.1):
  FastMCP.call_tool(name, arguments) returns a TUPLE:
    (content_blocks: list[ContentBlock], structured: dict)
  - content_blocks: list of TextContent items, one JSON blob per SearchResult hit
  - structured:     {'result': [<raw dicts>]}  (empty list when no hits)
  The spec example treated result as a flat sequence; we unpack the tuple instead.
"""
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.schema import Frontmatter, SessionMemory
from memoryd.server import build_server
from memoryd.storage import save_session


@pytest.fixture
def server_with_data(memory_root: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a memoryd MCP server pointed at a temp memory root with sample data."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(memory_root))

    s = SessionMemory(
        frontmatter=Frontmatter(
            title="logo 讨论",
            slug="2026-05-09-logo",
            type="session",
            scope_hash="test_scope",
            triggers=["logo", "wolin"],
            source="claude-code",
            created_at=datetime(2026, 5, 9),
        ),
        body="深蓝+银灰方向已定。\n",
    )
    save_session(memory_root, s)

    return build_server()


@pytest.mark.asyncio
async def test_search_memory_returns_matching_session(server_with_data):
    server = server_with_data
    # mcp v1.27.1: call_tool returns (content_blocks, structured_dict)
    content_blocks, structured = await server.call_tool(
        "search_memory", {"query": "深蓝", "scope_hash": "test_scope"}
    )
    # Flatten all text to a single blob for assertion
    text_blob = "".join(str(item) for item in content_blocks)
    # The title "logo 讨论" should appear in the JSON output
    assert "logo 讨论" in text_blob


@pytest.mark.asyncio
async def test_search_memory_empty_when_no_match(server_with_data):
    server = server_with_data
    # mcp v1.27.1: call_tool returns (content_blocks, structured_dict)
    content_blocks, structured = await server.call_tool(
        "search_memory",
        {"query": "不存在的关键词xyz", "scope_hash": "test_scope"},
    )
    # No hits → content_blocks is empty list; structured['result'] is []
    assert content_blocks == []
    assert structured.get("result") == []


@pytest.mark.asyncio
async def test_search_memory_raises_when_no_scope(memory_root: Path, monkeypatch: pytest.MonkeyPatch):
    """No scope_hash arg + no MEMORYD_DEFAULT_SCOPE env → ValueError."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(memory_root))
    monkeypatch.delenv("MEMORYD_DEFAULT_SCOPE", raising=False)

    server = build_server()

    with pytest.raises(Exception) as exc_info:
        await server.call_tool("search_memory", {"query": "anything"})
    # FastMCP may wrap ValueError in its own error type; assert "scope_hash" appears in message
    assert "scope_hash" in str(exc_info.value)


@pytest.mark.asyncio
async def test_record_long_term_creates_decision(server_with_data):
    server = server_with_data
    result = await server.call_tool("record_long_term", {
        "type": "decision",
        "title": "logo choice",
        "body": "deep blue",
        "triggers": ["logo", "color"],
        "scope_hash": "test_scope",
    })
    assert any("logo choice" in str(item) for item in result)


@pytest.mark.asyncio
async def test_list_by_type_filters(server_with_data):
    server = server_with_data
    # record one first
    await server.call_tool("record_long_term", {
        "type": "fact",
        "title": "stack is FastAPI",
        "body": "the API runs FastAPI",
        "triggers": ["stack", "fastapi"],
        "scope_hash": "test_scope",
    })
    result = await server.call_tool("list_by_type", {"type": "fact", "scope_hash": "test_scope"})
    blob = "".join(str(item) for item in result)
    assert "stack is FastAPI" in blob


@pytest.mark.asyncio
async def test_get_memory_returns_known_slug(server_with_data):
    server = server_with_data
    # The fixture data has slug "2026-05-09-logo"
    result = await server.call_tool("get_memory", {"slug": "2026-05-09-logo"})
    assert any("logo" in str(item).lower() for item in result)


@pytest.mark.asyncio
async def test_list_promotions_returns_empty_initially(server_with_data):
    server = server_with_data
    result = await server.call_tool("list_promotions", {"scope_hash": "test_scope"})
    # No promotions yet → empty list
    blob = "".join(str(item) for item in result)
    assert blob in ("", "[]") or "[]" in blob
