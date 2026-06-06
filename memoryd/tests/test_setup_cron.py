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


def test_ctx_falls_back_to_interpreter_sibling_when_which_fails(monkeypatch, tmp_path):
    """memoryd off PATH must resolve to the console script next to the running
    interpreter — never to the bare interpreter. A plist rendered as
    ``python3 decay-sweep`` can't run (real incident: 2026-06-05, launchd
    exit 512 across all four governance jobs)."""
    monkeypatch.delenv("MEMORYD_DATA_ROOT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    fake_py = venv_bin / "python3"
    fake_py.touch()
    sibling = venv_bin / "memoryd"
    sibling.touch()
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("sys.executable", str(fake_py))
    ctx = _ctx("decay")
    assert ctx["memoryd_bin"] == str(sibling)


def test_ctx_raises_when_memoryd_not_findable(monkeypatch, tmp_path):
    """Neither PATH nor interpreter-sibling: fail loudly instead of silently
    rendering a broken plist."""
    monkeypatch.delenv("MEMORYD_DATA_ROOT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    lone_py = tmp_path / "python3"
    lone_py.touch()
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("sys.executable", str(lone_py))
    with pytest.raises(RuntimeError):
        _ctx("decay")


def test_ctx_passes_proxy_env_through(monkeypatch, tmp_path):
    """LLM-backed jobs (weekly identity / monthly report) spawn `claude -p`,
    which 403s on this network without the proxy. launchd provides no shell
    env, so the installer must pass the proxies through into the plist
    (real incident 2026-06-05: monthly-report exit 1)."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv("MEMORYD_DATA_ROOT", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/memoryd")
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    ctx = _ctx("decay")
    assert ctx["extra_env"]["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert ctx["extra_env"]["NO_PROXY"] == "localhost,127.0.0.1"
    assert "HTTP_PROXY" not in ctx["extra_env"]


def test_macos_plist_renders_extra_env(monkeypatch, tmp_path):
    out = render(
        "launchd-monthly-report.plist.j2",
        label="com.memoryd.monthly-report",
        memoryd_bin="/usr/bin/memoryd",
        data_root="/tmp/d",
        log_dir="/tmp/l",
        hour=4,
        minute=0,
        extra_env={"HTTPS_PROXY": "http://127.0.0.1:7897"},
    )
    assert "<key>HTTPS_PROXY</key><string>http://127.0.0.1:7897</string>" in out


def test_macos_plist_renders_without_extra_env_for_old_callers(monkeypatch):
    out = render(
        "launchd-monthly-report.plist.j2",
        label="com.memoryd.monthly-report",
        memoryd_bin="/usr/bin/memoryd",
        data_root="/tmp/d",
        log_dir="/tmp/l",
        hour=4,
        minute=0,
    )
    assert "HTTPS_PROXY" not in out
    assert "<key>MEMORYD_DATA_ROOT</key>" in out


def test_macos_plist_extra_env_values_are_xml_escaped():
    """Authenticated proxies often contain '&' in credentials; unescaped it
    yields a malformed plist that launchd silently refuses to load — the
    exact silent-failure shape this audit set out to eliminate."""
    import plistlib

    out = render(
        "launchd-monthly-report.plist.j2",
        label="com.memoryd.monthly-report",
        memoryd_bin="/usr/bin/memoryd",
        data_root="/tmp/d",
        log_dir="/tmp/l",
        hour=4,
        minute=0,
        extra_env={"HTTPS_PROXY": "http://user:p&w<d@127.0.0.1:7897"},
    )
    parsed = plistlib.loads(out.encode("utf-8"))  # raises on malformed XML
    assert parsed["EnvironmentVariables"]["HTTPS_PROXY"] == "http://user:p&w<d@127.0.0.1:7897"


def test_cli_install_cron_reports_runtime_error_cleanly(monkeypatch, capsys):
    """When memoryd is unfindable, _ctx raises RuntimeError on purpose — the
    CLI must surface the actionable message + nonzero exit, not a traceback."""
    import argparse

    from memoryd import cli

    def boom(_key):
        raise RuntimeError("memoryd binary not found: test message")

    monkeypatch.setattr("memoryd.setup.install_cron", boom)
    args = argparse.Namespace(
        task=None, all=False, decay=True, digest=False,
        weekly_identity=False, monthly_report=False, sync_push=False,
    )
    rc = cli._cmd_install_cron(args)
    assert rc != 0
    err = capsys.readouterr().err
    assert "memoryd binary not found: test message" in err
