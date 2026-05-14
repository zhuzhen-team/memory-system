"""LLM provider abstraction. Plan 3 implements Anthropic; other providers
(openai / openrouter / local ollama) are stubs that raise LLMUnavailable
until Plan 5+ adds them.
"""
from __future__ import annotations

import os
from typing import Protocol

from .config import load_config


class LLMUnavailable(Exception):
    """Raised when no usable provider can be constructed."""


class LLMProvider(Protocol):
    def complete(self, *, system: str, user: str, model: str | None = None) -> str: ...


class AnthropicProvider:
    """Calls Anthropic Messages API.

    `anthropic` SDK auto-respects HTTPS_PROXY / ANTHROPIC_API_KEY env.
    Pass `client=` to inject a mock in tests.
    """

    def __init__(self, *, client: object | None = None, model: str = "claude-haiku-4-5") -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise LLMUnavailable("anthropic SDK not installed") from e
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise LLMUnavailable("ANTHROPIC_API_KEY env not set")
            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def complete(self, *, system: str, user: str, model: str | None = None) -> str:
        msg = self.client.messages.create(
            model=model or self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # Concatenate all text blocks
        parts: list[str] = []
        for block in getattr(msg, "content", []) or []:
            if getattr(block, "type", "") == "text":
                parts.append(getattr(block, "text", ""))
        return "".join(parts)


def get_provider() -> LLMProvider:
    """Construct the provider configured in ~/.config/memoryd/config.toml."""
    cfg = load_config()
    name = cfg["llm"]["provider"]
    model = cfg["llm"]["model"]
    if name == "anthropic":
        return AnthropicProvider(model=model)
    raise LLMUnavailable(f"unsupported llm provider: {name!r}")
