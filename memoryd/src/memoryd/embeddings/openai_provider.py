"""OpenAI embedding provider — text-embedding-3-small by default.

Uses the async ``openai`` client. Reads ``OPENAI_API_KEY`` from the environment
unless ``api_key`` is passed explicitly. The optional ``base_url`` argument
(or ``OPENAI_BASE_URL`` env var) supports OpenAI-compatible gateways.
"""
from __future__ import annotations

import os
from typing import Any


_DEFAULT_BATCH_SIZE = 256


_KNOWN_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbedder:
    """OpenAI embeddings (``text-embedding-3-small`` default)."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        *,
        batch_size: int = 0,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "openai embedder requires the `openai` package; install via `uv add openai`."
            ) from exc

        client_kwargs: dict[str, Any] = {}
        effective_base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        if effective_base_url:
            client_kwargs["base_url"] = effective_base_url
        if api_key:
            client_kwargs["api_key"] = api_key

        self._openai = openai
        self._client = openai.AsyncOpenAI(**client_kwargs)
        self._client_kwargs = client_kwargs
        self._model = model
        self._dim = _detect_dimension(model, client_kwargs, openai)
        self._batch_size = batch_size if batch_size > 0 else _DEFAULT_BATCH_SIZE

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model

    async def embed_text(self, text: str) -> list[float]:
        vecs = await self.embed_batch([text])
        return vecs[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            resp = await self._client.embeddings.create(
                input=batch,
                model=self._model,
                encoding_format="float",
            )
            out.extend(item.embedding for item in resp.data)
        return out


def _detect_dimension(model: str, client_kwargs: dict[str, Any], openai_mod: Any) -> int:
    """Return the embedding dimension for *model*.

    Uses a lookup table for well-known OpenAI models; for unknown models
    (e.g. custom models via ``OPENAI_BASE_URL``), perform a trial sync embed.
    """
    if model in _KNOWN_DIMENSIONS:
        return _KNOWN_DIMENSIONS[model]
    sync_client = openai_mod.OpenAI(**client_kwargs)
    trial = sync_client.embeddings.create(
        input=["dim"], model=model, encoding_format="float"
    )
    return len(trial.data[0].embedding)


__all__ = ["OpenAIEmbedder"]
