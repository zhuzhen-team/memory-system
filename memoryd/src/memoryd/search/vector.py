"""Milvus Lite vector store — one collection per scope.

Layout
------
A single Milvus Lite database (``~/.local/share/memoryd/milvus.db`` by default)
holds one collection per scope. Collection name is ``mem_<scope_hash>``;
keeping collections per-scope means deleting a scope is a single drop call,
and search filters do not need a scope predicate.

Schema (per collection)
-----------------------
    chunk_id      VARCHAR(64)  primary key — composite hash of memory + chunk
    memory_id     VARCHAR(128) source memory slug
    chunk_idx     INT64        chunk index inside the parent memory
    content       VARCHAR(65535) text used for BM25 + return payload
    embedding     FLOAT_VECTOR(dim) dense vector (cosine)
    sparse_vector SPARSE_FLOAT_VECTOR  built by the BM25 Function from content
    source        VARCHAR(256) free-form provenance string
    heading       VARCHAR(512) nearest markdown heading
    start_line    INT64
    end_line      INT64

Milvus Lite does not support Windows, so the import is wrapped in a clear
runtime error pointing the user to Docker / WSL2.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, ClassVar

from ..chunking import compute_chunk_id
from ..embeddings import Embedder


logger = logging.getLogger(__name__)


DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "memoryd" / "milvus.db"


def _db_path() -> Path:
    override = os.environ.get("MEMORYD_MILVUS_DB")
    if override:
        return Path(override)
    root = os.environ.get("MEMORYD_DATA_ROOT")
    if root:
        return Path(root) / "milvus.db"
    return DEFAULT_DB_PATH


def _escape_filter_value(value: str) -> str:
    """Escape backslashes and double quotes for Milvus filter expressions."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _collection_name(scope_hash: str) -> str:
    """Build the per-scope Milvus collection name (`mem_<scope_hash>`).

    Milvus collection names must match ``[A-Za-z_][A-Za-z0-9_]*``. `scope_hash`
    is a 12-char sha1 prefix (hex), which is already a safe suffix.
    """
    safe = "".join(c if c.isalnum() else "_" for c in scope_hash)
    return f"mem_{safe}"


