"""User-level memoryd config at ~/.config/memoryd/config.toml.

Schema (minimal v0.3 — Plan 3):
    [llm]
    provider = "anthropic"      # anthropic | openai | openrouter | local
    model = "claude-haiku-4-5"
    api_key_env = "ANTHROPIC_API_KEY"
    request_timeout_sec = 30

    [prompts]
    dura_extract = ""           # path override; empty → use bundled
"""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "request_timeout_sec": 30,
    },
    "prompts": {
        "dura_extract": "",
    },
}


def get_config_path() -> Path:
    home = os.environ.get("MEMORYD_CONFIG_HOME")
    base = Path(home) if home else (Path.home() / ".config" / "memoryd")
    return base / "config.toml"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _merge_dict(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_config() -> dict[str, Any]:
    p = get_config_path()
    if not p.exists():
        return dict(DEFAULT_CONFIG)
    parsed = tomllib.loads(p.read_text(encoding="utf-8"))
    return _merge_dict(DEFAULT_CONFIG, parsed)


def show_config() -> dict[str, Any]:
    return load_config()


def _render_toml(data: dict[str, Any]) -> str:
    """Hand-rolled minimal TOML writer (stdlib has no writer)."""
    lines: list[str] = []
    # Top-level scalars first (none in our schema)
    for k, v in data.items():
        if isinstance(v, dict):
            continue
        lines.append(f"{k} = {json.dumps(v)}")
    for section, body in data.items():
        if not isinstance(body, dict):
            continue
        lines.append(f"\n[{section}]")
        for k, v in body.items():
            lines.append(f"{k} = {json.dumps(v)}")
    return "\n".join(lines) + "\n"


def set_config_key(key_path: str, value: Any) -> None:
    """`key_path` like `llm.provider`; raises on bare keys (no dot)."""
    if "." not in key_path:
        raise ValueError(f"invalid key path (need at least one dot): {key_path!r}")
    parts = key_path.split(".")
    cfg = load_config()
    cursor = cfg
    for p in parts[:-1]:
        if p not in cursor or not isinstance(cursor[p], dict):
            cursor[p] = {}
        cursor = cursor[p]
    cursor[parts[-1]] = value
    _atomic_write(get_config_path(), _render_toml(cfg))
