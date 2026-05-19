"""Hybrid search — ripgrep keyword × Milvus vector with RRF reranking.

Combines two retrievers:

1. **Vector** — :class:`memoryd.search.vector.VectorStore` (Milvus Lite hybrid
   dense+BM25 over chunked content).
2. **Keyword** — ``ripgrep`` over the on-disk markdown files for fast literal
   substring/regex matches that vector recall might miss.

Results are fused with Reciprocal Rank Fusion (RRF) and an optional entity
boost is applied per the additive scoring rules in
:mod:`memoryd.search.scoring`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .scoring import ENTITY_BOOST_WEIGHT
from .vector import VectorStore, _db_path


_RRF_K = 60


def _data_root() -> Path:
    root = os.environ.get("MEMORYD_DATA_ROOT")
    if root:
        return Path(root)
    return Path.home() / ".local" / "share" / "memoryd"


@dataclass
class SearchResult:
    """Single hybrid search hit returned by :func:`hybrid_search`."""

    memory_id: str
    chunk_id: str
    content: str
    score: float
    source: str = ""
    heading: str = ""
    start_line: int = 0
    end_line: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


def _run_ripgrep(
    query: str, root: Path, scope_hash: str
) -> list[tuple[str, str]]:
    """Run ripgrep over the scope's markdown files.

    Returns a list of ``(memory_id, snippet)`` tuples in rank order — earliest
    hit first. ``memory_id`` is the file stem (matches the markdown slug used
    by :mod:`memoryd.storage`).
    """
    scope_dir = root / "scopes" / scope_hash
    if not scope_dir.exists():
        return []
    rg = shutil.which("rg")
    if not rg:
        return []
    try:
        proc = subprocess.run(
            [
                rg,
                "--no-heading",
                "--with-filename",
                "--line-number",
                "--max-count=3",
                "--ignore-case",
                "--type=md",
                "--",
                query,
                str(scope_dir),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if proc.returncode not in (0, 1):  # 1 == no matches
        return []
    hits: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path_str, _line_no, snippet = parts
        memory_id = Path(path_str).stem
        if memory_id in seen:
            continue
        seen.add(memory_id)
        hits.append((memory_id, snippet.strip()))
    return hits


def _rrf_score(rank: int, k: int = _RRF_K) -> float:
    """Reciprocal Rank Fusion contribution for a 0-based *rank*."""
    return 1.0 / (k + rank + 1)


def hybrid_search(
    query: str,
    scope_hash: str,
    *,
    top_k: int = 10,
    keyword_weight: float = 0.5,
    vector_weight: float = 0.5,
    entity_ids: list[str] | None = None,
    vector_store: VectorStore | None = None,
    data_root: Path | None = None,
) -> list[SearchResult]:
    """Search the scope with both ripgrep and Milvus, fuse with RRF.

    Parameters
    ----------
    query:
        Natural-language or keyword query.
    scope_hash:
        12-char scope identifier (see :mod:`memoryd.scope`).
    top_k:
        Maximum number of results to return.
    keyword_weight, vector_weight:
        Linear weights applied to each retriever's RRF score before summing.
        Defaults give equal weight to both signals.
    entity_ids:
        Optional list of ``memory_id`` strings that get a constant entity
        boost (``ENTITY_BOOST_WEIGHT``) added to their combined score.
    vector_store:
        Reuse an existing :class:`VectorStore` (recommended for multi-query
        callers). When ``None`` the function opens and closes a transient
        store on the default db path; pass your own embedder via the store.
    data_root:
        Override the on-disk data root (defaults to ``MEMORYD_DATA_ROOT`` or
        ``~/.local/share/memoryd``).
    """
    if not query.strip():
        return []

    root = data_root or _data_root()

    # ---- vector retriever ---------------------------------------------------
    vector_hits: list[dict[str, Any]] = []
    owns_store = vector_store is None
    if vector_store is None:
        try:
            from ..embeddings import get_embedder

            embedder = get_embedder()
            vector_store = VectorStore(_db_path(), embedder)
        except Exception:
            vector_store = None
    if vector_store is not None:
        try:
            vector_hits = vector_store.search(scope_hash, query, top_k=top_k * 2)
        except Exception:
            vector_hits = []
        if owns_store:
            try:
                vector_store.close()
            except Exception:
                pass

    # ---- keyword retriever (ripgrep) ---------------------------------------
    keyword_hits = _run_ripgrep(query, root, scope_hash)

    # ---- combine via RRF + linear weights ----------------------------------
    fused: dict[str, dict[str, Any]] = {}

    for rank, hit in enumerate(vector_hits):
        mem_id = str(hit.get("memory_id") or hit.get("chunk_id"))
        if not mem_id:
            continue
        score = vector_weight * _rrf_score(rank)
        existing = fused.setdefault(
            mem_id,
            {
                "memory_id": mem_id,
                "chunk_id": str(hit.get("chunk_id", "")),
                "content": str(hit.get("content", "")),
                "source": str(hit.get("source", "")),
                "heading": str(hit.get("heading", "")),
                "start_line": int(hit.get("start_line", 0) or 0),
                "end_line": int(hit.get("end_line", 0) or 0),
                "score": 0.0,
                "metadata": {},
            },
        )
        existing["score"] += score
        existing.setdefault("metadata", {})["vector_rank"] = rank
        if not existing["content"]:
            existing["content"] = str(hit.get("content", ""))

    for rank, (mem_id, snippet) in enumerate(keyword_hits):
        if not mem_id:
            continue
        score = keyword_weight * _rrf_score(rank)
        existing = fused.setdefault(
            mem_id,
            {
                "memory_id": mem_id,
                "chunk_id": "",
                "content": snippet,
                "source": "ripgrep",
                "heading": "",
                "start_line": 0,
                "end_line": 0,
                "score": 0.0,
                "metadata": {},
            },
        )
        existing["score"] += score
        existing.setdefault("metadata", {})["keyword_rank"] = rank
        if not existing["content"]:
            existing["content"] = snippet

    # ---- entity boost ------------------------------------------------------
    entity_set = {str(e) for e in (entity_ids or [])}
    if entity_set:
        for mem_id, row in fused.items():
            if mem_id in entity_set:
                row["score"] += ENTITY_BOOST_WEIGHT
                row.setdefault("metadata", {})["entity_match"] = True

    ranked = sorted(fused.values(), key=lambda r: r["score"], reverse=True)[:top_k]

    return [
        SearchResult(
            memory_id=r["memory_id"],
            chunk_id=r["chunk_id"],
            content=r["content"],
            score=r["score"],
            source=r["source"],
            heading=r["heading"],
            start_line=r["start_line"],
            end_line=r["end_line"],
            metadata=r.get("metadata", {}),
        )
        for r in ranked
    ]


__all__ = ["SearchResult", "hybrid_search"]
