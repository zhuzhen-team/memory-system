"""OpenAI / Azure OpenAI provider.

Both flavors share a single class — Azure is just OpenAI with a different
``base_url``, ``api_key`` env, and (optionally) ``api_version`` header. Build
an Azure instance via :func:`memoryd.llm.get_llm` with
``provider="azure-openai"``.
"""
from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel

from .base import LLMMessage, LLMUnavailable

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAIAsyncProvider:
    """Async OpenAI / Azure OpenAI chat completions wrapper.

    Tests inject ``client=`` (an object with ``chat.completions.create``).
    """

    def __init__(
        self,
        *,
        client: object | None = None,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
        flavor: str = "openai",  # "openai" | "azure-openai"
    ) -> None:
        self.model = model
        self.name = flavor
        if client is None:
            try:
                import openai  # type: ignore
            except ImportError as e:  # pragma: no cover - SDK pinned in pyproject
                raise LLMUnavailable("openai SDK not installed") from e
            if flavor == "azure-openai":
                key = api_key or os.environ.get("AZURE_OPENAI_API_KEY")
                if not key:
                    raise LLMUnavailable("AZURE_OPENAI_API_KEY env not set")
                endpoint = base_url or os.environ.get("AZURE_OPENAI_ENDPOINT")
                if not endpoint:
                    raise LLMUnavailable("AZURE_OPENAI_ENDPOINT env not set")
                api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
                client = openai.AsyncAzureOpenAI(
                    api_key=key,
                    azure_endpoint=endpoint,
                    api_version=api_version,
                )
            else:
                key = api_key or os.environ.get("OPENAI_API_KEY")
                if not key:
                    raise LLMUnavailable("OPENAI_API_KEY env not set")
                kw: dict[str, Any] = {"api_key": key}
                if base_url:
                    kw["base_url"] = base_url
                client = openai.AsyncOpenAI(**kw)
        self.client = client

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str:
        payload: list[dict[str, str]] = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": payload,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            # OpenAI Chat Completions supports response_format json_object.
            kwargs["response_format"] = {"type": "json_object"}

        resp = await self.client.chat.completions.create(**kwargs)
        # response.choices[0].message.content
        choices = getattr(resp, "choices", []) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "") if message else ""
        return content or ""

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
        data = json.loads(raw) if raw else {}
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return schema.model_validate(data)
        return data
