"""Tests for the SessionStart hook installer (Plan: SessionStart inject)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from memoryd.setup import (
    auto_install,
    install_cc_hook,
    install_cc_session_start_hook,
)


def test_install_session_start_hook_macos_writes_python_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    install_cc_session_start_hook(target_settings=settings)
    data = json.loads(settings.read_text())
    ss = data["hooks"]["SessionStart"]
    assert len(ss) == 1
    cmd = ss[0]["hooks"][0]["command"]
    assert "session-start.py" in cmd
    assert "python3" in cmd


def test_install_session_start_hook_windows_writes_powershell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    install_cc_session_start_hook(target_settings=settings)
    data = json.loads(settings.read_text())
    cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "session-start.ps1" in cmd
    assert "powershell" in cmd


def test_install_session_start_hook_replaces_prior_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-installing should not duplicate the SessionStart entry."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "SessionStart": [{
                "matcher": "*",
                "hooks": [{
                    "type": "command",
                    "command": "/old/path/session-start.py",
                }],
            }]
        }
    }))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    install_cc_session_start_hook(target_settings=settings)
    data = json.loads(settings.read_text())
    assert len(data["hooks"]["SessionStart"]) == 1
    cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "session-start.py" in cmd
    assert "/old/path" not in cmd


def test_install_session_start_hook_preserves_session_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Installing SessionStart must not disturb a pre-existing SessionEnd."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "SessionEnd": [{
                "matcher": "*",
                "hooks": [{"type": "command", "command": "/x/session-end.py"}],
            }],
        }
    }))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    install_cc_session_start_hook(target_settings=settings)
    data = json.loads(settings.read_text())
    assert "SessionEnd" in data["hooks"]
    assert "SessionStart" in data["hooks"]
    assert "/x/session-end.py" in data["hooks"]["SessionEnd"][0]["hooks"][0]["command"]


def test_install_cc_hook_with_session_start_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """install_cc_hook() should only do SessionEnd; SessionStart needs explicit flag."""
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    # SessionEnd-only installer
    install_cc_hook(target_settings=settings)
    data = json.loads(settings.read_text())
    assert "SessionEnd" in data["hooks"]
    assert "SessionStart" not in data["hooks"]


def test_auto_install_installs_both_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        "memoryd.setup.install_cron", MagicMock(return_value="/tmp/x.plist")
    )
    monkeypatch.setattr(
        "memoryd.setup.install_cc_hook", MagicMock(return_value="/tmp/settings.json")
    )
    monkeypatch.setattr(
        "memoryd.setup.install_cc_session_start_hook",
        MagicMock(return_value="/tmp/settings.json"),
    )
    out = auto_install()
    assert "cc_hook" in out
    assert "cc_session_start_hook" in out


def test_auto_install_captures_session_start_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """auto_install should record SessionStart errors without aborting."""
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        "memoryd.setup.install_cron", MagicMock(return_value="/tmp/x.plist")
    )
    monkeypatch.setattr(
        "memoryd.setup.install_cc_hook", MagicMock(return_value="/tmp/settings.json")
    )
    monkeypatch.setattr(
        "memoryd.setup.install_cc_session_start_hook",
        MagicMock(side_effect=RuntimeError("no settings dir")),
    )
    out = auto_install()
    assert "cc_session_start_hook_error" in out
    assert "no settings dir" in out["cc_session_start_hook_error"]
