"""Vector + hybrid search sub-package.

Public surface
--------------
* :class:`VectorStore` — Milvus Lite wrapper, one collection per scope.
* :func:`hybrid_search` — ripgrep × vector RRF fusion with optional entity boost.
* :class:`SearchResult` — dataclass returned by :func:`hybrid_search`.
* :mod:`scoring` — pure-function scoring utilities (BM25 normalisation, etc.).
"""
from __future__ import annotations

from .hybrid import SearchResult, hybrid_search
from .scoring import (
    ENTITY_BOOST_WEIGHT,
    get_bm25_params,
    lemmatize_for_bm25,
    normalize_bm25,
    score_and_rank,
)
# Legacy session-level search (kept for backwards compatibility with
# cli.py / server.py / test_search.py — these were previously consumers of
# the flat `memoryd.search` module, now folded into `search.sessions`).
from .sessions import SearchHit, search_sessions
from .vector import VectorStore

__all__ = [
    "ENTITY_BOOST_WEIGHT",
    "SearchHit",
    "SearchResult",
    "VectorStore",
    "get_bm25_params",
    "hybrid_search",
    "lemmatize_for_bm25",
    "normalize_bm25",
    "score_and_rank",
    "search_sessions",
]
