"""User-level memoryd config at ~/.config/memoryd/config.toml.

Schema (minimal v0.3 — Plan 3, extended by Plan 5):
    [llm]
    provider = "anthropic"      # anthropic | openai | openrouter | local
    model = "claude-haiku-4-5"
    api_key_env = "ANTHROPIC_API_KEY"
    request_timeout_sec = 30

    [prompts]
    dura_extract = ""           # path override; empty → use bundled

    [notify.smtp]               # Plan 5 — optional SMTP fallback for digest notify
    enabled = false
    host = ""
    port = 587
    use_tls = true
    from = ""                   # → SMTPConfig.from_addr
    to = ""                     # → SMTPConfig.to_addr
    username = ""
    password_env = ""

`load_config()` returns a `Config` (a `dict` subclass) so legacy
`cfg["llm"]["provider"]` access continues to work, while Plan 5 callers can
use `cfg.notify.smtp.enabled` (typed dataclass attribute access).
"""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .notify import SMTPConfig


DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        # claude-code provider spawns a `claude -p` subprocess and reuses the
        # user's existing Claude Code login. Zero API key needed — this is the
        # default because most memoryd users already run Claude Code locally.
        # Override via config.toml or `memoryd config set llm.provider anthropic`
        # if you want direct Anthropic SDK + ANTHROPIC_API_KEY instead.
        "provider": "claude-code",
        "model": "claude-haiku-4-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "request_timeout_sec": 30,
    },
    "prompts": {
        "dura_extract": "",
    },
}


@dataclass
class NotifyConfig:
    smtp: SMTPConfig = field(default_factory=SMTPConfig)


@dataclass
class SyncConfig:
    enabled: bool = False
    dir: str = ""
    auto_export_on_session_end: bool = False
    auto_import_on_session_start: bool = False


@dataclass
class SensitiveConfig:
    key_source: str = "random"   # "random" | "passphrase"
    kdf_iters: int = 600000


class Config(dict):
    """`dict` subclass that also exposes typed dataclass attributes.

    Existing call sites do `cfg["llm"]["provider"]` (dict access) and
    `json.dumps(cfg)` (dict serialisation). New Plan 5/6 call sites do
    `cfg.notify.smtp.enabled` / `cfg.sync.enabled` / `cfg.sensitive.key_source`
    (attribute access). Keeping both shapes avoids touching legacy code paths.
    """

    notify: NotifyConfig
    sync: SyncConfig
    sensitive: SensitiveConfig

    def __init__(
        self,
        data: dict[str, Any],
        notify: NotifyConfig,
        sync: SyncConfig,
        sensitive: SensitiveConfig,
    ) -> None:
        super().__init__(data)
        # Use object.__setattr__ to avoid odd dict.__setitem__ aliasing.
        object.__setattr__(self, "notify", notify)
        object.__setattr__(self, "sync", sync)
        object.__setattr__(self, "sensitive", sensitive)


def get_config_path() -> Path:
    home = os.environ.get("MEMORYD_CONFIG_HOME")
    base = Path(home) if home else (Path.home() / ".config" / "memoryd")
    return base / "config.toml"


def _config_path() -> Path:
    """Alias for tests that monkeypatch the config path lookup."""
    return get_config_path()


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


def _load_notify(data: dict[str, Any]) -> NotifyConfig:
    """Parse `[notify.smtp]` section into NotifyConfig.

    TOML key `from`/`to` map to dataclass fields `from_addr`/`to_addr`
    because `from` is a Python reserved word.
    """
    smtp_raw = (data.get("notify") or {}).get("smtp") or {}
    smtp = SMTPConfig(
        enabled=bool(smtp_raw.get("enabled", False)),
        host=str(smtp_raw.get("host", "")),
        port=int(smtp_raw.get("port", 587)),
        use_tls=bool(smtp_raw.get("use_tls", True)),
        from_addr=str(smtp_raw.get("from", "")),
        to_addr=str(smtp_raw.get("to", "")),
        username=str(smtp_raw.get("username", "")),
        password_env=str(smtp_raw.get("password_env", "")),
    )
    return NotifyConfig(smtp=smtp)


def _load_sync(data: dict[str, Any]) -> SyncConfig:
    """Parse `[sync]` section into SyncConfig."""
    raw = data.get("sync") or {}
    return SyncConfig(
        enabled=bool(raw.get("enabled", False)),
        dir=str(raw.get("dir", "")),
        auto_export_on_session_end=bool(raw.get("auto_export_on_session_end", False)),
        auto_import_on_session_start=bool(raw.get("auto_import_on_session_start", False)),
    )


def _load_sensitive(data: dict[str, Any]) -> SensitiveConfig:
    """Parse `[sensitive]` section into SensitiveConfig."""
    raw = data.get("sensitive") or {}
    return SensitiveConfig(
        key_source=str(raw.get("key_source", "random")),
        kdf_iters=int(raw.get("kdf_iters", 600000)),
    )


def load_config() -> Config:
    # deepcopy, not dict(): a shallow copy shares the nested "llm"/"sync"/...
    # dicts with process-global DEFAULT_CONFIG, so any in-place edit on a
    # load_config() result (config set, tests) silently poisons every later
    # load_config() in the same process.
    import copy

    p = _config_path()
    if not p.exists():
        data = copy.deepcopy(DEFAULT_CONFIG)
        return Config(data, _load_notify({}), _load_sync({}), _load_sensitive({}))
    parsed = tomllib.loads(p.read_text(encoding="utf-8"))
    # NOTE: this guards the shared global only. Sections present in `parsed`
    # but absent from DEFAULT_CONFIG still alias `parsed` — harmless, since
    # `parsed` is a fresh tomllib result discarded after this call.
    merged = _merge_dict(copy.deepcopy(DEFAULT_CONFIG), parsed)
    return Config(
        merged,
        _load_notify(parsed),
        _load_sync(parsed),
        _load_sensitive(parsed),
    )


def show_config() -> Config:
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
    # `cfg` is a Config (dict subclass) — round-trip via plain dict for write.
    cfg_data: dict[str, Any] = dict(cfg)
    cursor = cfg_data
    for p in parts[:-1]:
        if p not in cursor or not isinstance(cursor[p], dict):
            cursor[p] = {}
        cursor = cursor[p]
    cursor[parts[-1]] = value
    _atomic_write(get_config_path(), _render_toml(cfg_data))
