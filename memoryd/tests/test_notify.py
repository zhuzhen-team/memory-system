from unittest.mock import MagicMock, patch

import pytest

from memoryd.notify import SMTPConfig, _notify_native, _notify_smtp, notify


def test_smtp_config_is_complete_requires_all():
    assert not SMTPConfig().is_complete()
    c = SMTPConfig(enabled=True, host="h", from_addr="a@b", to_addr="c@d")
    assert c.is_complete()
    assert not SMTPConfig(enabled=False, host="h", from_addr="a", to_addr="b").is_complete()


def test_native_macos_invokes_osascript(monkeypatch):
    run = MagicMock()
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("subprocess.run", run)
    _notify_native("hello", "world")
    run.assert_called_once()
    args = run.call_args.args[0]
    assert args[0] == "osascript"
    assert "world" in args[-1]
    assert "hello" in args[-1]


def test_native_linux_invokes_notify_send(monkeypatch):
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("subprocess.run", run)
    _notify_native("t", "b")
    args = run.call_args.args[0]
    assert args == ["notify-send", "t", "b"]


def test_native_windows_uses_powershell(monkeypatch):
    run = MagicMock()
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("subprocess.run", run)
    _notify_native("t", "b")
    args = run.call_args.args[0]
    assert args[0] == "powershell"
    assert "BurntToast" in args[-1]


def test_native_swallows_errors(monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("subprocess.run", MagicMock(side_effect=RuntimeError("boom")))
    # must not raise
    _notify_native("t", "b")


def test_smtp_sends(monkeypatch):
    smtp_inst = MagicMock()
    smtp_class = MagicMock(return_value=smtp_inst)
    smtp_inst.__enter__ = MagicMock(return_value=smtp_inst)
    smtp_inst.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("smtplib.SMTP", smtp_class)
    cfg = SMTPConfig(enabled=True, host="smtp.x", port=587, use_tls=True,
                     from_addr="me@x", to_addr="you@y",
                     username="u", password_env="PW")
    monkeypatch.setenv("PW", "secret")
    _notify_smtp("t", "b", cfg)
    smtp_inst.starttls.assert_called_once()
    smtp_inst.login.assert_called_once_with("u", "secret")
    smtp_inst.send_message.assert_called_once()


def test_smtp_swallows_errors(monkeypatch):
    monkeypatch.setattr("smtplib.SMTP", MagicMock(side_effect=ConnectionRefusedError()))
    cfg = SMTPConfig(enabled=True, host="x", from_addr="a", to_addr="b")
    # must not raise
    _notify_smtp("t", "b", cfg)


def test_notify_dispatches_both(monkeypatch):
    monkeypatch.setattr("memoryd.notify._notify_native", MagicMock())
    smtp = MagicMock()
    monkeypatch.setattr("memoryd.notify._notify_smtp", smtp)
    cfg = SMTPConfig(enabled=True, host="x", from_addr="a", to_addr="b")
    notify("t", "b", cfg)
    smtp.assert_called_once()


def test_notify_skips_smtp_when_disabled(monkeypatch):
    monkeypatch.setattr("memoryd.notify._notify_native", MagicMock())
    smtp = MagicMock()
    monkeypatch.setattr("memoryd.notify._notify_smtp", smtp)
    cfg = SMTPConfig(enabled=False)
    notify("t", "b", cfg)
    smtp.assert_not_called()
