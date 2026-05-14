"""Server gate-interception tests for sensitive scopes (Plan 4, Task 7).

Tests:
  1. search_memory blocked on sensitive scope with no grant → tool error/exception
  2. search_memory passes with a valid grant
  3. request_sensitive_read returns {granted: False} without grant (non-interactive)
  4. request_sensitive_read returns {granted: True} with an existing valid grant
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.schema import Frontmatter, SessionMemory
from memoryd.server import build_server
from memoryd.storage import save_session


SENSITIVE_SCOPE = "sen_scope_001"


@pytest.fixture
def sensitive_server(memory_root: Path, monkeypatch: pytest.MonkeyPatch):
    """MCP server with a sensitive scope registered and sample data written."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(memory_root))
    monkeypatch.delenv("MEMORYD_AUTH_INTERACTIVE", raising=False)

    # Write a sample session under the sensitive scope
    s = SessionMemory(
        frontmatter=Frontmatter(
            title="secret project",
            slug="2026-05-14-secret",
            type="session",
            scope_hash=SENSITIVE_SCOPE,
            triggers=["secret", "finance"],
            source="claude-code",
            created_at=datetime(2026, 5, 14),
        ),
        body="Top-secret content here.\n",
    )
    save_session(memory_root, s)

    # Register the scope as sensitive in SQLite
    from memoryd.index import open_index
    idx = open_index(memory_root / "index.db")
    try:
        idx.register_sensitive_scope(SENSITIVE_SCOPE, "/fake/sensitive/root")
    finally:
        idx.close()

    return build_server()


# ---------------------------------------------------------------------------
# Test 1: search_memory blocked on sensitive scope with no grant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_memory_blocked_on_sensitive_no_grant(
    sensitive_server, memory_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """search_memory on a sensitive scope with no grant must raise / return error."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(memory_root))

    # Ensure no grant file exists for this scope
    grants_dir = memory_root / "grants"
    grant_file = grants_dir / f"{SENSITIVE_SCOPE}.json"
    if grant_file.exists():
        grant_file.unlink()

    with pytest.raises(Exception) as exc_info:
        await sensitive_server.call_tool(
            "search_memory", {"query": "secret", "scope_hash": SENSITIVE_SCOPE}
        )
    err = str(exc_info.value)
    # Must mention grant or authorization denial
    assert "grant" in err.lower() or "AuthorizationRequired" in err or "sensitive" in err.lower()


# ---------------------------------------------------------------------------
# Test 2: search_memory passes with a valid grant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_memory_passes_with_grant(
    sensitive_server, memory_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """search_memory succeeds after a valid once-grant is issued."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(memory_root))

    # Write a valid grant for this scope
    from memoryd.governance.grants import write_grant
    write_grant(SENSITIVE_SCOPE, "/fake/sensitive/root", "once")

    content_blocks, structured = await sensitive_server.call_tool(
        "search_memory", {"query": "secret", "scope_hash": SENSITIVE_SCOPE}
    )
    text_blob = "".join(str(item) for item in content_blocks)
    assert "secret project" in text_blob


# ---------------------------------------------------------------------------
# Test 3: request_sensitive_read returns {granted: False} without grant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_sensitive_read_returns_false_without_grant(
    sensitive_server, memory_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """No grant + MEMORYD_AUTH_INTERACTIVE not set → granted: False."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(memory_root))
    monkeypatch.delenv("MEMORYD_AUTH_INTERACTIVE", raising=False)

    # Ensure no grant
    grant_file = memory_root / "grants" / f"{SENSITIVE_SCOPE}.json"
    if grant_file.exists():
        grant_file.unlink()

    # Use a scope_path that will hash to SENSITIVE_SCOPE via scope_hash(resolve_scope_root(...))
    # We build a tiny temp directory that doesn't contain .git so resolve_scope_root returns it
    # and then we patch scope_hash to return SENSITIVE_SCOPE for our path.
    # Simpler: just monkeypatch the scope functions inside server to return our test hash.
    scope_dir = tmp_path / "fake_scope"
    scope_dir.mkdir()

    import memoryd.server as _srv_mod
    original_build = _srv_mod.build_server

    # Patch at the governance gate level: monkeypatch check_or_raise to see SENSITIVE_SCOPE
    # More direct: patch scope.resolve_scope_root + scope.scope_hash in server context
    from memoryd import scope as _scope_mod
    monkeypatch.setattr(_scope_mod, "resolve_scope_root", lambda p: scope_dir)
    monkeypatch.setattr(_scope_mod, "scope_hash", lambda p: SENSITIVE_SCOPE)

    # request_sensitive_read returns dict → FastMCP yields a plain list of TextContent
    result = await sensitive_server.call_tool(
        "request_sensitive_read",
        {"scope_path": str(scope_dir), "query": "want to read finance data", "duration": "once"},
    )
    blob = "".join(str(item) for item in (result[0] if isinstance(result, tuple) else result))
    # granted should be False
    assert "false" in blob.lower() or '"granted": false' in blob.lower()


# ---------------------------------------------------------------------------
# Test 4: request_sensitive_read returns {granted: True} with existing grant
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_sensitive_read_returns_true_with_existing_grant(
    sensitive_server, memory_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Existing valid grant → granted: True."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(memory_root))

    # Write a valid grant
    from memoryd.governance.grants import write_grant
    write_grant(SENSITIVE_SCOPE, "/fake/sensitive/root", "session")

    scope_dir = tmp_path / "fake_scope2"
    scope_dir.mkdir()

    from memoryd import scope as _scope_mod
    monkeypatch.setattr(_scope_mod, "resolve_scope_root", lambda p: scope_dir)
    monkeypatch.setattr(_scope_mod, "scope_hash", lambda p: SENSITIVE_SCOPE)

    # request_sensitive_read returns dict → FastMCP yields a plain list of TextContent
    result = await sensitive_server.call_tool(
        "request_sensitive_read",
        {"scope_path": str(scope_dir), "query": "want to read finance data", "duration": "session"},
    )
    blob = "".join(str(item) for item in (result[0] if isinstance(result, tuple) else result))
    assert "true" in blob.lower() or '"granted": true' in blob.lower()
