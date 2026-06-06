"""``memoryd.llm`` — LLM provider abstraction.

This package supersedes the old single-file ``memoryd/llm.py`` (Plan 3). It
provides two surfaces:

1. **New async multi-provider API** (Plan 10+, this package):
   - :class:`LLMProvider` (async protocol)
   - :class:`LLMMessage`, :class:`JudgeResult` (Pydantic models)
   - :func:`get_llm` factory — picks anthropic / openai / ollama / azure-openai
   - Prompt templates under :mod:`memoryd.llm.prompts`

2. **Legacy sync API** (Plan 3, preserved verbatim so existing call sites and
   :mod:`memoryd.config` keep working):
   - :class:`AnthropicProvider` — sync ``complete(system=, user=)``
   - :class:`LLMUnavailable`
   - :func:`get_provider` — reads ``[llm]`` from ``~/.config/memoryd/config.toml``

Pick the new ``get_llm`` for new code; the old ``get_provider`` continues to
exist for backward compatibility.
"""
from __future__ import annotations

import os

from .anthropic_provider import (
    DEFAULT_MODEL as DEFAULT_ANTHROPIC_MODEL,
    AnthropicAsyncProvider,
)
from .base import (
    JudgeResult,
    LegacyLLMProvider,
    LLMMessage,
    LLMProvider,
    LLMUnavailable,
)
from .claude_code_provider import ClaudeCodeProvider
from .factory import get_llm, get_llm_from_config
from .ollama_provider import OllamaAsyncProvider
from .openai_provider import OpenAIAsyncProvider

__all__ = [
    # New API
    "LLMProvider",
    "LLMMessage",
    "JudgeResult",
    "LLMUnavailable",
    "AnthropicAsyncProvider",
    "OpenAIAsyncProvider",
    "OllamaAsyncProvider",
    "ClaudeCodeProvider",
    "get_llm",
    "get_llm_from_config",
    # Legacy API (Plan 3 backward compat)
    "AnthropicProvider",
    "LegacyLLMProvider",
    "get_provider",
]


# ---------------------------------------------------------------------------
# Legacy Plan 3 surface — verbatim behavior from the old ``memoryd/llm.py``.
# ---------------------------------------------------------------------------


class AnthropicProvider:
    """Legacy synchronous Anthropic wrapper (Plan 3 contract).

    Kept so existing call sites and ``tests/test_llm.py`` continue to pass.
    New code should use :func:`get_llm` + :class:`AnthropicAsyncProvider`.
    """

    def __init__(
        self,
        *,
        client: object | None = None,
        model: str = DEFAULT_ANTHROPIC_MODEL,
    ) -> None:
        if client is None:
            try:
                import anthropic  # type: ignore
            except ImportError as e:  # pragma: no cover
                raise LLMUnavailable("anthropic SDK not installed") from e
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise LLMUnavailable("ANTHROPIC_API_KEY env not set")
            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def complete(
        self, *, system: str, user: str, model: str | None = None
    ) -> str:
        msg = self.client.messages.create(
            model=model or self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts: list[str] = []
        for block in getattr(msg, "content", []) or []:
            if getattr(block, "type", "") == "text":
                parts.append(getattr(block, "text", ""))
        return "".join(parts)


def get_provider() -> LegacyLLMProvider:
    """Construct the provider configured in ``~/.config/memoryd/config.toml``.

    Plan 3 contract — synchronous, returns a :class:`LegacyLLMProvider`.
    """
    # Local import to avoid circular bootstrap during ``memoryd.config`` loads.
    from ..config import load_config

    cfg = load_config()
    name = cfg["llm"]["provider"]
    model = cfg["llm"]["model"]
    if name == "anthropic":
        return AnthropicProvider(model=model)
    if name == "claude-code":
        # ClaudeCodeProvider implements both async (new) and sync .complete()
        # (legacy) interfaces, so legacy callers like governance/analyze.py
        # can use it transparently.
        return ClaudeCodeProvider(model=model)
    raise LLMUnavailable(f"unsupported llm provider: {name!r}")
