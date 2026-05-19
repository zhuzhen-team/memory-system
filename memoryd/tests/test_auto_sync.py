"""Plan 6 Task 6: auto-export/import wiring + 5min throttle.

Covers:
- cli._maybe_auto_import(): respects [sync] gates + 5min marker throttle
- cli._cmd_sync_export(--auto): silent no-op when not opted in; runs when opted in
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

from memoryd import cli


class _Sync:
    def __init__(
        self,
        *,
        enabled: bool = False,
        dir: str = "/tmp/sync",
        auto_export_on_session_end: bool = False,
        auto_import_on_session_start: bool = False,
    ) -> None:
        self.enabled = enabled
        self.dir = dir
        self.auto_export_on_session_end = auto_export_on_session_end
        self.auto_import_on_session_start = auto_import_on_session_start


class _Cfg:
    def __init__(self, sync: _Sync) -> None:
        self.sync = sync


def _patch_cfg(monkeypatch, sync: _Sync) -> None:
    monkeypatch.setattr("memoryd.config.load_config", lambda: _Cfg(sync))


def test_maybe_auto_import_no_op_when_disabled(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, _Sync(enabled=False, auto_import_on_session_start=False))
    fake_popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cli._maybe_auto_import()
    fake_popen.assert_not_called()


def test_maybe_auto_import_no_op_when_only_sync_enabled(monkeypatch, tmp_path):
    """sync.enabled=True but auto_import flag off must not fork."""
    _patch_cfg(monkeypatch, _Sync(enabled=True, auto_import_on_session_start=False))
    fake_popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cli._maybe_auto_import()
    fake_popen.assert_not_called()


def test_maybe_auto_import_forks_when_enabled(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, _Sync(enabled=True, auto_import_on_session_start=True))
    fake_popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/memoryd")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cli._maybe_auto_import()
    fake_popen.assert_called_once()
    args = fake_popen.call_args.args[0]
    assert "sync" in args and "import" in args and "--auto" in args


def test_maybe_auto_import_throttles_within_5_min(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, _Sync(enabled=True, auto_import_on_session_start=True))
    fake_popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/memoryd")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # pre-create fresh marker
    marker = tmp_path / ".local" / "share" / "memoryd" / "last_import_at"
    marker.parent.mkdir(parents=True)
    marker.touch()
    cli._maybe_auto_import()
    fake_popen.assert_not_called()


def test_maybe_auto_import_runs_after_5_min(monkeypatch, tmp_path):
    _patch_cfg(monkeypatch, _Sync(enabled=True, auto_import_on_session_start=True))
    fake_popen = MagicMock()
    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/memoryd")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    marker = tmp_path / ".local" / "share" / "memoryd" / "last_import_at"
    marker.parent.mkdir(parents=True)
    marker.touch()
    old = marker.stat().st_mtime - 400  # 400s ago > 300s threshold
    os.utime(marker, (old, old))
    cli._maybe_auto_import()
    fake_popen.assert_called_once()


def test_sync_export_auto_flag_no_op_when_disabled(monkeypatch, tmp_path):
    """--auto flag must silently skip when [sync] not opted-in (exit 0, no work)."""
    _patch_cfg(monkeypatch, _Sync(enabled=False, auto_export_on_session_end=False))
    fake_export = MagicMock()
    monkeypatch.setattr("memoryd.sync.export", fake_export)
    args = type("A", (), {"auto": True, "scope": None, "dry_run": False})()
    rc = cli._cmd_sync_export(args)
    assert rc == 0
    fake_export.assert_not_called()


def test_sync_export_auto_flag_runs_when_enabled(monkeypatch, tmp_path):
    sync = _Sync(
        enabled=True,
        dir=str(tmp_path / "sync"),
        auto_export_on_session_end=True,
    )
    _patch_cfg(monkeypatch, sync)
    fake_export = MagicMock()
    fake_export.return_value = MagicMock(copied=0, skipped=0, dry_run=False)
    monkeypatch.setattr("memoryd.sync.export", fake_export)
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path / "data")
    args = type("A", (), {"auto": True, "scope": None, "dry_run": False})()
    rc = cli._cmd_sync_export(args)
    assert rc == 0
    fake_export.assert_called_once()


def test_sync_import_auto_flag_no_op_when_disabled(monkeypatch, tmp_path):
    """Mirror behavior for import --auto."""
    _patch_cfg(monkeypatch, _Sync(enabled=False, auto_import_on_session_start=False))
    fake_import = MagicMock()
    monkeypatch.setattr("memoryd.sync.import_", fake_import)
    args = type("A", (), {"auto": True, "scope": None, "dry_run": False})()
    rc = cli._cmd_sync_import(args)
    assert rc == 0
    fake_import.assert_not_called()
