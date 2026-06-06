"""Tests for memoryd.llm.factory.get_llm and the new async provider surface.

All HTTP / SDK calls are mocked — these tests must never reach a real API.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from memoryd.llm import (
    AnthropicAsyncProvider,
    LLMMessage,
    LLMUnavailable,
    OllamaAsyncProvider,
    OpenAIAsyncProvider,
    get_llm,
)


# ---------------------------------------------------------------------------
# Factory routing
# ---------------------------------------------------------------------------


def test_get_llm_anthropic_returns_async_provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    p = get_llm("anthropic", client=MagicMock())
    assert isinstance(p, AnthropicAsyncProvider)
    assert p.name == "anthropic"


def test_get_llm_anthropic_uses_default_model_when_none(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    p = get_llm("anthropic", client=MagicMock())
    # Default Anthropic model should resolve to the Plan 3 baseline.
    assert "claude" in p.model


def test_get_llm_anthropic_respects_model_override():
    p = get_llm("anthropic", model="claude-sonnet-4-5", client=MagicMock())
    assert p.model == "claude-sonnet-4-5"


def test_get_llm_openai_returns_provider(monkeypatch):
    p = get_llm("openai", client=MagicMock())
    assert isinstance(p, OpenAIAsyncProvider)
    assert p.name == "openai"


def test_get_llm_azure_openai_returns_provider_with_azure_flavor():
    p = get_llm("azure-openai", client=MagicMock())
    assert isinstance(p, OpenAIAsyncProvider)
    assert p.name == "azure-openai"


def test_get_llm_ollama_returns_provider():
    p = get_llm("ollama")
    assert isinstance(p, OllamaAsyncProvider)
    assert p.name == "ollama"


def test_get_llm_unknown_provider_raises():
    with pytest.raises(LLMUnavailable, match="unknown LLM provider"):
        get_llm("not-a-real-provider")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Env-key error paths (provider constructors)
# ---------------------------------------------------------------------------


def test_anthropic_async_provider_raises_when_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable, match="ANTHROPIC_API_KEY"):
        AnthropicAsyncProvider()


def test_openai_async_provider_raises_when_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable, match="OPENAI_API_KEY"):
        OpenAIAsyncProvider()


def test_azure_openai_async_provider_raises_when_no_api_key(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    with pytest.raises(LLMUnavailable, match="AZURE_OPENAI_API_KEY"):
        OpenAIAsyncProvider(flavor="azure-openai")


def test_azure_openai_async_provider_raises_when_no_endpoint(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-test")
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    with pytest.raises(LLMUnavailable, match="AZURE_OPENAI_ENDPOINT"):
        OpenAIAsyncProvider(flavor="azure-openai")


# ---------------------------------------------------------------------------
# Sanity: legacy get_provider still works (backward compat)
# ---------------------------------------------------------------------------


def test_legacy_get_provider_still_importable(monkeypatch, tmp_path):
    """get_provider keeps working and follows DEFAULT_CONFIG (claude-code).

    Was asserting AnthropicProvider — stale since the default provider
    deliberately moved to claude-code (see DEFAULT_CONFIG comment)."""
    from memoryd.llm import get_provider
    from memoryd.llm.claude_code_provider import ClaudeCodeProvider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    for var in (
        "https_proxy",
        "HTTPS_PROXY",
        "http_proxy",
        "HTTP_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(var, raising=False)
    p = get_provider()
    assert isinstance(p, ClaudeCodeProvider)


# ---------------------------------------------------------------------------
# Sanity: LLMMessage is a Pydantic model with role/content
# ---------------------------------------------------------------------------


def test_llm_message_roundtrips():
    m = LLMMessage(role="user", content="hi")
    assert m.role == "user"
    assert m.content == "hi"
    # Pydantic v2 dumps
    assert m.model_dump() == {"role": "user", "content": "hi"}


# ---------------------------------------------------------------------------
# Config-aware factory (get_llm_from_config)
# ---------------------------------------------------------------------------


def test_get_llm_from_config_honors_configured_provider(monkeypatch, tmp_path):
    """judge/compare must use the provider from config.toml, not the hardcoded
    anthropic default. Real incident: claude-code was configured but every MCP
    judge call failed with 'ANTHROPIC_API_KEY env not set'."""
    (tmp_path / "config.toml").write_text(
        '[llm]\nprovider = "claude-code"\nmodel = "claude-haiku-4-5"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    from memoryd.llm import get_llm_from_config
    from memoryd.llm.claude_code_provider import ClaudeCodeProvider

    p = get_llm_from_config()
    assert isinstance(p, ClaudeCodeProvider)
    assert p.model == "claude-haiku-4-5"


def test_get_llm_from_config_anthropic_route(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    (tmp_path / "config.toml").write_text(
        '[llm]\nprovider = "anthropic"\nmodel = "claude-sonnet-4-5"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    from memoryd.llm import get_llm_from_config

    p = get_llm_from_config(client=MagicMock())
    assert isinstance(p, AnthropicAsyncProvider)
    assert p.model == "claude-sonnet-4-5"
