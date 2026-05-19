"""Local Ollama HTTP provider.

Calls Ollama's ``/api/chat`` endpoint (default ``http://localhost:11434``).
No SDK dependency — uses stdlib ``urllib.request`` so tests can mock the
single ``_post`` entry point cleanly without extra HTTP libs.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel

from .base import LLMMessage, LLMUnavailable

DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaAsyncProvider:
    """Async-style Ollama wrapper (sync HTTP under the hood, awaited in tests).

    Ollama is local, so a thread-blocking HTTP call is fine — we still expose
    ``async def`` so the interface matches the protocol. Use ``base_url`` to
    target a non-default Ollama instance.
    """

    name = "ollama"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        timeout: float = 60.0,
        post: Any = None,
    ) -> None:
        self.model = model
        self.base_url = (
            base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.timeout = timeout
        # Tests inject _post; default uses urllib.
        self._post = post or self._default_post

    def _default_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read().decode("utf-8")
        except urllib.error.URLError as e:  # pragma: no cover - exercised in tests via mock
            raise LLMUnavailable(f"ollama HTTP error: {e}") from e
        return json.loads(payload)

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }
        if json_mode:
            body["format"] = "json"
        resp = self._post("/api/chat", body)
        # Ollama /api/chat returns {"message": {"role": "...", "content": "..."}}
        msg = resp.get("message") or {}
        return msg.get("content", "") or ""

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
