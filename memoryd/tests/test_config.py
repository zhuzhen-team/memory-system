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
