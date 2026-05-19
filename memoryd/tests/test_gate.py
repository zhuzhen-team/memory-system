"""Tests for governance.gate module."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from memoryd.governance.gate import AuthorizationRequired, check_or_raise
from memoryd.governance.grants import write_grant
from memoryd.index import open_index


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect all storage to tmp_path and clear interactive flag."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("MEMORYD_AUTH_INTERACTIVE", raising=False)


@pytest.fixture
def memory_root(tmp_path: Path) -> Path:
    return tmp_path


def _make_sensitive(memory_root: Path, scope_hash: str, scope_root: str = "/tmp/scope") -> None:
    """Register scope_hash as sensitive in the index DB."""
    idx = open_index(memory_root / "index.db")
    try:
        idx.register_sensitive_scope(scope_hash, scope_root)
    finally:
        idx.close()


def test_check_or_raise_passes_when_scope_not_sensitive(memory_root):
    # scope_hash not registered as sensitive -> no-op, no exception
    check_or_raise("deadbeef1234", "search_memory", memory_root=memory_root)


def test_check_or_raise_raises_when_no_grant(memory_root):
    _make_sensitive(memory_root, "cafebabe5678", "/tmp/secret")
    with pytest.raises(AuthorizationRequired, match="requires grant"):
        check_or_raise("cafebabe5678", "search_memory", memory_root=memory_root)


def test_check_or_raise_passes_with_valid_grant(memory_root):
    scope_hash = "11223344aabb"
    _make_sensitive(memory_root, scope_hash, "/tmp/private")
    write_grant(scope_hash, "/tmp/private", "once")
    # Should not raise
    check_or_raise(scope_hash, "search_memory", memory_root=memory_root)


def test_check_or_raise_writes_audit_on_grant_and_deny(memory_root, monkeypatch):
    from memoryd.governance.audit import query_events, audit_log_path

    scope_granted = "aabb1122ccdd"
    scope_denied = "eeff5566aabb"

    _make_sensitive(memory_root, scope_granted, "/tmp/granted")
    _make_sensitive(memory_root, scope_denied, "/tmp/denied")

    # Grant for scope_granted
    write_grant(scope_granted, "/tmp/granted", "session")
    check_or_raise(scope_granted, "read_memory", memory_root=memory_root)

    # scope_denied has no grant
    with pytest.raises(AuthorizationRequired):
        check_or_raise(scope_denied, "read_memory", memory_root=memory_root)

    granted_events = query_events(scope_hash=scope_granted, event_type="access_granted")
    denied_events = query_events(scope_hash=scope_denied, event_type="access_denied")
    assert len(granted_events) == 1
    assert granted_events[0]["result"] == "ok"
    assert len(denied_events) == 1
    assert denied_events[0]["result"] == "denied"


def test_interactive_prompt_returns_none_without_tty(memory_root, monkeypatch):
    """When MEMORYD_AUTH_INTERACTIVE is not set, raise AuthorizationRequired (no tty path taken)."""
    scope_hash = "99887766aabb"
    _make_sensitive(memory_root, scope_hash, "/tmp/interactive-scope")
    # MEMORYD_AUTH_INTERACTIVE not set (cleared by autouse fixture)
    with pytest.raises(AuthorizationRequired):
        check_or_raise(scope_hash, "search_memory", memory_root=memory_root)
