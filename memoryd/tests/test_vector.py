"""Tests for memoryd.search.vector — Milvus Lite vector store.

Uses an in-memory / tmp_path Milvus Lite db with a fake deterministic
embedder (no model download) to keep the test fast and offline.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="milvus-lite is not available on Windows"
)


class _FakeEmbedder:
    """Deterministic 32-d embedder used for vector tests.

    Hashes each text into a stable byte sequence then projects to a unit
    vector — good enough for cosine ANN sanity checks.
    """

    dim = 32
    model_name = "fake-embedder"

    async def embed_text(self, text: str) -> list[float]:
        return self._encode(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._encode(t) for t in texts]

    def _encode(self, text: str) -> list[float]:
        h = hashlib.sha256(text.encode()).digest()
        # Repeat hash to fill 32 floats, scaled to [-1, 1].
        raw = (h * ((self.dim // len(h)) + 1))[: self.dim]
        # Normalise.
        import math

        floats = [(b - 128) / 128.0 for b in raw]
        norm = math.sqrt(sum(f * f for f in floats)) or 1.0
        return [f / norm for f in floats]


def _milvus_available() -> bool:
    try:
        import pymilvus  # noqa: F401
    except Exception:
        return False
    return True


pytestmark = [
    pytestmark,
    pytest.mark.skipif(not _milvus_available(), reason="pymilvus not installed"),
]


@pytest.fixture
def store(tmp_path: Path) -> Any:
    from memoryd.search.vector import VectorStore

    db = tmp_path / "milvus.db"
    s = VectorStore(db, _FakeEmbedder())
    yield s
    s.close()


def test_upsert_chunk_and_count(store: Any) -> None:
    scope = "abc123def456"
    cid = store.upsert_chunk(
        scope_hash=scope,
        memory_id="mem-1",
        chunk_idx=0,
        text="hello world from memoryd",
        metadata={"content_hash": "h1", "start_line": 1, "end_line": 1},
    )
    assert isinstance(cid, str) and len(cid) == 16
    assert store.count(scope) == 1


def test_search_returns_inserted_chunks(store: Any) -> None:
    scope = "scope_search_1"
    store.upsert_chunks(
        scope,
        "mem-A",
        [
            {
                "text": "vector databases store embeddings efficiently",
                "metadata": {"content_hash": "a", "start_line": 1, "end_line": 1},
            },
            {
                "text": "the cat sat on the mat",
                "metadata": {"content_hash": "b", "start_line": 2, "end_line": 2},
            },
        ],
    )
    hits = store.search(scope, "vector embeddings", top_k=2)
    assert hits
    assert any("vector" in (h.get("content") or "") for h in hits)
    for h in hits:
        assert "score" in h
        assert h.get("memory_id") == "mem-A"


def test_search_on_missing_scope_returns_empty(store: Any) -> None:
    assert store.search("unknown_scope_xyz", "anything", top_k=3) == []


def test_delete_memory_removes_all_chunks(store: Any) -> None:
    scope = "scope_delete"
    store.upsert_chunks(
        scope,
        "mem-del",
        [
            {
                "text": f"piece {i}",
                "metadata": {"content_hash": f"c{i}", "start_line": i, "end_line": i},
            }
            for i in range(3)
        ],
    )
    assert store.count(scope) == 3
    removed = store.delete_memory(scope, "mem-del")
    assert removed == 3
    assert store.count(scope) == 0


def test_drop_scope_removes_collection(store: Any) -> None:
    scope = "scope_drop"
    store.upsert_chunk(
        scope, "m", 0, "hello",
        metadata={"content_hash": "x", "start_line": 1, "end_line": 1},
    )
    assert store.count(scope) == 1
    store.drop_scope(scope)
    assert store.count(scope) == 0
