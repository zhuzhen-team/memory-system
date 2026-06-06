"""Config read/write tests."""
import tomllib
from pathlib import Path

import pytest

from memoryd.config import (
    DEFAULT_CONFIG,
    get_config_path,
    load_config,
    set_config_key,
    show_config,
)


def test_load_default_when_file_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    cfg = load_config()
    assert cfg["llm"]["provider"] == DEFAULT_CONFIG["llm"]["provider"]


def test_set_key_creates_file_and_value(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    set_config_key("llm.provider", "openai")
    cfg_path = get_config_path()
    parsed = tomllib.loads(cfg_path.read_text())
    assert parsed["llm"]["provider"] == "openai"


def test_set_nested_key_preserves_other_keys(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    set_config_key("llm.provider", "anthropic")
    set_config_key("llm.model", "claude-haiku-4-5")
    cfg = load_config()
    assert cfg["llm"]["provider"] == "anthropic"
    assert cfg["llm"]["model"] == "claude-haiku-4-5"


def test_show_config_is_dict(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    out = show_config()
    assert isinstance(out, dict)
    assert "llm" in out


def test_set_key_rejects_invalid_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="path"):
        set_config_key("bare", "value")  # missing dot → invalid


def test_load_notify_smtp_section(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("""
[notify.smtp]
enabled = true
host = "smtp.example.com"
port = 465
use_tls = false
from = "me@example.com"
to = "you@example.com"
username = "me"
password_env = "PW"
""")
    monkeypatch.setattr("memoryd.config._config_path", lambda: cfg_file)
    cfg = load_config()
    assert cfg.notify.smtp.enabled is True
    assert cfg.notify.smtp.host == "smtp.example.com"
    assert cfg.notify.smtp.port == 465
    assert cfg.notify.smtp.use_tls is False
    assert cfg.notify.smtp.from_addr == "me@example.com"
    assert cfg.notify.smtp.to_addr == "you@example.com"
    assert cfg.notify.smtp.password_env == "PW"


def test_load_notify_defaults_when_section_missing(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("")
    monkeypatch.setattr("memoryd.config._config_path", lambda: cfg_file)
    cfg = load_config()
    assert cfg.notify.smtp.enabled is False
    assert cfg.notify.smtp.host == ""


def test_load_sync_config_defaults(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("")
    monkeypatch.setattr("memoryd.config._config_path", lambda: cfg_file)
    cfg = load_config()
    assert cfg.sync.enabled is False
    assert cfg.sync.dir == ""
    assert cfg.sync.auto_export_on_session_end is False
    assert cfg.sync.auto_import_on_session_start is False


def test_load_sync_config_from_toml(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("""
[sync]
enabled = true
dir = "~/Dropbox/memoryd"
auto_export_on_session_end = true
auto_import_on_session_start = true
""")
    monkeypatch.setattr("memoryd.config._config_path", lambda: cfg_file)
    cfg = load_config()
    assert cfg.sync.enabled is True
    assert cfg.sync.dir == "~/Dropbox/memoryd"
    assert cfg.sync.auto_export_on_session_end is True


def test_load_sensitive_config_defaults(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("")
    monkeypatch.setattr("memoryd.config._config_path", lambda: cfg_file)
    cfg = load_config()
    assert cfg.sensitive.key_source == "random"
    assert cfg.sensitive.kdf_iters == 600000


def test_load_sensitive_config_passphrase_mode(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("""
[sensitive]
key_source = "passphrase"
kdf_iters = 1200000
""")
    monkeypatch.setattr("memoryd.config._config_path", lambda: cfg_file)
    cfg = load_config()
    assert cfg.sensitive.key_source == "passphrase"
    assert cfg.sensitive.kdf_iters == 1200000


def test_load_config_never_aliases_default_config(monkeypatch, tmp_path):
    """Mutating a load_config() result must not poison the process-global
    DEFAULT_CONFIG. It was a shallow copy: nested dicts ("llm", "sync", ...)
    were shared, so any in-place edit (config set, tests) leaked into every
    later load_config() in the same process."""
    from memoryd.config import DEFAULT_CONFIG, load_config

    # path 1: no config file → defaults
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path / "empty"))
    cfg = load_config()
    cfg["llm"]["provider"] = "poisoned"
    assert DEFAULT_CONFIG["llm"]["provider"] != "poisoned"

    # path 2: partial config file → merged; untouched sections must not alias
    d = tmp_path / "partial"
    d.mkdir()
    (d / "config.toml").write_text("[sync]\ndir = \"/tmp/x\"\n", encoding="utf-8")
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(d))
    cfg2 = load_config()
    cfg2["llm"]["model"] = "poisoned-model"
    assert DEFAULT_CONFIG["llm"]["model"] != "poisoned-model"
