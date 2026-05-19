from unittest.mock import MagicMock, patch

import pytest

from memoryd.setup_cron import CronSchedule, _JOBS, _ctx, install, uninstall
from memoryd.templates import render


def test_cron_schedule_systemd_daily():
    assert CronSchedule(3, 0).to_systemd_oncalendar() == "*-*-* 03:00:00"


def test_cron_schedule_systemd_weekly_monday():
    assert CronSchedule(9, 0, weekday=1).to_systemd_oncalendar() == "Mon *-*-* 09:00:00"


def test_render_launchd_decay_contains_hour():
    out = render(
        "launchd-decay.plist.j2",
        label="com.memoryd.decay-sweep",
        memoryd_bin="/usr/bin/memoryd",
        data_root="/tmp/d",
        log_dir="/tmp/d/logs",
        hour=3,
        minute=0,
    )
    assert "<integer>3</integer>" in out
    assert "decay-sweep" in out
    assert "com.memoryd.decay-sweep" in out


def test_render_launchd_digest_contains_weekday():
    out = render(
        "launchd-digest.plist.j2",
        label="com.memoryd.weekly-digest",
        memoryd_bin="/usr/bin/memoryd",
        data_root="/tmp/d",
        log_dir="/tmp/d/logs",
        hour=9,
        minute=0,
        weekday=1,
    )
    assert "<integer>1</integer>" in out
    assert "digest" in out
    assert "--notify" in out


def test_render_systemd_timer_daily_oncalendar():
    out = render(
        "systemd-decay.timer.j2",
        label="com.memoryd.decay-sweep",
        hour=3, minute=0,
    )
    assert "OnCalendar=*-*-* 03:00:00" in out


def test_render_systemd_digest_timer_monday():
    out = render(
        "systemd-digest.timer.j2",
        label="com.memoryd.weekly-digest",
        hour=9, minute=0,
    )
    assert "OnCalendar=Mon *-*-* 09:00:00" in out


def test_render_windows_decay_daily():
    out = render(
        "windows-decay.xml.j2",
        label="com.memoryd.decay-sweep",
        memoryd_bin="C:\\memoryd\\memoryd.exe",
        data_root="C:\\m",
        log_dir="C:\\m\\logs",
        hour=3, minute=0,
    )
    assert "ScheduleByDay" in out
    assert "T03:00:00" in out


def test_render_windows_digest_weekly_monday():
    out = render(
        "windows-digest.xml.j2",
        label="com.memoryd.weekly-digest",
        memoryd_bin="C:\\memoryd\\memoryd.exe",
        data_root="C:\\m",
        log_dir="C:\\m\\logs",
        hour=9, minute=0,
        weekday=1,
    )
    assert "ScheduleByWeek" in out
    assert "Monday" in out
    assert "--notify" in out


def test_install_macos_writes_plist_and_bootstraps(monkeypatch, tmp_path):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    fake_run = MagicMock(return_value=MagicMock(returncode=0, stdout=b"500"))
    fake_co = MagicMock(return_value=b"500\n")
    monkeypatch.setattr("subprocess.check_output", fake_co)
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/memoryd")
    out = install("decay")
    assert out.exists()
    assert "com.memoryd.decay-sweep.plist" in str(out)
    # bootstrap called
    assert any("bootstrap" in (" ".join(c.args[0]) if isinstance(c.args[0], list) else "")
               for c in fake_run.call_args_list)


def test_install_linux_writes_units_and_enables(monkeypatch, tmp_path):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    fake_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/memoryd")
    out = install("digest")
    svc, tmr = out
    assert svc.exists() and tmr.exists()
    assert "weekly-digest.service" in svc.name
    assert "weekly-digest.timer" in tmr.name


def test_install_windows_writes_xml_utf16(monkeypatch, tmp_path):
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    fake_run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("shutil.which", lambda _: "C:\\memoryd.exe")
    out = install("decay")
    assert out.exists()
    head = out.read_bytes()[:2]
    assert head == b"\xff\xfe"  # UTF-16 LE BOM
