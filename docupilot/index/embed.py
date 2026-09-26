"""Local ONNX models via fastembed: dense + BM25 sparse embeddings and a cross-encoder reranker.

Nothing here calls a remote API — documents are indexed fully on-device.
"""

import logging
import math
import threading
from functools import lru_cache
from pathlib import Path

from fastembed import SparseEmbedding, SparseTextEmbedding, TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder

from docupilot.config import get_settings

log = logging.getLogger(__name__)
_lock = threading.Lock()
# Cross-encoder input cap (~400 tokens); the relevant part of a chunk is almost always near its start.
_RERANK_CHARS = 1600


def _cache_dir() -> str:
    d = get_settings().data_dir / "models"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _load(cls, name: str):
    """Loads from the local cache without touching the network; downloads only on first use."""
    cache = _cache_dir()
    with _lock:
        # fastembed downloads only the files it needs (e.g. a few BM25 stopword lists); newer huggingface_hub
        # rejects such partial snapshots offline, so prefer pointing fastembed at the cached snapshot directly.
        snapshots = sorted(Path(cache).glob(f"models--{name.replace('/', '--')}/snapshots/*"))
        if snapshots:
            try:
                return cls(name, cache_dir=cache, specific_model_path=str(snapshots[-1]))
            except Exception:  # noqa: BLE001
                pass
        try:
            return cls(name, cache_dir=cache, local_files_only=True)
        except Exception:  # noqa: BLE001 - not cached yet
            pass
        log.info("Downloading %s (first run only)", name)
        return cls(name, cache_dir=cache)


@lru_cache
def dense_model() -> TextEmbedding:
    return _load(TextEmbedding, get_settings().dense_model)


@lru_cache
def sparse_model() -> SparseTextEmbedding:
    return _load(SparseTextEmbedding, get_settings().sparse_model)


@lru_cache
def reranker() -> TextCrossEncoder:
    return _load(TextCrossEncoder, get_settings().rerank_model)


@lru_cache
def dense_dim() -> int:
    return len(next(iter(dense_model().embed(["dimension probe"]))))


def embed_passages(texts: list[str]) -> tuple[list[list[float]], list[SparseEmbedding]]:
    dense = [v.tolist() for v in dense_model().passage_embed(texts)]
    sparse = list(sparse_model().passage_embed(texts))
    return dense, sparse


def embed_query(text: str) -> tuple[list[float], SparseEmbedding]:
    dense = next(iter(dense_model().query_embed(text))).tolist()
    sparse = next(iter(sparse_model().query_embed(text)))
    return dense, sparse


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def rerank(query: str, docs: list[str]) -> list[float]:
    """Cross-encoder relevance per doc, squashed to [0, 1] so thresholds are model-agnostic."""
    if not docs:
        return []
    # Batches are padded to their longest member, so one long chunk makes a whole batch slow.
    # Sorting by length and using small batches keeps padding minimal (~4x faster on typical results).
    order = sorted(range(len(docs)), key=lambda i: len(docs[i]))
    raw = list(reranker().rerank(query, [docs[i][:_RERANK_CHARS] for i in order], batch_size=4))
    scores = [0.0] * len(docs)
    for i, s in zip(order, raw):
        scores[i] = _sigmoid(float(s))
    return scores


def warmup() -> None:
    dense_dim()
    sparse_model()
    rerank("warmup", ["warmup"])
