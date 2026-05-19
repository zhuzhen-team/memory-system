"""Scoring utilities for hybrid retrieval.

Adapted from mem0/utils/scoring.py (Apache-2.0, Mem0).

Provides:
    * BM25 sigmoid normalisation with query-length-adaptive parameters.
    * Additive scoring combining semantic + BM25 + entity boost.
    * A simple jieba-backed lemmatiser used to count query terms.

All functions are pure (no I/O, no module-level state) so they are easy to
unit-test and embed in higher-level search pipelines.
"""
from __future__ import annotations

import math
import re
from typing import Any


# Weight applied to entity-match boost when summing the combined score.
ENTITY_BOOST_WEIGHT = 0.5


def _normalize_for_count(query: str) -> str:
    """Lowercase + collapse whitespace for stable term counting."""
    return re.sub(r"\s+", " ", query.lower().strip())


def lemmatize_for_bm25(query: str) -> str:
    """Return a space-separated token sequence used for term counting.

    Tries `jieba.cut_for_search` first (handles CJK + ASCII gracefully); falls
    back to a regex split if jieba is unavailable. The return value is *only*
    consumed by ``get_bm25_params`` for length-based parameter selection, so
    the exact tokenisation does not affect ranking quality.
    """
    norm = _normalize_for_count(query)
    if not norm:
        return ""
    try:
        import jieba

        tokens = [t for t in jieba.cut_for_search(norm) if t.strip()]
        if tokens:
            return " ".join(tokens)
    except Exception:
        pass
    return " ".join(re.findall(r"[\w一-鿿]+", norm))


def get_bm25_params(query: str, *, lemmatized: str | None = None) -> tuple[float, float]:
    """Get BM25 sigmoid parameters based on query length.

    Longer queries tend to have higher raw BM25 scores, so the sigmoid
    midpoint and steepness are shifted to keep the normalised score in a
    comparable range across query lengths.

    Returns
    -------
    (midpoint, steepness)
        Parameters consumed by :func:`normalize_bm25`.
    """
    if lemmatized is None:
        lemmatized = lemmatize_for_bm25(query)
    num_terms = len(lemmatized.split()) if lemmatized else 1

    if num_terms <= 3:
        return 5.0, 0.7
    if num_terms <= 6:
        return 7.0, 0.6
    if num_terms <= 9:
        return 9.0, 0.5
    if num_terms <= 15:
        return 10.0, 0.5
    return 12.0, 0.5


def normalize_bm25(raw_score: float, midpoint: float, steepness: float) -> float:
    """Normalise a raw BM25 score to ``[0, 1]`` using a logistic sigmoid.

    Parameters
    ----------
    raw_score:
        Unbounded BM25 score (typically 0–20+).
    midpoint:
        Score at which the sigmoid outputs ``0.5``.
    steepness:
        Controls how quickly the sigmoid transitions.
    """
    return 1.0 / (1.0 + math.exp(-steepness * (raw_score - midpoint)))


def score_and_rank(
    semantic_results: list[dict[str, Any]],
    bm25_scores: dict[str, float],
    entity_boosts: dict[str, float],
    threshold: float,
    top_k: int,
) -> list[dict[str, Any]]:
    """Score candidates additively and return the top-*k* results.

    For each candidate the semantic score is taken from its ``score`` field;
    ``combined = (semantic + bm25 + entity_boost) / max_possible``.

    The threshold gates the **semantic** score before combining — candidates
    below the threshold are excluded even if BM25/entity boosts would have
    raised them. The divisor adapts to which signals are active:

    * Semantic only: ``max_possible = 1.0``
    * Semantic + BM25: ``max_possible = 2.0``
    * Semantic + BM25 + entity: ``max_possible = 2.5``
    * Semantic + entity (no BM25): ``max_possible = 1.5``

    Returns
    -------
    list of dict
        ``{"id": str, "score": float, "payload": Any}`` sorted by score desc.
    """
    has_bm25 = bool(bm25_scores)
    has_entity = bool(entity_boosts)

    max_possible = 1.0
    if has_bm25:
        max_possible += 1.0
    if has_entity:
        max_possible += ENTITY_BOOST_WEIGHT

    scored: list[dict[str, Any]] = []

    for result in semantic_results:
        mem_id = result.get("id")
        if mem_id is None:
            continue
        semantic_score = float(result.get("score", 0.0))
        if semantic_score < threshold:
            continue

        mem_id_str = str(mem_id)
        bm25_score = float(bm25_scores.get(mem_id_str, 0.0))
        entity_boost = float(entity_boosts.get(mem_id_str, 0.0))

        raw_combined = semantic_score + bm25_score + entity_boost
        combined = min(raw_combined / max_possible, 1.0)

        scored.append(
            {
                "id": mem_id_str,
                "score": combined,
                "payload": result.get("payload"),
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


__all__ = [
    "ENTITY_BOOST_WEIGHT",
    "get_bm25_params",
    "lemmatize_for_bm25",
    "normalize_bm25",
    "score_and_rank",
]
