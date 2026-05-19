"""Linux systemd user unit helpers."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..templates import render


def units_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def install_units(service_template: str, timer_template: str, label: str,
                  *, ctx: dict) -> tuple[Path, Path]:
    units_dir().mkdir(parents=True, exist_ok=True)
    svc = units_dir() / f"{label}.service"
    tmr = units_dir() / f"{label}.timer"
    svc.write_text(render(service_template, label=label, **ctx), encoding="utf-8")
    tmr.write_text(render(timer_template, label=label, **ctx), encoding="utf-8")
    return svc, tmr


def enable_timer(label: str) -> None:
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", f"{label}.timer"],
                   check=True)


def uninstall(label: str) -> None:
    subprocess.run(["systemctl", "--user", "disable", "--now", f"{label}.timer"],
                   capture_output=True, check=False)
    for suffix in (".timer", ".service"):
        f = units_dir() / f"{label}{suffix}"
        if f.exists():
            f.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, check=False)
