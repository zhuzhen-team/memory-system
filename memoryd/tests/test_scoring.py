"""Tests for memoryd.search.scoring — BM25 normalisation + additive ranking."""
from __future__ import annotations

import math

from memoryd.search.scoring import (
    ENTITY_BOOST_WEIGHT,
    get_bm25_params,
    lemmatize_for_bm25,
    normalize_bm25,
    score_and_rank,
)


def test_bm25_params_scale_with_query_length() -> None:
    short = get_bm25_params("hi")
    longer = get_bm25_params("a b c d e")
    long_q = get_bm25_params("a b c d e f g h i j k l m n o p q r s t")
    # Midpoint grows monotonically with query length.
    assert short[0] < longer[0] <= long_q[0]
    # Both elements are positive floats.
    for mp, step in (short, longer, long_q):
        assert mp > 0
        assert step > 0


def test_normalize_bm25_is_sigmoid() -> None:
    # At midpoint, sigmoid returns 0.5.
    assert math.isclose(normalize_bm25(5.0, 5.0, 0.7), 0.5, abs_tol=1e-9)
    # Very large score saturates near 1.
    assert normalize_bm25(50.0, 5.0, 0.7) > 0.99
    # Very negative score (rare in practice) sits near 0.
    assert normalize_bm25(-50.0, 5.0, 0.7) < 0.01


def test_score_and_rank_threshold_gates_semantic_only() -> None:
    results = [
        {"id": "a", "score": 0.9, "payload": {"x": 1}},
        {"id": "b", "score": 0.3, "payload": {"x": 2}},  # below threshold
    ]
    out = score_and_rank(
        results, bm25_scores={"b": 1.0}, entity_boosts={}, threshold=0.5, top_k=10
    )
    ids = [r["id"] for r in out]
    assert "a" in ids
    assert "b" not in ids  # bm25 cannot rescue a below-threshold semantic hit


def test_score_and_rank_combined_with_all_signals() -> None:
    results = [
        {"id": "a", "score": 0.8, "payload": "pa"},
        {"id": "b", "score": 0.8, "payload": "pb"},
    ]
    out = score_and_rank(
        results,
        bm25_scores={"a": 0.5, "b": 0.0},
        entity_boosts={"a": 0.5, "b": 0.0},
        threshold=0.0,
        top_k=5,
    )
    # a should rank above b because of bm25 + entity contributions.
    assert out[0]["id"] == "a"
    assert out[1]["id"] == "b"
    # Max possible divisor: 1 (sem) + 1 (bm25) + 0.5 (entity) = 2.5.
    assert math.isclose(out[0]["score"], (0.8 + 0.5 + 0.5) / 2.5, abs_tol=1e-9)


def test_score_and_rank_top_k_and_payload_preserved() -> None:
    results = [
        {"id": f"m{i}", "score": 0.9 - i * 0.05, "payload": {"i": i}}
        for i in range(5)
    ]
    out = score_and_rank(
        results, bm25_scores={}, entity_boosts={}, threshold=0.0, top_k=3
    )
    assert len(out) == 3
    assert [r["id"] for r in out] == ["m0", "m1", "m2"]
    assert out[0]["payload"] == {"i": 0}


def test_score_and_rank_skips_results_without_id() -> None:
    out = score_and_rank(
        [{"score": 0.9}, {"id": "x", "score": 0.8}],
        bm25_scores={},
        entity_boosts={},
        threshold=0.0,
        top_k=5,
    )
    assert [r["id"] for r in out] == ["x"]


def test_lemmatize_for_bm25_returns_tokens() -> None:
    tokens = lemmatize_for_bm25("Hello world from memoryd")
    assert tokens  # non-empty
    assert "hello" in tokens.lower() or "world" in tokens.lower()


def test_entity_boost_weight_constant() -> None:
    # Tests rely on this being 0.5; pin it in case mem0 changes upstream.
    assert ENTITY_BOOST_WEIGHT == 0.5
