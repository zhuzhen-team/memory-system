"""Tests for governance.audit module."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memoryd.governance.audit import (
    _hash_for_chain,
    _ZERO_PREV,
    append_event,
    audit_log_path,
    query_events,
    verify_chain,
)


@pytest.fixture(autouse=True)
def _isolated_audit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect audit storage to a temp directory for every test."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_path))


def test_append_event_first_line_prev_hash_zero():
    ev = append_event({"event_type": "test", "scope_hash": "abc123"})
    assert ev["prev_hash"] == _ZERO_PREV
    assert len(ev["prev_hash"]) == 64
    assert ev["prev_hash"] == "0" * 64


def test_append_event_chain_links():
    ev1 = append_event({"event_type": "first", "scope_hash": "aaa"})
    ev2 = append_event({"event_type": "second", "scope_hash": "bbb"})
    expected_prev_hash = _hash_for_chain(ev1)
    assert ev2["prev_hash"] == expected_prev_hash


def test_query_events_filters_by_scope():
    append_event({"event_type": "access_granted", "scope_hash": "scope_x"})
    append_event({"event_type": "access_denied", "scope_hash": "scope_y"})
    append_event({"event_type": "access_granted", "scope_hash": "scope_z"})

    results = query_events(scope_hash="scope_x")
    assert len(results) == 1
    assert results[0]["scope_hash"] == "scope_x"


def test_query_events_filters_by_since():
    ev1 = append_event({"event_type": "access_granted", "scope_hash": "abc", "ts": "2026-01-01T00:00:00+00:00"})
    ev2 = append_event({"event_type": "access_granted", "scope_hash": "def", "ts": "2026-01-02T00:00:00+00:00"})

    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    results = query_events(since=future)
    assert results == []


def test_verify_chain_detects_tampering():
    append_event({"event_type": "first", "scope_hash": "aaa"})
    append_event({"event_type": "second", "scope_hash": "bbb"})

    p = audit_log_path()
    lines = p.read_text(encoding="utf-8").splitlines()
    # Tamper with the first line: change scope_hash
    first = json.loads(lines[0])
    first["scope_hash"] = "TAMPERED"
    lines[0] = json.dumps(first, ensure_ascii=False)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    valid, broken_at = verify_chain()
    assert valid is False
    # The second line's prev_hash will no longer match the hash of the tampered first line
    assert broken_at == 2
