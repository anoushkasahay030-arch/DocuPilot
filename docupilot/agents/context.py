"""Shared helpers: runtime deps, UI events, numbered source blocks and citation extraction."""

import re
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from docupilot.config import Settings
from docupilot.llm import LLM
from docupilot.models import Citation, RetrievedChunk, TableResult
from docupilot.workspace import Workspace

_CITE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass
class Deps:
    workspace: Workspace
    llm: LLM
    settings: Settings


def deps(config: RunnableConfig) -> Deps:
    return config["configurable"]["deps"]


def emit(event: str, **data: Any) -> None:
    """Sends a custom event to whoever streams the graph (the Chainlit UI). No-op outside streaming."""
    try:
        writer = get_stream_writer()
    except Exception:
        return
    writer({"event": event, **data})


@dataclass
class Source:
    n: int
    block: str
    citation: Citation


def build_sources(chunks: list[RetrievedChunk], tables: list[TableResult], threshold: float) -> list[Source]:
    sources: list[Source] = []
    for r in chunks:
        if r.score < threshold:
            continue
        c = r.chunk
        n = len(sources) + 1
        kind = "spreadsheet schema" if c.kind == "table_schema" else c.kind
        sources.append(Source(n, f"[{n}] {c.location()} ({kind})\n{c.text}", Citation(
            n=n, file_name=c.file_name, kind=c.kind, page=c.page, page_exact=c.page_exact,
            heading_path=c.heading_path, snippet=c.text, score=r.score)))
    for t in tables:
        if t.error or not t.markdown:
            continue
        n = len(sources) + 1
        origin = ", ".join(t.file_names) or "spreadsheet"
        block = (f"[{n}] {origin} — computed with SQL over table(s) {', '.join(t.tables)}\n"
                 f"Purpose: {t.question}\nSQL: {t.sql}\nResult ({t.row_count} rows):\n{t.markdown}")
        sources.append(Source(n, block, Citation(n=n, file_name=origin, kind="sql", snippet=t.markdown,
                                                 sql=t.sql, score=1.0)))
    return sources


def render_sources(sources: list[Source]) -> str:
    return "\n\n".join(s.block for s in sources)


def cited_numbers(answer: str) -> list[int]:
    seen: dict[int, None] = {}
    for m in _CITE.finditer(answer):
        for part in m.group(1).split(","):
            seen[int(part)] = None
    return list(seen)


def used_citations(answer: str, sources: list[Source]) -> list[Citation]:
    by_n = {s.n: s.citation for s in sources}
    return [by_n[n] for n in cited_numbers(answer) if n in by_n]


def format_history(history: list[dict], turns: int = 3, max_chars: int = 600) -> str:
    recent = history[-2 * turns:]
    if not recent:
        return "(none)"
    lines = []
    for m in recent:
        content = m["content"] if len(m["content"]) <= max_chars else m["content"][:max_chars] + " …"
        lines.append(f"{m['role'].upper()}: {content}")
    return "\n".join(lines)
