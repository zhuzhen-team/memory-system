"""LLM provider tests (mock HTTP, no real API calls)."""
from unittest.mock import MagicMock

import pytest

from memoryd.llm import (
    AnthropicProvider,
    LLMUnavailable,
    get_provider,
)


def test_anthropic_provider_calls_messages_create(monkeypatch):
    """AnthropicProvider.complete builds a Messages request and returns text."""
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = [MagicMock(type="text", text="hello world")]
    fake_client.messages.create.return_value = fake_msg

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    p = AnthropicProvider(client=fake_client, model="claude-haiku-4-5")
    out = p.complete(system="sys", user="user prompt")
    assert out == "hello world"
    args, kwargs = fake_client.messages.create.call_args
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["system"] == "sys"
    assert kwargs["messages"][0]["role"] == "user"
    assert kwargs["messages"][0]["content"] == "user prompt"


def test_anthropic_provider_concatenates_multi_block_content(monkeypatch):
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = [
        MagicMock(type="text", text="part1"),
        MagicMock(type="text", text="part2"),
    ]
    fake_client.messages.create.return_value = fake_msg
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    p = AnthropicProvider(client=fake_client)
    assert p.complete(system="s", user="u") == "part1part2"


def test_anthropic_provider_raises_on_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable, match="ANTHROPIC_API_KEY"):
        AnthropicProvider()


def test_get_provider_returns_anthropic_when_configured(monkeypatch, tmp_path):
    """get_provider() reads ~/.config/memoryd/config.toml."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    # Actually configure anthropic — the test used to lean on the old default
    # (and on DEFAULT_CONFIG aliasing pollution masking it after the default
    # moved to claude-code).
    (tmp_path / "config.toml").write_text('[llm]\nprovider = "anthropic"\n')
    # Clear proxy env vars so anthropic.Anthropic() can initialise in environments
    # that have a SOCKS proxy configured (socksio may not be installed).
    for _pvar in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY",
                  "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(_pvar, raising=False)
    p = get_provider()
    assert isinstance(p, AnthropicProvider)


def test_get_provider_raises_for_unknown_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    # write config with unknown provider
    cfg = tmp_path / "config.toml"
    cfg.write_text('[llm]\nprovider = "weird"\n')
    with pytest.raises(LLMUnavailable, match="weird"):
        get_provider()
