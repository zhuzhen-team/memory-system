"""Tests for `memoryd audit --verify` (Plan 10)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from memoryd.cli import cmd_audit
from memoryd.governance.audit import append_event, audit_log_path


@pytest.fixture(autouse=True)
def _isolated_audit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_path))


def _args(**kw) -> argparse.Namespace:
    base = dict(
        scope=None,
        since=None,
        event_type=None,
        json=False,
        verify=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_audit_verify_ok_on_empty(capsys):
    rc = cmd_audit(_args(verify=True))
    err = capsys.readouterr().err
    assert rc == 0
    assert "OK" in err


def test_audit_verify_ok_after_append(capsys):
    append_event({"event_type": "first", "scope_hash": "a"})
    append_event({"event_type": "second", "scope_hash": "b"})

    rc = cmd_audit(_args(verify=True))
    err = capsys.readouterr().err
    assert rc == 0
    assert "OK" in err


def test_audit_verify_detects_tamper(capsys):
    append_event({"event_type": "first", "scope_hash": "a"})
    append_event({"event_type": "second", "scope_hash": "b"})

    # Corrupt the first event so the chain hash for #2 no longer matches.
    p = audit_log_path()
    lines = p.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["scope_hash"] = "TAMPERED"
    lines[0] = json.dumps(first, ensure_ascii=False)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rc = cmd_audit(_args(verify=True))
    err = capsys.readouterr().err
    assert rc == 1
    assert "BROKEN" in err


def test_audit_verify_json_output(capsys):
    append_event({"event_type": "x", "scope_hash": "a"})
    rc = cmd_audit(_args(verify=True, json=True))
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert data["valid"] is True
    assert data["first_broken_line"] == -1
    assert "audit_log_path" in data
