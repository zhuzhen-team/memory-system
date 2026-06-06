"""LLM provider factory.

``get_llm()`` is the new entry point — pick a provider, return an async
:class:`LLMProvider` instance ready for ``generate`` / ``generate_json``.

The legacy synchronous :func:`memoryd.llm.get_provider` (Plan 3) keeps its
behavior and is preserved as a separate function so existing callers do not
break.
"""
from __future__ import annotations

from typing import Any, Literal

from .anthropic_provider import (
    DEFAULT_MODEL as DEFAULT_ANTHROPIC_MODEL,
    AnthropicAsyncProvider,
)
from .base import LLMProvider, LLMUnavailable
from .claude_code_provider import (
    DEFAULT_MODEL as DEFAULT_CLAUDE_CODE_MODEL,
    ClaudeCodeProvider,
)
from .ollama_provider import (
    DEFAULT_MODEL as DEFAULT_OLLAMA_MODEL,
    OllamaAsyncProvider,
)
from .openai_provider import (
    DEFAULT_MODEL as DEFAULT_OPENAI_MODEL,
    OpenAIAsyncProvider,
)


_PROVIDER_NAMES = (
    "anthropic", "openai", "ollama", "azure-openai", "claude-code",
)
ProviderName = Literal[
    "anthropic", "openai", "ollama", "azure-openai", "claude-code",
]


def get_llm(
    provider: ProviderName = "anthropic",
    model: str | None = None,
    **kw: Any,
) -> LLMProvider:
    """Return a provider instance.

    Args:
        provider: one of ``anthropic`` / ``openai`` / ``ollama`` /
            ``azure-openai`` / ``claude-code``.
        model: provider-specific model id; ``None`` uses each provider's default.
        **kw: passed through to the underlying provider constructor (``client=``,
            ``api_key=``, ``base_url=`` for tests).

    Raises:
        LLMUnavailable: unknown provider name, or missing env (delegated from
            provider constructor).
    """
    if provider == "anthropic":
        return AnthropicAsyncProvider(model=model or DEFAULT_ANTHROPIC_MODEL, **kw)
    if provider == "openai":
        return OpenAIAsyncProvider(
            model=model or DEFAULT_OPENAI_MODEL,
            flavor="openai",
            **kw,
        )
    if provider == "azure-openai":
        return OpenAIAsyncProvider(
            model=model or DEFAULT_OPENAI_MODEL,
            flavor="azure-openai",
            **kw,
        )
    if provider == "ollama":
        return OllamaAsyncProvider(model=model or DEFAULT_OLLAMA_MODEL, **kw)
    if provider == "claude-code":
        return ClaudeCodeProvider(model=model or DEFAULT_CLAUDE_CODE_MODEL, **kw)
    raise LLMUnavailable(
        f"unknown LLM provider: {provider!r} (expected one of {_PROVIDER_NAMES})"
    )


def get_llm_from_config(**kw: Any) -> LLMProvider:
    """Like :func:`get_llm`, but provider/model come from config.toml.

    MCP tool handlers (mem_judge / mem_compare) must honor the user's
    configured ``[llm] provider`` the same way the legacy
    :func:`memoryd.llm.get_provider` does — calling bare ``get_llm()``
    silently pins them to the anthropic default and they fail with
    "ANTHROPIC_API_KEY env not set" even when claude-code is configured.
    """
    # Local import to avoid circular bootstrap during ``memoryd.config`` loads
    # (same reason as get_provider).
    from ..config import load_config

    cfg = load_config()
    provider = cfg["llm"]["provider"]
    model = cfg["llm"].get("model") or None
    return get_llm(provider, model=model, **kw)
