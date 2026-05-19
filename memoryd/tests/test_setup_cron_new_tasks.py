"""Plan 10: ensure weekly_identity + monthly_report cron tasks are registered."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from memoryd import setup_cron
from memoryd.setup_cron import CronSchedule, _JOBS, known_jobs
from memoryd.templates import render


def test_known_jobs_includes_new_tasks():
    keys = known_jobs()
    assert "weekly_identity" in keys
    assert "monthly_report" in keys
    # Plus the two original ones.
    assert "decay" in keys
    assert "digest" in keys


def test_weekly_identity_schedule_sunday_02():
    sch: CronSchedule = _JOBS["weekly_identity"]["schedule"]
    assert sch.weekday == 0  # Sunday in launchd numbering
    assert sch.hour == 2
    assert sch.minute == 0


def test_monthly_report_schedule_04():
    sch: CronSchedule = _JOBS["monthly_report"]["schedule"]
    assert sch.hour == 4
    assert sch.minute == 0


def test_render_launchd_weekly_identity_contains_command():
    out = render(
        "launchd-weekly-identity.plist.j2",
        label="com.memoryd.weekly-identity",
        memoryd_bin="/usr/bin/memoryd",
        data_root="/tmp/d",
        log_dir="/tmp/d/logs",
        hour=2,
        minute=0,
        weekday=0,
    )
    assert "<string>profile</string>" in out
    assert "<string>rewrite</string>" in out
    assert "<integer>0</integer>" in out  # Sunday
    assert "<integer>2</integer>" in out  # 02:00


def test_render_launchd_monthly_report_contains_day1():
    out = render(
        "launchd-monthly-report.plist.j2",
        label="com.memoryd.monthly-report",
        memoryd_bin="/usr/bin/memoryd",
        data_root="/tmp/d",
        log_dir="/tmp/d/logs",
        hour=4,
        minute=0,
    )
    assert "<string>profile</string>" in out
    assert "<string>report</string>" in out
    assert "<string>--current-month</string>" in out
    assert "<key>Day</key>" in out
    assert "<integer>4</integer>" in out


def test_render_systemd_monthly_report_oncalendar():
    out = render(
        "systemd-monthly-report.timer.j2",
        label="com.memoryd.monthly-report",
        hour=4,
        minute=0,
    )
    assert "OnCalendar=*-*-01 04:00:00" in out


def test_render_systemd_weekly_identity_sunday():
    out = render(
        "systemd-weekly-identity.timer.j2",
        label="com.memoryd.weekly-identity",
        hour=2,
        minute=0,
    )
    assert "OnCalendar=Sun *-*-* 02:00:00" in out


def test_render_windows_weekly_identity_sunday():
    out = render(
        "windows-weekly-identity.xml.j2",
        label="com.memoryd.weekly-identity",
        memoryd_bin="C:\\memoryd.exe",
        data_root="C:\\d",
        log_dir="C:\\d\\logs",
        hour=2,
        minute=0,
        weekday=0,
    )
    assert "ScheduleByWeek" in out
    assert "<Sunday/>" in out
    assert "profile rewrite" in out


def test_render_windows_monthly_report_first_of_month():
    out = render(
        "windows-monthly-report.xml.j2",
        label="com.memoryd.monthly-report",
        memoryd_bin="C:\\memoryd.exe",
        data_root="C:\\d",
        log_dir="C:\\d\\logs",
        hour=4,
        minute=0,
    )
    assert "ScheduleByMonth" in out
    assert "<Day>1</Day>" in out
    assert "profile report --current-month" in out


# ---------------------------------------------------------------------------
# install() actually wires templates for the new keys.
# ---------------------------------------------------------------------------


def test_install_weekly_identity_macos(monkeypatch, tmp_path):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("subprocess.run", MagicMock(return_value=MagicMock(returncode=0)))
    monkeypatch.setattr("subprocess.check_output", MagicMock(return_value=b"500\n"))
    monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/memoryd")
    out = setup_cron.install("weekly_identity")
    assert "com.memoryd.weekly-identity" in str(out)
    assert out.exists()


def test_install_monthly_report_linux(monkeypatch, tmp_path):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("subprocess.run", MagicMock(return_value=MagicMock(returncode=0)))
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/memoryd")
    out = setup_cron.install("monthly_report")
    svc, tmr = out
    assert "monthly-report.service" in svc.name
    assert "monthly-report.timer" in tmr.name


# ---------------------------------------------------------------------------
# auto_install picks up new jobs.
# ---------------------------------------------------------------------------


def test_auto_install_invokes_new_jobs(monkeypatch):
    from memoryd import setup

    calls: list[str] = []

    def _fake_install(key: str):
        calls.append(key)
        return "/tmp/" + key

    monkeypatch.setattr(setup, "install_cron", _fake_install)
    monkeypatch.setattr(setup, "install_cc_hook", lambda: "/tmp/cc")
    monkeypatch.setattr("memoryd.platforms.detect", lambda: "darwin")
    out = setup.auto_install()
    assert "weekly_identity" in calls
    assert "monthly_report" in calls
    assert out["weekly_identity_cron"] == "/tmp/weekly_identity"
    assert out["monthly_report_cron"] == "/tmp/monthly_report"
