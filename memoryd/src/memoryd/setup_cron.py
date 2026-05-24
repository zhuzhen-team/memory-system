"""Cross-platform cron-style job install / uninstall.

Two job kinds:
- decay-sweep: daily at 03:00
- weekly-digest: Monday 09:00 (with --notify)
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import platforms


@dataclass
class CronSchedule:
    hour: int
    minute: int
    weekday: int | None = None  # 0=Sun … 6=Sat (launchd numbering); None for daily

    def to_systemd_oncalendar(self) -> str:
        if self.weekday is None:
            return f"*-*-* {self.hour:02d}:{self.minute:02d}:00"
        names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        return f"{names[self.weekday]} *-*-* {self.hour:02d}:{self.minute:02d}:00"


_JOBS = {
    "decay": {
        "label": "com.memoryd.decay-sweep",
        "schedule": CronSchedule(hour=3, minute=0),
        "macos_template": "launchd-decay.plist.j2",
        "linux_service": "systemd-decay.service.j2",
        "linux_timer": "systemd-decay.timer.j2",
        "windows_template": "windows-decay.xml.j2",
    },
    "digest": {
        "label": "com.memoryd.weekly-digest",
        "schedule": CronSchedule(hour=9, minute=0, weekday=1),  # Mon
        "macos_template": "launchd-digest.plist.j2",
        "linux_service": "systemd-digest.service.j2",
        "linux_timer": "systemd-digest.timer.j2",
        "windows_template": "windows-digest.xml.j2",
    },
    "weekly_identity": {
        # Plan 10: weekly LLM rewrite of profile/identity.md (Sunday 02:00)
        "label": "com.memoryd.weekly-identity",
        "schedule": CronSchedule(hour=2, minute=0, weekday=0),  # Sun
        "macos_template": "launchd-weekly-identity.plist.j2",
        "linux_service": "systemd-weekly-identity.service.j2",
        "linux_timer": "systemd-weekly-identity.timer.j2",
        "windows_template": "windows-weekly-identity.xml.j2",
    },
    "monthly_report": {
        # Plan 10: monthly profile evolution report (1st of month 04:00)
        # weekday left None so daily template path is taken; the actual
        # monthly cadence is encoded inside each template (launchd Day=1,
        # systemd OnCalendar=*-*-01, Windows ScheduleByMonth).
        "label": "com.memoryd.monthly-report",
        "schedule": CronSchedule(hour=4, minute=0),
        "macos_template": "launchd-monthly-report.plist.j2",
        "linux_service": "systemd-monthly-report.service.j2",
        "linux_timer": "systemd-monthly-report.timer.j2",
        "windows_template": "windows-monthly-report.xml.j2",
    },
    "sync_push": {
        # Daily cross-device sync. Runs `memoryd sync export` against the
        # configured sync.dir (a Dropbox/iCloud/Syncthing folder). 03:30
        # local time — sits just after decay-sweep so each push captures
        # any state changes from that pass.
        "label": "com.memoryd.sync-push",
        "schedule": CronSchedule(hour=3, minute=30),
        "macos_template": "launchd-sync-push.plist.j2",
        "linux_service": "systemd-sync-push.service.j2",
        "linux_timer": "systemd-sync-push.timer.j2",
        "windows_template": "windows-sync-push.xml.j2",
    },
}


def known_jobs() -> tuple[str, ...]:
    """Return the registered cron task keys (stable for help text / tests)."""
    return tuple(_JOBS.keys())


def _ctx(job_key: str) -> dict:
    spec = _JOBS[job_key]
    sch = spec["schedule"]
    bin_path = shutil.which("memoryd") or sys.executable
    data_root = os.environ.get(
        "MEMORYD_DATA_ROOT", str(Path.home() / ".local" / "share" / "memoryd")
    )
    log_dir = str(Path(data_root) / "logs")
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    ctx = dict(
        memoryd_bin=bin_path,
        data_root=data_root,
        log_dir=log_dir,
        hour=sch.hour,
        minute=sch.minute,
    )
    if sch.weekday is not None:
        ctx["weekday"] = sch.weekday
    return ctx


def install(job_key: str, *, register: bool = True) -> Path | tuple[Path, Path]:
    if job_key not in _JOBS:
        raise ValueError(f"unknown job: {job_key}")
    spec = _JOBS[job_key]
    label = spec["label"]
    ctx = _ctx(job_key)
    plat = platforms.detect()
    if plat == "darwin":
        from .platforms import macos
        path = macos.install_plist(spec["macos_template"], label, ctx=ctx)
        if register:
            macos.bootstrap(label)
        return path
    if plat == "linux":
        from .platforms import linux
        svc, tmr = linux.install_units(
            spec["linux_service"], spec["linux_timer"], label, ctx=ctx
        )
        if register:
            linux.enable_timer(label)
        return svc, tmr
    if plat == "windows":
        from .platforms import windows
        xml = windows.install_task(spec["windows_template"], label, ctx=ctx)
        if register:
            windows.register_task(label)
        return xml


def uninstall(job_key: str) -> None:
    spec = _JOBS[job_key]
    label = spec["label"]
    plat = platforms.detect()
    if plat == "darwin":
        from .platforms import macos
        macos.uninstall(label)
    elif plat == "linux":
        from .platforms import linux
        linux.uninstall(label)
    elif plat == "windows":
        from .platforms import windows
        windows.uninstall(label)
