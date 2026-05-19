"""Smoke test for `memoryd audit` CLI subcommand (Task 8)."""
from __future__ import annotations

import argparse
import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from memoryd.governance.audit import append_event
from memoryd.cli import cmd_audit


@pytest.fixture(autouse=True)
def _isolated_audit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect audit storage to a temp directory for every test."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_path))


def _make_audit_args(
    scope=None,
    since=None,
    event_type=None,
    json_output=False,
) -> argparse.Namespace:
    return argparse.Namespace(
        scope=scope,
        since=since,
        event_type=event_type,
        json=json_output,
    )


def test_cli_audit_smoke_table(capsys):
    """Write 2 audit events, run cmd_audit in table mode, verify both appear in stdout."""
    append_event({
        "event_type": "access_denied",
        "scope_hash": "aabbccdd1234",
        "tool": "search_memory",
        "result": "denied",
        "ts": "2026-05-14T10:00:00+00:00",
    })
    append_event({
        "event_type": "grant_issued",
        "scope_hash": "aabbccdd1234",
        "tool": None,
        "result": "ok",
        "ts": "2026-05-14T10:01:00+00:00",
    })

    args = _make_audit_args()
    rc = cmd_audit(args)

    assert rc == 0
    captured = capsys.readouterr()
    out = captured.out

    # Both events must appear as rows
    assert "access_denied" in out
    assert "grant_issued" in out
    # scope_hash prefix appears in both rows
    assert "aabbccdd1234" in out


def test_cli_audit_smoke_json(capsys):
    """Write 2 audit events, run cmd_audit --json, verify JSON output contains both."""
    append_event({
        "event_type": "sensitive_marked",
        "scope_hash": "deadbeef9999",
        "ts": "2026-05-14T09:00:00+00:00",
    })
    append_event({
        "event_type": "access_granted",
        "scope_hash": "deadbeef9999",
        "tool": "get_memory",
        "result": "ok",
        "ts": "2026-05-14T09:05:00+00:00",
    })

    args = _make_audit_args(json_output=True)
    rc = cmd_audit(args)

    assert rc == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)
    assert len(data) == 2
    event_types = {e["event_type"] for e in data}
    assert "sensitive_marked" in event_types
    assert "access_granted" in event_types


def test_cli_audit_filter_by_event_type(capsys):
    """--event-type filter should return only matching events."""
    append_event({"event_type": "access_denied", "scope_hash": "s1"})
    append_event({"event_type": "grant_issued", "scope_hash": "s1"})
    append_event({"event_type": "access_denied", "scope_hash": "s1"})

    args = _make_audit_args(event_type="grant_issued", json_output=True)
    rc = cmd_audit(args)

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 1
    assert data[0]["event_type"] == "grant_issued"
