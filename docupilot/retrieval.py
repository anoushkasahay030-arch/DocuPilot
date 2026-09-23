"""Hybrid retrieval → cross-encoder rerank, with per-sub-query quotas for cross-document questions."""

import math

from docupilot.index import embed
from docupilot.index.store import VectorStore
from docupilot.ingest.chunker import embed_text
from docupilot.models import RetrievedChunk


def retrieve(store: VectorStore, queries: list[str], session_id: str, *, file_names: list[str] | None = None,
             k: int = 8, prefetch_k: int = 40, candidates: int = 24) -> list[RetrievedChunk]:
    queries = [q for q in dict.fromkeys(q.strip() for q in queries) if q]
    if not queries:
        return []
    # With several sub-queries (e.g. "compare A and B") each one is guaranteed its own share of the context,
    # so one strongly-matching document can't crowd the other out.
    per_query = k if len(queries) == 1 else max(3, math.ceil(k / len(queries)))
    best: dict[str, RetrievedChunk] = {}
    for q in queries:
        cands = store.search(q, session_id, file_names=file_names, prefetch_k=prefetch_k, limit=candidates)
        scores = embed.rerank(q, [embed_text(c) for c in cands])
        ranked = sorted(zip(cands, scores), key=lambda cs: cs[1], reverse=True)[:per_query]
        for chunk, score in ranked:
            prev = best.get(chunk.chunk_id)
            if prev is None or score > prev.score:
                best[chunk.chunk_id] = RetrievedChunk(chunk=chunk, score=score, query=q)
    ranked_all = sorted(best.values(), key=lambda r: r.score, reverse=True)
    return ranked_all[:max(k, per_query * len(queries))]
