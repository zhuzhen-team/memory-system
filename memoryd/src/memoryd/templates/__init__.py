"""Jinja2 environment for cron / unit / plist templates packaged with memoryd."""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


def _template_dir() -> Path:
    return Path(str(files("memoryd").joinpath("templates")))


def render(template_name: str, **ctx) -> str:
    env = Environment(
        loader=FileSystemLoader(_template_dir()),
        keep_trailing_newline=True,
        autoescape=False,
    )
    return env.get_template(template_name).render(**ctx)
