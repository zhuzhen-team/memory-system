"""Embedding providers — abstract protocol + lazy-loading factory.

The default provider is ``onnx-bge-m3`` (local, no API key) which downloads
``BAAI/bge-m3`` ONNX weights to ``~/.cache/memoryd/models/`` on first use.
``openai`` is an opt-in alternative for users who want managed embeddings.

Usage
-----
    embedder = get_embedder("onnx-bge-m3")
    vec = await embedder.embed_text("hello world")
    vecs = await embedder.embed_batch(["a", "b"])
"""
from __future__ import annotations

import importlib
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Minimal async embedding interface used by the vector store."""

    @property
    def dim(self) -> int:
        """Output dimension of the embedding vectors."""
        ...

    @property
    def model_name(self) -> str:
        """Identifier (model id) of the underlying embedder — used by chunk_id."""
        ...

    async def embed_text(self, text: str) -> list[float]:
        """Embed a single string and return one dense vector."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of strings and return one dense vector per input."""
        ...


# Provider registry: name -> (module_path, class_name)
_PROVIDERS: dict[str, tuple[str, str]] = {
    "onnx-bge-m3": ("memoryd.embeddings.onnx_bge_m3", "OnnxBgeM3Embedder"),
    "openai": ("memoryd.embeddings.openai_provider", "OpenAIEmbedder"),
}

# Default embedding model id per provider (kept in sync with each class's
# constructor default so callers can resolve the effective model without
# importing heavy deps).
DEFAULT_MODELS: dict[str, str] = {
    "onnx-bge-m3": "BAAI/bge-m3",
    "openai": "text-embedding-3-small",
}


def get_embedder(provider: str = "onnx-bge-m3", **kwargs: Any) -> Embedder:
    """Instantiate an embedder by *provider* name.

    Parameters
    ----------
    provider:
        ``"onnx-bge-m3"`` (default) or ``"openai"``.
    **kwargs:
        Forwarded to the embedder constructor. Common kwargs:
        ``model`` (str), ``batch_size`` (int), ``api_key`` (str, openai only).

    Raises
    ------
    ValueError:
        Unknown provider name.
    ImportError:
        Provider's optional dependencies are not installed.
    """
    if provider not in _PROVIDERS:
        available = ", ".join(sorted(_PROVIDERS))
        raise ValueError(
            f"unknown embedding provider {provider!r}; available: {available}"
        )

    module_path, class_name = _PROVIDERS[provider]
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        raise ImportError(
            f"embedding provider {provider!r} is missing dependencies: {exc}"
        ) from exc
    cls = getattr(mod, class_name)
    return cls(**kwargs)


__all__ = ["DEFAULT_MODELS", "Embedder", "get_embedder"]
