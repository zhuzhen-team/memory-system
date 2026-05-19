"""Tests for memoryd.search.hybrid — RRF fusion of vector + keyword retrievers.

These tests stub out the heavy components (Milvus + ripgrep) so the RRF /
entity-boost / weight logic can be exercised in isolation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from memoryd.search import hybrid as hybrid_mod
from memoryd.search.hybrid import SearchResult, hybrid_search


class _FakeVectorStore:
    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self._hits = hits
        self.closed = False

    def search(
        self, scope_hash: str, query: str, *, top_k: int = 10, **_: Any
    ) -> list[dict[str, Any]]:
        return self._hits[:top_k]

    def close(self) -> None:
        self.closed = True


def test_empty_query_returns_no_results(tmp_path: Path) -> None:
    assert hybrid_search("", "scope", data_root=tmp_path) == []
    assert hybrid_search("   ", "scope", data_root=tmp_path) == []


def test_vector_only_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hybrid_mod, "_run_ripgrep", lambda *a, **k: [])
    store = _FakeVectorStore(
        [
            {
                "memory_id": "m1",
                "chunk_id": "c1",
                "content": "vector hit one",
                "source": "test",
                "heading": "H",
                "start_line": 1,
                "end_line": 2,
            },
            {
                "memory_id": "m2",
                "chunk_id": "c2",
                "content": "vector hit two",
                "source": "test",
                "heading": "H",
                "start_line": 3,
                "end_line": 4,
            },
        ]
    )
    results = hybrid_search(
        "vector", "scope", top_k=5, vector_store=store, data_root=tmp_path
    )
    assert len(results) == 2
    assert results[0].memory_id == "m1"
    # Earlier rank should have higher score.
    assert results[0].score > results[1].score
    assert all(isinstance(r, SearchResult) for r in results)


def test_keyword_only_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hybrid_mod,
        "_run_ripgrep",
        lambda *a, **k: [("memA", "first match"), ("memB", "second match")],
    )
    store = _FakeVectorStore([])
    results = hybrid_search(
        "match", "scope", top_k=5, vector_store=store, data_root=tmp_path
    )
    ids = [r.memory_id for r in results]
    assert ids == ["memA", "memB"]
    assert results[0].score > results[1].score


def test_overlapping_hits_fuse_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hybrid_mod,
        "_run_ripgrep",
        lambda *a, **k: [("shared", "kw hit"), ("kw_only", "kw")],
    )
    store = _FakeVectorStore(
        [
            {
                "memory_id": "shared",
                "chunk_id": "cs",
                "content": "vec hit for shared",
                "source": "",
                "heading": "",
                "start_line": 0,
                "end_line": 0,
            },
            {
                "memory_id": "vec_only",
                "chunk_id": "cv",
                "content": "vec only",
                "source": "",
                "heading": "",
                "start_line": 0,
                "end_line": 0,
            },
        ]
    )
    results = hybrid_search(
        "anything", "scope", top_k=5, vector_store=store, data_root=tmp_path
    )
    # "shared" is rank 0 in both retrievers → highest score.
    assert results[0].memory_id == "shared"
    shared_score = results[0].score
    vec_only_score = next(r.score for r in results if r.memory_id == "vec_only")
    kw_only_score = next(r.score for r in results if r.memory_id == "kw_only")
    assert shared_score > vec_only_score
    assert shared_score > kw_only_score


def test_entity_boost_lifts_matching_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hybrid_mod, "_run_ripgrep", lambda *a, **k: [])
    store = _FakeVectorStore(
        [
            {
                "memory_id": "low",
                "chunk_id": "cl",
                "content": "x",
                "source": "",
                "heading": "",
                "start_line": 0,
                "end_line": 0,
            },
            {
                "memory_id": "high",
                "chunk_id": "ch",
                "content": "y",
                "source": "",
                "heading": "",
                "start_line": 0,
                "end_line": 0,
            },
        ]
    )
    results = hybrid_search(
        "q",
        "scope",
        top_k=5,
        vector_store=store,
        data_root=tmp_path,
        entity_ids=["high"],  # boost the rank-1 ("high") result
    )
    # entity boost (0.5) is large compared with RRF deltas, so high should win.
    assert results[0].memory_id == "high"
    assert results[0].metadata.get("entity_match") is True
    # The non-boosted entry must not have entity_match flagged.
    other = next(r for r in results if r.memory_id == "low")
    assert other.metadata.get("entity_match") is not True


def test_weight_overrides_change_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hybrid_mod, "_run_ripgrep", lambda *a, **k: [("kw_top", "kw")]
    )
    store = _FakeVectorStore(
        [
            {
                "memory_id": "vec_top",
                "chunk_id": "cv",
                "content": "v",
                "source": "",
                "heading": "",
                "start_line": 0,
                "end_line": 0,
            }
        ]
    )
    # With keyword weight 0 the vector winner should come on top.
    results = hybrid_search(
        "q",
        "scope",
        top_k=5,
        vector_store=store,
        data_root=tmp_path,
        keyword_weight=0.0,
        vector_weight=1.0,
    )
    assert results[0].memory_id == "vec_top"

    # Now flip the weights: keyword wins.
    results2 = hybrid_search(
        "q",
        "scope",
        top_k=5,
        vector_store=store,
        data_root=tmp_path,
        keyword_weight=1.0,
        vector_weight=0.0,
    )
    assert results2[0].memory_id == "kw_top"
