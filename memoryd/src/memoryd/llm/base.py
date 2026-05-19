"""LLM provider abstraction.

This module defines the *new* multi-provider abstraction (Plan 10+). The
``LLMProvider`` protocol below is async and JSON-mode aware, designed for
the memory pipeline (extract entities, judge supersedes, rewrite identity,
monthly change reports).

For backward compatibility the legacy synchronous ``complete()`` interface
(Plan 3) is preserved via :class:`LegacyLLMProvider` so old call sites and
:func:`memoryd.llm.get_provider` continue to work.
"""
from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel


class LLMUnavailable(Exception):
    """Raised when a provider cannot be constructed (missing key, bad config)."""


class LLMMessage(BaseModel):
    """Provider-neutral chat message.

    The role taxonomy mirrors OpenAI/Anthropic chat APIs. Anthropic does not
    have a "system" message *inside* the messages list — providers translate
    accordingly (Anthropic uses the top-level ``system`` parameter).
    """

    role: Literal["system", "user", "assistant"]
    content: str


class JudgeResult(BaseModel):
    """Generic 0-1 confidence judgment, re-exported from ``memoryd.llm``.

    Concrete prompts (judge_supersedes etc.) return Pydantic subclasses; this
    is the shared shape for "is X true? confidence + reason" style outputs.
    """

    decision: bool
    confidence: float
    reason: str


@runtime_checkable
class LLMProvider(Protocol):
    """New async multi-provider LLM interface.

    Implementations live in ``anthropic_provider.py`` / ``openai_provider.py``
    / ``ollama_provider.py``. Construct them via :func:`memoryd.llm.get_llm`.
    """

    name: str
    model: str

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str:
        """Single-turn generation returning the raw assistant text."""
        ...

    async def generate_json(
        self,
        messages: list[LLMMessage],
        schema: type[BaseModel] | dict,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> BaseModel | dict:
        """Strict JSON generation. ``schema`` may be a Pydantic model or dict.

        - If a Pydantic model is passed, the parsed instance is returned.
        - If a dict (raw JSON schema) is passed, a parsed ``dict`` is returned.
        """
        ...


# ---------------------------------------------------------------------------
# Legacy (Plan 3) sync protocol — preserved for `complete()` callers.
# ---------------------------------------------------------------------------


@runtime_checkable
class LegacyLLMProvider(Protocol):
    """The original Plan 3 protocol — synchronous, single ``complete`` call.

    The :class:`AnthropicProvider` (legacy variant) and :func:`get_provider`
    continue to honor this shape.
    """

    def complete(self, *, system: str, user: str, model: str | None = None) -> str: ...


# ---------------------------------------------------------------------------
# Helpers shared across providers.
# ---------------------------------------------------------------------------


def split_system(messages: list[LLMMessage]) -> tuple[str, list[dict[str, Any]]]:
    """Extract the (first) ``system`` message from a messages list.

    Anthropic's API takes ``system`` as a top-level parameter, not as a
    role inside the messages array. Returns ``(system_text, other_messages)``
    where ``other_messages`` is a list of ``{"role", "content"}`` dicts.
    """
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
        else:
            rest.append({"role": m.role, "content": m.content})
    return ("\n\n".join(system_parts), rest)
