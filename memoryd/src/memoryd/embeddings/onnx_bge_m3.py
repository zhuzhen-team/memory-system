"""Local ONNX bge-m3 embedder — runs on CPU, no API key required.

The first call lazily downloads the ``BAAI/bge-m3`` ONNX model (int8 quantised
variant by default) to ``~/.cache/memoryd/models/`` via huggingface-hub. Once
cached, subsequent runs are fully offline.

Adapted from memsearch onnx.py (MIT, Zilliz).
"""
from __future__ import annotations

import asyncio
import os
from functools import partial
from pathlib import Path
from typing import Any


_DEFAULT_BATCH_SIZE = 32
_DEFAULT_MAX_LEN = 8192


def _cache_dir() -> Path:
    override = os.environ.get("MEMORYD_MODEL_CACHE")
    base = Path(override) if override else (Path.home() / ".cache" / "memoryd" / "models")
    base.mkdir(parents=True, exist_ok=True)
    return base


class OnnxBgeM3Embedder:
    """ONNX Runtime embedder backed by ``BAAI/bge-m3``.

    Two ONNX output formats are supported:
    * Models with a ``dense_vecs`` output (pre-pooled, e.g. bge-m3 int8 export).
    * Models with ``last_hidden_state`` output — CLS pooling + L2 normalize.
    """

    def __init__(
        self,
        model: str = "BAAI/bge-m3",
        *,
        batch_size: int = 0,
        max_length: int = _DEFAULT_MAX_LEN,
    ) -> None:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise ImportError(
                "onnx-bge-m3 embedder requires onnxruntime; "
                "install with `uv add onnxruntime` or use the openai provider."
            ) from exc

        from huggingface_hub import hf_hub_download, list_repo_files
        from tokenizers import Tokenizer

        cache = _cache_dir()
        tok_path, model_path = self._download_files(
            model, cache, hf_hub_download, list_repo_files
        )

        self._tokenizer = Tokenizer.from_file(tok_path)
        self._tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        self._tokenizer.enable_truncation(max_length=max_length)

        self._session = ort.InferenceSession(model_path)
        self._output_names = [o.name for o in self._session.get_outputs()]
        self._has_dense_vecs = "dense_vecs" in self._output_names
        self._model = model

        # Probe dimension via a single forward pass.
        probe = self._encode(["hello"])
        self._dim = len(probe[0])
        self._batch_size = batch_size if batch_size > 0 else _DEFAULT_BATCH_SIZE

    @staticmethod
    def _download_files(
        model: str,
        cache: Path,
        hf_hub_download: Any,
        list_repo_files: Any,
    ) -> tuple[str, str]:
        """Download tokenizer + ONNX model, preferring local cache (offline).

        Returns ``(tokenizer_path, onnx_model_path)``.
        """
        cache_dir = str(cache)

        # Attempt 1: fully offline (sandbox-friendly).
        try:
            tok_path = hf_hub_download(
                model, "tokenizer.json", cache_dir=cache_dir, local_files_only=True
            )
            model_path = None
            onnx_file = None
            for candidate in ("model_quantized.onnx", "model.onnx"):
                try:
                    model_path = hf_hub_download(
                        model, candidate, cache_dir=cache_dir, local_files_only=True
                    )
                    onnx_file = candidate
                    break
                except Exception:
                    continue
            if model_path is None:
                raise FileNotFoundError("no cached ONNX model")
            import contextlib

            with contextlib.suppress(Exception):
                hf_hub_download(
                    model,
                    onnx_file + "_data",
                    cache_dir=cache_dir,
                    local_files_only=True,
                )
            return tok_path, model_path
        except Exception:
            pass

        # Attempt 2: online (first run or cache evicted).
        tok_path = hf_hub_download(model, "tokenizer.json", cache_dir=cache_dir)
        repo_files = list_repo_files(model)
        onnx_files = [f for f in repo_files if f.endswith(".onnx")]
        if not onnx_files:
            raise ValueError(f"no .onnx files found in {model}")
        if "model_quantized.onnx" in onnx_files:
            onnx_file = "model_quantized.onnx"
        elif "model.onnx" in onnx_files:
            onnx_file = "model.onnx"
        else:
            onnx_file = onnx_files[0]
        data_file = onnx_file + "_data"
        if data_file in repo_files:
            hf_hub_download(model, data_file, cache_dir=cache_dir)
        model_path = hf_hub_download(model, onnx_file, cache_dir=cache_dir)
        return tok_path, model_path

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
        loop = asyncio.get_running_loop()
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            chunk = await loop.run_in_executor(None, partial(self._encode, batch))
            out.extend(chunk)
        return out

    def _encode(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        encoded = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encoded])
        attention_mask = np.array([e.attention_mask for e in encoded])
        feed = {"input_ids": input_ids, "attention_mask": attention_mask}
        outputs = self._session.run(None, feed)

        if self._has_dense_vecs:
            idx = self._output_names.index("dense_vecs")
            embeddings = outputs[idx]
        else:
            idx = self._output_names.index("last_hidden_state")
            embeddings = outputs[idx][:, 0, :]

        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normalized = embeddings / np.where(norms == 0, 1.0, norms)
        return normalized.tolist()


__all__ = ["OnnxBgeM3Embedder"]
