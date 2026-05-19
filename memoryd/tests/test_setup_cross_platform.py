"""Plan 5 Task 4 tests — install-cron / install-cc-hook / auto-install wiring."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from memoryd.setup import (
    auto_install,
    install_cc_hook,
    install_cron,
    uninstall_cron,
)


def test_install_cron_macos_invokes_setup_cron(monkeypatch, tmp_path):
    fake = MagicMock(return_value=tmp_path / "plist")
    monkeypatch.setattr("memoryd.setup_cron.install", fake)
    out = install_cron("decay")
    fake.assert_called_once_with("decay")
    assert out == tmp_path / "plist"


def test_uninstall_cron_delegates(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr("memoryd.setup_cron.uninstall", fake)
    uninstall_cron("digest")
    fake.assert_called_once_with("digest")


def test_install_cc_hook_writes_settings_macos(monkeypatch, tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {}}))
    # backup dir lives under Path.home(); redirect to tmp_path to avoid touching ~/.claude
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    install_cc_hook(target_settings=settings)
    data = json.loads(settings.read_text())
    se = data["hooks"]["SessionEnd"]
    assert len(se) == 1
    assert "session-end.py" in se[0]["hooks"][0]["command"]


def test_install_cc_hook_uses_ps1_on_windows(monkeypatch, tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    install_cc_hook(target_settings=settings)
    data = json.loads(settings.read_text())
    cmd = data["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
    assert "session-end.ps1" in cmd
    assert "powershell" in cmd


def test_install_cc_hook_replaces_prior_entry(monkeypatch, tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {
            "SessionEnd": [{
                "matcher": "*",
                "hooks": [{
                    "type": "command",
                    "command": "/old/cc-session-end-hook.sh",
                }],
            }]
        }
    }))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    install_cc_hook(target_settings=settings)
    data = json.loads(settings.read_text())
    # only one entry now, pointing at our wrapper
    assert len(data["hooks"]["SessionEnd"]) == 1
    cmd = data["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
    assert "session-end.py" in cmd
    assert "/old/cc-session-end-hook.sh" not in cmd


def test_auto_install_returns_results(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        "memoryd.setup.install_cron", MagicMock(return_value="/tmp/x.plist")
    )
    monkeypatch.setattr(
        "memoryd.setup.install_cc_hook", MagicMock(return_value="/tmp/settings.json")
    )
    out = auto_install()
    assert out["platform"] == "darwin"
    assert "decay_cron" in out
    assert "digest_cron" in out
    assert "cc_hook" in out


def test_auto_install_records_errors(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")

    def boom(*_):
        raise RuntimeError("no systemd")

    monkeypatch.setattr("memoryd.setup.install_cron", boom)
    monkeypatch.setattr(
        "memoryd.setup.install_cc_hook", MagicMock(return_value="/tmp/s.json")
    )
    out = auto_install()
    assert "decay_cron_error" in out
    assert "no systemd" in out["decay_cron_error"]
