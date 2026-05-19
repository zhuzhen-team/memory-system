"""Tests for memoryd.embeddings — factory + OpenAI provider (mocked).

The ONNX provider downloads ~1 GB on first run, so its end-to-end test is
gated behind ``-m slow`` (skipped by default).
"""
from __future__ import annotations

from typing import Any

import pytest

from memoryd.embeddings import DEFAULT_MODELS, Embedder, get_embedder
from memoryd.embeddings.openai_provider import OpenAIEmbedder


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        get_embedder("does-not-exist")


def test_default_models_table_has_onnx_and_openai() -> None:
    assert "onnx-bge-m3" in DEFAULT_MODELS
    assert "openai" in DEFAULT_MODELS
    assert DEFAULT_MODELS["openai"] == "text-embedding-3-small"


class _FakeAsyncEmbeddings:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def create(
        self, *, input: list[str], model: str, encoding_format: str
    ) -> Any:
        self.calls.append(input)
        # Return a fake response with one 1536-d zero vector per input.
        class _Item:
            def __init__(self, vec: list[float]) -> None:
                self.embedding = vec

        class _Resp:
            def __init__(self, items: list[_Item]) -> None:
                self.data = items

        return _Resp([_Item([0.0] * 1536) for _ in input])


class _FakeAsyncClient:
    def __init__(self, **_: Any) -> None:
        self.embeddings = _FakeAsyncEmbeddings()


class _FakeSyncClient:
    def __init__(self, **_: Any) -> None:
        pass


def _patch_openai(monkeypatch: pytest.MonkeyPatch) -> _FakeAsyncEmbeddings:
    import openai

    fake = _FakeAsyncClient()
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kw: fake)
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: _FakeSyncClient())
    return fake.embeddings


def test_openai_embedder_dim_for_known_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_openai(monkeypatch)
    e = OpenAIEmbedder(model="text-embedding-3-small", api_key="sk-test")
    assert e.dim == 1536
    assert e.model_name == "text-embedding-3-small"


@pytest.mark.asyncio
async def test_openai_embedder_embed_text_returns_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_embeddings = _patch_openai(monkeypatch)
    e = OpenAIEmbedder(model="text-embedding-3-small", api_key="sk-test")
    vec = await e.embed_text("hello")
    assert isinstance(vec, list)
    assert len(vec) == 1536
    assert fake_embeddings.calls == [["hello"]]


@pytest.mark.asyncio
async def test_openai_embedder_embed_batch_batches_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_embeddings = _patch_openai(monkeypatch)
    e = OpenAIEmbedder(
        model="text-embedding-3-small", api_key="sk-test", batch_size=2
    )
    vecs = await e.embed_batch(["a", "b", "c", "d", "e"])
    assert len(vecs) == 5
    # 5 inputs with batch_size=2 → 3 calls (2 + 2 + 1).
    assert [len(c) for c in fake_embeddings.calls] == [2, 2, 1]


@pytest.mark.asyncio
async def test_openai_embedder_empty_batch_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_embeddings = _patch_openai(monkeypatch)
    e = OpenAIEmbedder(model="text-embedding-3-small", api_key="sk-test")
    assert await e.embed_batch([]) == []
    assert fake_embeddings.calls == []


def test_get_embedder_returns_openai_when_mocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_openai(monkeypatch)
    e = get_embedder("openai", api_key="sk-test")
    assert isinstance(e, OpenAIEmbedder)
    # Embedder is a Protocol — runtime_checkable lets us assert structural conformance.
    assert isinstance(e, Embedder)


@pytest.mark.slow
def test_onnx_embedder_loads_model() -> None:
    """End-to-end ONNX load. Skipped by default (downloads ~1 GB)."""
    pytest.importorskip("onnxruntime")
    from memoryd.embeddings.onnx_bge_m3 import OnnxBgeM3Embedder

    e = OnnxBgeM3Embedder()
    assert e.dim > 0
    assert e.model_name == "BAAI/bge-m3"
