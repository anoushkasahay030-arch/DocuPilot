import operator
from typing import Annotated, Literal, Required, TypedDict

from docupilot.models import Citation, RetrievedChunk, TableResult

Route = Literal["docs", "table", "both", "summary", "chitchat"]


class GraphState(TypedDict, total=False):
    # Input for this turn
    question: Required[str]
    # Conversation memory, persisted across turns by the checkpointer
    history: Annotated[list[dict], operator.add]

    # Router
    standalone: Required[str]
    route: Route
    target_files: list[str]
    queries: list[str]

    # Specialist agents
    chunks: list[RetrievedChunk]
    table_results: list[TableResult]

    # Synthesis / verification
    answer: str
    citations: list[Citation]
    claims: list[dict]
    confidence: float | None
    attempt: int
    needs_retry: bool
    idk: bool


def fresh_turn(question: str) -> GraphState:
    """Resets per-turn fields; `history` is kept by the checkpointer."""
    return GraphState(question=question, standalone=question, route="docs", target_files=[], queries=[],
                      chunks=[], table_results=[], answer="", citations=[], claims=[], confidence=None,
                      attempt=0, needs_retry=False, idk=False)
