"""Tests for ``memoryd.mcp_server`` — tool registration + admin gating.

These tests inspect the fastmcp surface but don't exercise the actual tool
bodies (those are covered in ``test_mcp_tools.py``). Goals:

1. The agent tier exposes exactly the 13 expected tools (7 memory + 4
   session + 2 judge). Admin tools are absent.
2. Toggling ``MEMORYD_MCP_ADMIN=1`` (or passing ``include_admin=True``)
   adds the 6 admin tools, bringing the total to 19.
3. Tool parameter schemas line up with the spec — required vs optional
   args, correct types, no leaked Python internals.
4. ``main()`` boots the server cleanly with ``--transport=stdio`` (we
   stub the run call so no real I/O happens).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from memoryd.mcp_server import (
    ADMIN_TOOL_NAMES,
    build_server,
    is_admin_enabled,
    list_tool_names,
    list_tool_summaries,
    main,
)


# Expected tool tiers — keep this in sync with mcp_server.py docstring.
AGENT_TOOLS = {
    "mem_save",
    "mem_update",
    "mem_delete",
    "mem_get",
    "mem_search",
    "mem_context",
    "mem_timeline",
    "mem_session_start",
    "mem_session_end",
    "mem_session_summary",
    "mem_capture_passive",
    "mem_judge",
    "mem_compare",
}
ADMIN_TOOLS = set(ADMIN_TOOL_NAMES)
ALL_TOOLS = AGENT_TOOLS | ADMIN_TOOLS


# ---------------------------------------------------------------------------
# Admin gating
# ---------------------------------------------------------------------------


def test_is_admin_enabled_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORYD_MCP_ADMIN", raising=False)
    assert is_admin_enabled() is False
    monkeypatch.setenv("MEMORYD_MCP_ADMIN", "1")
    assert is_admin_enabled() is True
    monkeypatch.setenv("MEMORYD_MCP_ADMIN", "0")
    assert is_admin_enabled() is False


@pytest.mark.asyncio
async def test_agent_tier_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORYD_MCP_ADMIN", raising=False)
    mcp = build_server()
    names = set(await list_tool_names(mcp))
    assert names == AGENT_TOOLS
    # Admin tools must NOT be present.
    for name in ADMIN_TOOLS:
        assert name not in names


@pytest.mark.asyncio
async def test_admin_tier_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORYD_MCP_ADMIN", "1")
    mcp = build_server()
    names = set(await list_tool_names(mcp))
    assert names == ALL_TOOLS
    assert len(names) == 19


@pytest.mark.asyncio
async def test_explicit_include_admin_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caller-passed ``include_admin`` always wins, regardless of env."""
    monkeypatch.delenv("MEMORYD_MCP_ADMIN", raising=False)
    mcp = build_server(include_admin=True)
    names = set(await list_tool_names(mcp))
    assert ADMIN_TOOLS.issubset(names)

    monkeypatch.setenv("MEMORYD_MCP_ADMIN", "1")
    mcp2 = build_server(include_admin=False)
    names2 = set(await list_tool_names(mcp2))
    assert names2.isdisjoint(ADMIN_TOOLS)


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_tools_have_descriptions() -> None:
    mcp = build_server(include_admin=True)
    summaries = await list_tool_summaries(mcp)
    for s in summaries:
        assert s["description"], f"tool {s['name']} missing description"
        # Every tool gets a JSON schema with at least an `type: object` envelope.
        schema = s["parameters"]
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"


@pytest.mark.asyncio
async def test_mem_save_schema_required_args() -> None:
    mcp = build_server(include_admin=False)
    summaries = await list_tool_summaries(mcp)
    sch = next(s["parameters"] for s in summaries if s["name"] == "mem_save")
    props = sch.get("properties", {})
    # `content` is required; `type` / `scope` are optional with defaults.
    assert "content" in sch.get("required", [])
    assert "type" in props
    assert "scope" in props


@pytest.mark.asyncio
async def test_mem_search_schema_optional_filters() -> None:
    mcp = build_server(include_admin=False)
    summaries = await list_tool_summaries(mcp)
    sch = next(s["parameters"] for s in summaries if s["name"] == "mem_search")
    props = sch.get("properties", {})
    assert "query" in sch.get("required", [])
    # types + entity_ids accept list[str] (optional)
    assert "types" in props
    assert "entity_ids" in props
    assert "top_k" in props


@pytest.mark.asyncio
async def test_mem_judge_schema_required() -> None:
    mcp = build_server(include_admin=False)
    summaries = await list_tool_summaries(mcp)
    sch = next(s["parameters"] for s in summaries if s["name"] == "mem_judge")
    required = set(sch.get("required", []))
    assert {"new_text", "old_memory_id"}.issubset(required)


@pytest.mark.asyncio
async def test_mem_session_start_optional_args() -> None:
    mcp = build_server(include_admin=False)
    summaries = await list_tool_summaries(mcp)
    sch = next(s["parameters"] for s in summaries if s["name"] == "mem_session_start")
    # session_start has all-optional args (scope/source/title).
    assert sch.get("required", []) == []


@pytest.mark.asyncio
async def test_admin_tool_schemas_present_when_enabled() -> None:
    mcp = build_server(include_admin=True)
    summaries = {s["name"]: s for s in await list_tool_summaries(mcp)}
    # mem_merge_projects requires scope_a + scope_b
    merge_sch = summaries["mem_merge_projects"]["parameters"]
    assert {"scope_a", "scope_b"}.issubset(set(merge_sch.get("required", [])))
    # mem_doctor takes no required args
    doctor_sch = summaries["mem_doctor"]["parameters"]
    assert doctor_sch.get("required", []) == []
    # mem_save_prompt requires both name + content
    sp_sch = summaries["mem_save_prompt"]["parameters"]
    assert {"name", "content"}.issubset(set(sp_sch.get("required", [])))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def test_main_stdio_invokes_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """``memoryd-mcp`` should construct the server and call ``run()``."""
    monkeypatch.delenv("MEMORYD_MCP_ADMIN", raising=False)

    calls: dict[str, Any] = {}

    def fake_run(self, *args: Any, **kwargs: Any) -> None:
        calls["transport"] = kwargs.get("transport") or (args[0] if args else None)
        calls["self"] = self

    with patch("memoryd.mcp_server.FastMCP.run", new=fake_run):
        rc = main(["--transport", "stdio"])
    assert rc == 0
    # stdio transport is the default fastmcp call → `transport` arg is None
    # in our wrapper (we only pass transport for http). Either way, run was called.
    assert "self" in calls


def test_main_http_passes_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORYD_MCP_ADMIN", raising=False)
    captured: dict[str, Any] = {}

    def fake_run(self, *args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)
        if args:
            captured["transport"] = args[0]

    with patch("memoryd.mcp_server.FastMCP.run", new=fake_run):
        rc = main(["--transport", "http", "--port", "9999", "--host", "127.0.0.1"])
    assert rc == 0
    assert captured.get("transport") == "http"
    assert captured.get("port") == 9999
    assert captured.get("host") == "127.0.0.1"


def test_main_admin_flag_forces_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORYD_MCP_ADMIN", raising=False)
    captured_servers: list[Any] = []

    def fake_run(self, *args: Any, **kwargs: Any) -> None:
        captured_servers.append(self)

    with patch("memoryd.mcp_server.FastMCP.run", new=fake_run):
        rc = main(["--admin"])
    assert rc == 0
    assert captured_servers, "server should have been started"
    import asyncio
    names = set(asyncio.run(list_tool_names(captured_servers[0])))
    assert names == ALL_TOOLS
