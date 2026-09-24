"""Qdrant (embedded, no server) with named dense + sparse vectors and RRF hybrid search."""

import uuid
from collections.abc import Callable
from pathlib import Path

from qdrant_client import QdrantClient, models

from docupilot.index import embed
from docupilot.ingest.chunker import embed_text
from docupilot.models import Chunk

COLLECTION = "chunks"


class VectorStore:
    def __init__(self, path: Path | None = None):
        if path is None:
            self.client = QdrantClient(location=":memory:")
        else:
            path.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(path))
        self._ensure()

    def _ensure(self) -> None:
        if self.client.collection_exists(COLLECTION):
            return
        self.client.create_collection(
            COLLECTION,
            vectors_config={"dense": models.VectorParams(size=embed.dense_dim(), distance=models.Distance.COSINE)},
            sparse_vectors_config={"sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )

    def reset(self) -> None:
        if self.client.collection_exists(COLLECTION):
            self.client.delete_collection(COLLECTION)
        self._ensure()

    @staticmethod
    def _point_id(chunk: Chunk) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{chunk.session_id}/{chunk.chunk_id}"))

    def upsert(self, chunks: list[Chunk], *, batch_size: int = 64,
               on_progress: Callable[[int, int], None] | None = None) -> None:
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]
            dense, sparse = embed.embed_passages([embed_text(c) for c in batch])
            points = [
                models.PointStruct(
                    id=self._point_id(c),
                    vector={"dense": d,
                            "sparse": models.SparseVector(indices=s.indices.tolist(), values=s.values.tolist())},
                    payload=c.payload(),
                )
                for c, d, s in zip(batch, dense, sparse)
            ]
            self.client.upsert(COLLECTION, points=points, wait=True)
            if on_progress:
                on_progress(min(start + batch_size, len(chunks)), len(chunks))

    @staticmethod
    def _filter(session_id: str, file_names: list[str] | None = None,
                file_id: str | None = None) -> models.Filter:
        must: list[models.Condition] = [models.FieldCondition(key="session_id",
                                                              match=models.MatchValue(value=session_id))]
        if file_names:
            must.append(models.FieldCondition(key="file_name", match=models.MatchAny(any=file_names)))
        if file_id:
            must.append(models.FieldCondition(key="file_id", match=models.MatchValue(value=file_id)))
        return models.Filter(must=must)

    def search(self, query: str, session_id: str, *, file_names: list[str] | None = None,
               prefetch_k: int = 40, limit: int = 24) -> list[Chunk]:
        """Hybrid search: dense (semantic) + BM25 (keyword) candidates fused with Reciprocal Rank Fusion."""
        dense, sparse = embed.embed_query(query)
        flt = self._filter(session_id, file_names)
        res = self.client.query_points(
            COLLECTION,
            prefetch=[
                models.Prefetch(query=dense, using="dense", limit=prefetch_k, filter=flt),
                models.Prefetch(query=models.SparseVector(indices=sparse.indices.tolist(),
                                                          values=sparse.values.tolist()),
                                using="sparse", limit=prefetch_k, filter=flt),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=flt,
            limit=limit,
            with_payload=True,
        )
        return [Chunk.from_payload(p.payload or {}) for p in res.points]

    def file_chunks(self, session_id: str, file_names: list[str] | None = None, *, max_scan: int = 5000) -> list[Chunk]:
        """All chunks of the given files in document order (used for whole-file summaries)."""
        out: list[Chunk] = []
        offset = None
        while len(out) < max_scan:
            points, offset = self.client.scroll(COLLECTION, scroll_filter=self._filter(session_id, file_names),
                                                limit=256, offset=offset, with_payload=True)
            out += [Chunk.from_payload(p.payload or {}) for p in points]
            if offset is None:
                break
        return sorted(out, key=lambda c: (c.file_name, int(c.chunk_id.rsplit(":", 1)[1])))

    def delete_file(self, session_id: str, file_id: str) -> None:
        self.client.delete(COLLECTION, points_selector=models.FilterSelector(
            filter=self._filter(session_id, file_id=file_id)))

    def delete_session(self, session_id: str) -> None:
        self.client.delete(COLLECTION, points_selector=models.FilterSelector(filter=self._filter(session_id)))

    def count(self, session_id: str) -> int:
        return self.client.count(COLLECTION, count_filter=self._filter(session_id), exact=True).count
