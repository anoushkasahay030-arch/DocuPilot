"""Retrieval agent: hybrid search + rerank, widening the search on retry. Also serves whole-file summaries."""

import asyncio

from langchain_core.runnables import RunnableConfig

from docupilot.agents.context import deps, emit
from docupilot.agents.state import GraphState
from docupilot.models import RetrievedChunk
from docupilot.retrieval import retrieve

SUMMARY_CHUNKS = 16


def _sample_evenly(items: list, n: int) -> list:
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


async def retriever_node(state: GraphState, config: RunnableConfig) -> GraphState:
    d = deps(config)
    ws, s = d.workspace, d.settings
    attempt = state.get("attempt", 0)

    if state.get("route") == "summary":
        targets = state.get("target_files") or [f.name for f in ws.doc_files] or None
        chunks = await asyncio.to_thread(ws.store.file_chunks, ws.session_id, targets)
        # Evenly spaced coverage of each file (beginning, middle, end) instead of top-k similarity.
        per_file: dict[str, list] = {}
        for c in chunks:
            per_file.setdefault(c.file_name, []).append(c)
        quota = max(4, SUMMARY_CHUNKS // max(1, len(per_file)))
        picked = [RetrievedChunk(c, 1.0, "summary") for cs in per_file.values() for c in _sample_evenly(cs, quota)]
        emit("step", agent="Retrieval", title=f"Sampled {len(picked)} passages across {len(per_file)} file(s)",
             detail="\n".join(f"- {r.chunk.location()}" for r in picked))
        return {"chunks": picked}

    queries = list(state.get("queries") or [state["standalone"]])
    targets = [f for f in state.get("target_files") or [] if ws.file_by_name(f)]
    k, prefetch = s.top_k, s.prefetch_k
    if attempt > 0:
        # Retry: search wider and also try the user's original wording.
        k, prefetch = s.top_k * 2, s.prefetch_k * 2
        queries.append(state["question"])

    results = await asyncio.to_thread(retrieve, ws.store, queries, ws.session_id, ensure_files=targets,
                                      k=k, prefetch_k=prefetch, candidates=max(24, k * 3))
    title = f"Retrieved {len(results)} passages" + (" (widened search)" if attempt else "")
    emit("step", agent="Retrieval", title=title,
         detail="\n".join(f"- `{r.score:.2f}` {r.chunk.location()}" for r in results) or "No matches.")
    return {"chunks": results}
