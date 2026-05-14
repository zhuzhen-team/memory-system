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
