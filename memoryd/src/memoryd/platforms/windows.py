"""Windows Task Scheduler helpers."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..templates import render


def task_xml_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "memoryd"


def install_task(template: str, label: str, *, ctx: dict) -> Path:
    task_xml_dir().mkdir(parents=True, exist_ok=True)
    xml = render(template, label=label, **ctx)
    out = task_xml_dir() / f"{label}.xml"
    # Task Scheduler requires UTF-16 LE BOM
    out.write_bytes(b"\xff\xfe" + xml.encode("utf-16-le"))
    return out


def register_task(label: str) -> None:
    xml = task_xml_dir() / f"{label}.xml"
    subprocess.run(
        ["schtasks", "/Create", "/TN", label, "/XML", str(xml), "/F"],
        check=True,
    )


def uninstall(label: str) -> None:
    subprocess.run(["schtasks", "/Delete", "/TN", label, "/F"],
                   capture_output=True, check=False)
    xml = task_xml_dir() / f"{label}.xml"
    if xml.exists():
        xml.unlink()
