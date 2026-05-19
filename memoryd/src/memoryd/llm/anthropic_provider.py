"""Anthropic Claude provider for the new async LLM abstraction.

Wraps ``anthropic.AsyncAnthropic`` (or a mock client injected by tests). Honors
``ANTHROPIC_API_KEY`` and respects ``HTTPS_PROXY`` via the SDK's default.

The legacy synchronous :class:`AnthropicProvider` from Plan 3 lives in
:mod:`memoryd.llm` (re-exported by ``__init__``) — this class is the new
async one used by ``get_llm("anthropic")``.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import BaseModel

from .base import LLMMessage, LLMUnavailable, split_system

DEFAULT_MODEL = "claude-haiku-4-5"


class AnthropicAsyncProvider:
    """Async Anthropic Messages API wrapper.

    Pass ``client=`` to inject a mock; otherwise the constructor tries to
    import ``anthropic`` and build an ``AsyncAnthropic()``.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        client: object | None = None,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        if client is None:
            try:
                import anthropic  # type: ignore
            except ImportError as e:  # pragma: no cover - SDK pinned in pyproject
                raise LLMUnavailable("anthropic SDK not installed") from e
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise LLMUnavailable("ANTHROPIC_API_KEY env not set")
            client = anthropic.AsyncAnthropic(api_key=key)
        self.client = client

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str:
        system, rest = split_system(messages)
        # Anthropic has no native json_mode flag — we strengthen the system
        # prompt instead so the model produces strict JSON. Callers wanting
        # parsed output should use generate_json().
        if json_mode:
            json_hint = (
                "\n\n你必须只输出严格、可被 json.loads 解析的 JSON。"
                "不要包含解释、markdown 围栏或前导文本。"
            )
            system = (system + json_hint) if system else json_hint.strip()

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": rest,
        }
        if system:
            kwargs["system"] = system

        msg = await self.client.messages.create(**kwargs)
        parts: list[str] = []
        for block in getattr(msg, "content", []) or []:
            if getattr(block, "type", "") == "text":
                parts.append(getattr(block, "text", ""))
        return "".join(parts)

    async def generate_json(
        self,
        messages: list[LLMMessage],
        schema: type[BaseModel] | dict,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
    ) -> BaseModel | dict:
        raw = await self.generate(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=True,
        )
        data = _parse_json(raw)
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return schema.model_validate(data)
        return data


def _parse_json(text: str) -> Any:
    """Tolerant JSON parser: strips ```json fences and trailing prose.

    Anthropic occasionally wraps the output in ``` fences even when told not to;
    we extract the largest JSON-looking substring as fallback.
    """
    stripped = text.strip()
    # Strip leading/trailing code fences.
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        # Fallback: find first { … last } / first [ … last ].
        for open_, close_ in (("{", "}"), ("[", "]")):
            i = stripped.find(open_)
            j = stripped.rfind(close_)
            if 0 <= i < j:
                try:
                    return json.loads(stripped[i : j + 1])
                except json.JSONDecodeError:
                    continue
        raise
