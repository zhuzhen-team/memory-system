"""Cross-platform desktop notification + optional SMTP fallback.

`notify(title, body, config=None)` is best-effort:
- Try native GUI for the current platform.
- If SMTP config is enabled and complete, also send an email.
- Failures on either channel are logged but never raised.
"""
from __future__ import annotations

import logging
import os
import smtplib
import subprocess
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

from .platforms import detect

log = logging.getLogger(__name__)


@dataclass
class SMTPConfig:
    enabled: bool = False
    host: str = ""
    port: int = 587
    use_tls: bool = True
    from_addr: str = ""
    to_addr: str = ""
    username: str = ""
    password_env: str = ""  # name of env var holding the password

    def is_complete(self) -> bool:
        return bool(
            self.enabled
            and self.host
            and self.from_addr
            and self.to_addr
        )


def notify(title: str, body: str, smtp: Optional[SMTPConfig] = None) -> None:
    """Best-effort dual-channel notify; never raises."""
    _notify_native(title, body)
    if smtp is not None and smtp.is_complete():
        _notify_smtp(title, body, smtp)


def _notify_native(title: str, body: str) -> None:
    try:
        plat = detect()
    except Exception:
        log.warning("notify: unknown platform, skipping native")
        return
    try:
        if plat == "darwin":
            script = f'display notification "{_esc(body)}" with title "{_esc(title)}"'
            subprocess.run(["osascript", "-e", script], check=False, timeout=5)
        elif plat == "linux":
            r = subprocess.run(
                ["notify-send", title, body], check=False, timeout=5
            )
            if r.returncode != 0:
                log.info("notify-send unavailable; skipping")
        elif plat == "windows":
            ps = (
                "try { "
                "Import-Module BurntToast -ErrorAction Stop; "
                f"New-BurntToastNotification -Text '{_esc(title)}','{_esc(body)}' "
                "} catch { "
                f"msg * /TIME:60 '{_esc(title)}: {_esc(body)}' "
                "}"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                          check=False, timeout=10)
    except Exception as e:
        log.warning("notify native failed: %s", e)


def _notify_smtp(title: str, body: str, c: SMTPConfig) -> None:
    try:
        password = os.environ.get(c.password_env, "") if c.password_env else ""
        msg = EmailMessage()
        msg["Subject"] = title
        msg["From"] = c.from_addr
        msg["To"] = c.to_addr
        msg.set_content(body)
        with smtplib.SMTP(c.host, c.port, timeout=15) as s:
            if c.use_tls:
                s.starttls()
            if c.username and password:
                s.login(c.username, password)
            s.send_message(msg)
    except Exception as e:
        log.warning("notify smtp failed: %s", e)


def _esc(s: str) -> str:
    """Escape shell single-quotes / AppleScript double-quotes."""
    return s.replace("'", "'\\''").replace('"', '\\"')
