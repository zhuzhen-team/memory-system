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
from .ollama_provider import (
    DEFAULT_MODEL as DEFAULT_OLLAMA_MODEL,
    OllamaAsyncProvider,
)
from .openai_provider import (
    DEFAULT_MODEL as DEFAULT_OPENAI_MODEL,
    OpenAIAsyncProvider,
)


_PROVIDER_NAMES = ("anthropic", "openai", "ollama", "azure-openai")
ProviderName = Literal["anthropic", "openai", "ollama", "azure-openai"]


def get_llm(
    provider: ProviderName = "anthropic",
    model: str | None = None,
    **kw: Any,
) -> LLMProvider:
    """Return a provider instance.

    Args:
        provider: one of ``anthropic`` / ``openai`` / ``ollama`` / ``azure-openai``.
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
    raise LLMUnavailable(
        f"unknown LLM provider: {provider!r} (expected one of {_PROVIDER_NAMES})"
    )
