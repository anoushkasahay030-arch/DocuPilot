"""The LangGraph wiring:

    router ─┬─ retriever ─┐
            ├─ table ─────┼─ synthesis ─┬─ verifier ─┬─ finalize
            └─ (both) ────┘             │            └─ retry (once) → retriever/table
                                        ├─ retry (nothing relevant) → retriever/table
                                        └─ chitchat / I-don't-know → finalize
"""

from collections.abc import Awaitable, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

from docupilot.agents.context import Deps
from docupilot.agents.retriever import retriever_node
from docupilot.agents.router import router_node
from docupilot.agents.state import GraphState, fresh_turn
from docupilot.agents.synthesis import synthesis_node
from docupilot.agents.table import table_node
from docupilot.agents.verifier import verifier_node

_STATE_TYPES = [("docupilot.models", n) for n in ("Chunk", "RetrievedChunk", "TableResult", "Citation")]


def fanout(state: GraphState) -> list[str]:
    route = state.get("route", "docs")
    if route == "chitchat":
        return ["synthesis"]
    if state.get("attempt", 0) > 0:
        # Retries only redo the part that can improve; table results are kept.
        return ["table"] if route == "table" else ["retriever"]
    return {"docs": ["retriever"], "summary": ["retriever"], "table": ["table"],
            "both": ["retriever", "table"]}[route]


def after_synthesis(state: GraphState) -> list[str] | str:
    if state.get("needs_retry"):
        return fanout(state)
    if state.get("route") == "chitchat" or state.get("idk"):
        return "finalize"
    return "verifier"


def after_verifier(state: GraphState) -> list[str] | str:
    return fanout(state) if state.get("needs_retry") else "finalize"


async def finalize_node(state: GraphState) -> GraphState:
    return {"history": [{"role": "user", "content": state["question"]},
                        {"role": "assistant", "content": state.get("answer", "")}]}


def build_graph(checkpointer=None):
    g = StateGraph(GraphState)
    g.add_node("router", router_node)
    g.add_node("retriever", retriever_node)
    g.add_node("table", table_node)
    g.add_node("synthesis", synthesis_node)
    g.add_node("verifier", verifier_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "router")
    g.add_conditional_edges("router", fanout, ["retriever", "table", "synthesis"])
    g.add_edge("retriever", "synthesis")
    g.add_edge("table", "synthesis")
    g.add_conditional_edges("synthesis", after_synthesis, ["retriever", "table", "verifier", "finalize"])
    g.add_conditional_edges("verifier", after_verifier, ["retriever", "table", "finalize"])
    g.add_edge("finalize", END)

    if checkpointer is None:
        checkpointer = MemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=_STATE_TYPES))
    return g.compile(checkpointer=checkpointer)


EventHandler = Callable[[dict], Awaitable[None]]


async def ask(graph, deps: Deps, thread_id: str, question: str, on_event: EventHandler | None = None) -> GraphState:
    """Runs one conversational turn, forwarding agent events (steps, tokens) to `on_event`."""
    config: RunnableConfig = {"configurable": {"thread_id": thread_id, "deps": deps}, "recursion_limit": 25}
    async for event in graph.astream(fresh_turn(question), config, stream_mode="custom"):
        if on_event:
            await on_event(event)
    return (await graph.aget_state(config)).values
