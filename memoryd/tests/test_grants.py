"""Tests for governance.grants module."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memoryd.governance.grants import (
    is_grant_valid,
    read_grant,
    revoke_grant,
    write_grant,
)


@pytest.fixture(autouse=True)
def _isolated_grants_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect grants storage to a temp directory for every test."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_path))


_HASH = "aabbccdd1234"
_ROOT = "/tmp/test-scope"
_NOW = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)


def test_write_then_read_roundtrip():
    g = write_grant(_HASH, _ROOT, "session", task_id="t1", now=_NOW)
    g2 = read_grant(_HASH)
    assert g2 is not None
    assert g2 == g
    assert g2["scope_hash"] == _HASH
    assert g2["scope_root"] == _ROOT
    assert g2["duration"] == "session"
    assert g2["task_id"] == "t1"


def test_once_expires_in_90s():
    g = write_grant(_HASH, _ROOT, "once", now=_NOW)
    issued = datetime.fromisoformat(g["issued_at"])
    expires = datetime.fromisoformat(g["expires_at"])
    delta = (expires - issued).total_seconds()
    assert abs(delta - 90) < 1


def test_session_expires_in_8h():
    g = write_grant(_HASH, _ROOT, "session", now=_NOW)
    issued = datetime.fromisoformat(g["issued_at"])
    expires = datetime.fromisoformat(g["expires_at"])
    delta = (expires - issued).total_seconds()
    assert abs(delta - 8 * 3600) < 1


def test_task_never_expires_until_revoked():
    g = write_grant(_HASH, _ROOT, "task", now=_NOW)
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert is_grant_valid(g, now=far_future) is True
    # Revoke and confirm gone
    assert revoke_grant(_HASH) is True
    assert read_grant(_HASH) is None


def test_is_grant_valid_after_expiry():
    g = write_grant(_HASH, _ROOT, "once", now=_NOW)
    # Advance mock now by 91 seconds — past the 90s window
    later = _NOW + timedelta(seconds=91)
    assert is_grant_valid(g, now=later) is False