class VectorStore:
    """Thin async-friendly wrapper around ``pymilvus.MilvusClient``.

    Each scope has its own collection. The store is synchronous internally
    (Milvus Lite has no async client) but the public surface is plain Python
    that callers can wrap in ``asyncio.to_thread`` if needed.
    """

    _QUERY_FIELDS: ClassVar[list[str]] = [
        "chunk_id",
        "memory_id",
        "chunk_idx",
        "content",
        "source",
        "heading",
        "start_line",
        "end_line",
    ]

    def __init__(
        self,
        db_path: Path | str | None,
        embedder: Embedder,
        *,
        token: str | None = None,
    ) -> None:
        try:
            from pymilvus import MilvusClient  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "pymilvus is required for memoryd.search.vector; "
                "install via `uv add pymilvus milvus-lite`."
            ) from exc
        from pymilvus import MilvusClient

        if db_path is None:
            db_path = _db_path()
        uri = str(db_path)
        is_local = not uri.startswith(("http", "tcp"))

        if is_local and sys.platform == "win32" and uri != ":memory:":
            raise RuntimeError(
                "milvus-lite does not support Windows (no wheels on PyPI).\n"
                "Run a remote Milvus server instead, e.g.:\n"
                "  docker run -d -p 19530:19530 milvusdb/milvus:latest standalone\n"
                "Then pass the URI: VectorStore('http://localhost:19530', embedder)."
            )

        if is_local and uri != ":memory:":
            Path(uri).expanduser().parent.mkdir(parents=True, exist_ok=True)
            uri = str(Path(uri).expanduser())

        connect_kwargs: dict[str, Any] = {"uri": uri}
        if token:
            connect_kwargs["token"] = token
        try:
            self._client = MilvusClient(**connect_kwargs)
        except Exception as exc:
            if is_local:
                raise RuntimeError(
                    f"failed to open Milvus Lite database at {uri!r}: {exc}"
                ) from exc
            raise

        self._uri = uri
        self._is_lite = is_local
        self._embedder = embedder
        self._dim = embedder.dim
        self._ensured: set[str] = set()

    # ----- collection lifecycle ----------------------------------------------

    def _ensure_collection(self, scope_hash: str) -> str:
        """Create the per-scope collection if missing; return its name."""
        coll = _collection_name(scope_hash)
        if coll in self._ensured:
            return coll
        if self._client.has_collection(coll):
            self._load(coll)
            self._ensured.add(coll)
            return coll

        from pymilvus import DataType, Function, FunctionType

        schema = self._client.create_schema(
            enable_dynamic_field=True,
            description=f"memoryd chunks for scope {scope_hash}",
        )
        schema.add_field(
            field_name="chunk_id",
            datatype=DataType.VARCHAR,
            max_length=64,
            is_primary=True,
        )
        schema.add_field(
            field_name="memory_id", datatype=DataType.VARCHAR, max_length=128
        )
        schema.add_field(field_name="chunk_idx", datatype=DataType.INT64)
        schema.add_field(
            field_name="content",
            datatype=DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
        )
        schema.add_field(
            field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=self._dim
        )
        schema.add_field(
            field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR
        )
        schema.add_field(
            field_name="source", datatype=DataType.VARCHAR, max_length=256
        )
        schema.add_field(
            field_name="heading", datatype=DataType.VARCHAR, max_length=512
        )
        schema.add_field(field_name="start_line", datatype=DataType.INT64)
        schema.add_field(field_name="end_line", datatype=DataType.INT64)
        schema.add_function(
            Function(
                name="bm25_fn",
                function_type=FunctionType.BM25,
                input_field_names=["content"],
                output_field_names=["sparse_vector"],
            )
        )

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="embedding", index_type="FLAT", metric_type="COSINE"
        )
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )

        self._client.create_collection(
            collection_name=coll, schema=schema, index_params=index_params
        )
        self._load(coll)
        self._ensured.add(coll)
        return coll

    def _load(self, coll: str) -> None:
        try:
            self._client.load_collection(collection_name=coll)
        except TypeError:
            self._client.load_collection(coll)

    # ----- write side --------------------------------------------------------

    def upsert_chunk(
        self,
        scope_hash: str,
        memory_id: str,
        chunk_idx: int,
        text: str,
        metadata: dict[str, Any],
    ) -> str:
        """Embed and upsert a single chunk; return the chunk's primary key.

        ``metadata`` may contain ``source`` (str), ``heading`` (str),
        ``start_line`` (int), ``end_line`` (int), ``content_hash`` (str) and
        any extra dynamic fields.
        """
        coll = self._ensure_collection(scope_hash)
        embedding = self._embed_sync(text)

        content_hash = str(metadata.get("content_hash", ""))
        chunk_id = compute_chunk_id(
            source=memory_id,
            start_line=int(metadata.get("start_line", chunk_idx)),
            end_line=int(metadata.get("end_line", chunk_idx)),
            content_hash=content_hash or text,
            model=self._embedder.model_name,
        )

        row: dict[str, Any] = {
            "chunk_id": chunk_id,
            "memory_id": memory_id,
            "chunk_idx": int(chunk_idx),
            "content": text,
            "embedding": embedding,
            "source": str(metadata.get("source", ""))[:256],
            "heading": str(metadata.get("heading", ""))[:512],
            "start_line": int(metadata.get("start_line", 0)),
            "end_line": int(metadata.get("end_line", 0)),
        }
        self._client.upsert(collection_name=coll, data=[row])
        return chunk_id

    def upsert_chunks(
        self,
        scope_hash: str,
        memory_id: str,
        chunks: list[dict[str, Any]],
    ) -> list[str]:
        """Batch variant of :meth:`upsert_chunk`.

        Each item in *chunks* must include ``text`` and may include
        ``chunk_idx`` and a ``metadata`` dict (same shape as
        :meth:`upsert_chunk`).
        """
        if not chunks:
            return []
        coll = self._ensure_collection(scope_hash)
        texts = [c["text"] for c in chunks]
        embeddings = self._embed_batch_sync(texts)

        rows: list[dict[str, Any]] = []
        ids: list[str] = []
        for i, c in enumerate(chunks):
            md = c.get("metadata", {}) or {}
            chunk_idx = int(c.get("chunk_idx", i))
            content_hash = str(md.get("content_hash", ""))
            cid = compute_chunk_id(
                source=memory_id,
                start_line=int(md.get("start_line", chunk_idx)),
                end_line=int(md.get("end_line", chunk_idx)),
                content_hash=content_hash or c["text"],
                model=self._embedder.model_name,
            )
            ids.append(cid)
            rows.append(
                {
                    "chunk_id": cid,
                    "memory_id": memory_id,
                    "chunk_idx": chunk_idx,
                    "content": c["text"],
                    "embedding": embeddings[i],
                    "source": str(md.get("source", ""))[:256],
                    "heading": str(md.get("heading", ""))[:512],
                    "start_line": int(md.get("start_line", 0)),
                    "end_line": int(md.get("end_line", 0)),
                }
            )
        self._client.upsert(collection_name=coll, data=rows)
        return ids

    # ----- read side ---------------------------------------------------------

    def search(
        self,
        scope_hash: str,
        query: str,
        *,
        top_k: int = 10,
        filter_expr: str = "",
    ) -> list[dict[str, Any]]:
        """Hybrid search (dense + BM25 + RRF) over the scope's collection.

        Returns at most *top_k* hits as plain dicts. Scores are normalised to
        ``[0, 1]``: the theoretical max RRF score is ``num_retrievers/(k+1)``.
        Empty collection returns ``[]``.
        """
        coll = _collection_name(scope_hash)
        if not self._client.has_collection(coll):
            return []
        self._load(coll)

        # BM25 crashes on empty collections (avgdl=0 → NaN).
        stats = self._client.get_collection_stats(coll)
        if int(stats.get("row_count", 0)) == 0:
            return []

        from pymilvus import AnnSearchRequest, RRFRanker

        query_embedding = self._embed_sync(query)

        req_kwargs: dict[str, Any] = {}
        if filter_expr:
            req_kwargs["expr"] = filter_expr

        dense_req = AnnSearchRequest(
            data=[query_embedding],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {}},
            limit=top_k,
            **req_kwargs,
        )
        bm25_req = AnnSearchRequest(
            data=[query] if query else [""],
            anns_field="sparse_vector",
            param={"metric_type": "BM25"},
            limit=top_k,
            **req_kwargs,
        )

        rrf_k = 60
        results = self._client.hybrid_search(
            collection_name=coll,
            reqs=[dense_req, bm25_req],
            ranker=RRFRanker(k=rrf_k),
            limit=top_k,
            output_fields=self._QUERY_FIELDS,
        )
        if not results or not results[0]:
            return []
        max_rrf = 2 / (rrf_k + 1)
        return [
            {**hit["entity"], "score": hit["distance"] / max_rrf}
            for hit in results[0]
        ]

    def query(
        self, scope_hash: str, *, filter_expr: str = ""
    ) -> list[dict[str, Any]]:
        """Retrieve chunks by scalar filter (no vector required)."""
        coll = _collection_name(scope_hash)
        if not self._client.has_collection(coll):
            return []
        self._load(coll)
        return self._client.query(
            collection_name=coll,
            output_fields=self._QUERY_FIELDS,
            filter=filter_expr if filter_expr else 'chunk_id != ""',
        )

    # ----- delete side -------------------------------------------------------

    def delete_memory(self, scope_hash: str, memory_id: str) -> int:
        """Delete all chunks belonging to a memory id; return rows removed."""
        coll = _collection_name(scope_hash)
        if not self._client.has_collection(coll):
            return 0
        escaped = _escape_filter_value(memory_id)
        rows = self._client.query(
            collection_name=coll,
            filter=f'memory_id == "{escaped}"',
            output_fields=["chunk_id"],
        )
        ids = [r["chunk_id"] for r in rows]
        if not ids:
            return 0
        self._client.delete(collection_name=coll, ids=ids)
        return len(ids)

    def drop_scope(self, scope_hash: str) -> None:
        """Drop the entire collection for a scope (used by scope teardown)."""
        coll = _collection_name(scope_hash)
        if self._client.has_collection(coll):
            self._client.drop_collection(coll)
        self._ensured.discard(coll)

    def count(self, scope_hash: str) -> int:
        """Return total number of chunks indexed for a scope."""
        coll = _collection_name(scope_hash)
        if not self._client.has_collection(coll):
            return 0
        stats = self._client.get_collection_stats(coll)
        return int(stats.get("row_count", 0))

    # ----- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()
        if self._is_lite and self._uri != ":memory:":
            try:
                from milvus_lite.server_manager import server_manager_instance

                server_manager_instance.release_server(self._uri)
            except Exception:
                pass

    def __enter__(self) -> VectorStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ----- embedding helpers -------------------------------------------------

    def _embed_sync(self, text: str) -> list[float]:
        return self._run_async(self._embedder.embed_text(text))

    def _embed_batch_sync(self, texts: list[str]) -> list[list[float]]:
        return self._run_async(self._embedder.embed_batch(texts))

    @staticmethod
    def _run_async(coro: Any) -> Any:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        # Caller is already inside an event loop — run synchronously on a
        # dedicated thread to avoid `asyncio.run` from raising.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()


__all__ = ["VectorStore", "DEFAULT_DB_PATH"]
