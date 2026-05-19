"""macOS launchd helpers for cron-style jobs."""
from __future__ import annotations

import subprocess
from pathlib import Path

from ..templates import render


def launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def install_plist(template: str, label: str, *, ctx: dict) -> Path:
    text = render(template, label=label, **ctx)
    out = launch_agents_dir() / f"{label}.plist"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out


def bootstrap(label: str) -> None:
    """Best-effort launchctl bootstrap."""
    plist = launch_agents_dir() / f"{label}.plist"
    uid = subprocess.check_output(["id", "-u"]).decode().strip()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                   capture_output=True, check=False)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
                   check=True)


def uninstall(label: str) -> None:
    plist = launch_agents_dir() / f"{label}.plist"
    uid = subprocess.check_output(["id", "-u"]).decode().strip()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                   capture_output=True, check=False)
    if plist.exists():
        plist.unlink()
