"""Synthesis agent: one grounded, cited answer from all specialist results (streamed)."""

from langchain_core.runnables import RunnableConfig

from docupilot import prompts
from docupilot.agents.context import build_sources, deps, emit, format_history, render_sources, used_citations
from docupilot.agents.state import GraphState

IDK_PREFIX = "I couldn't find that in your files"


def is_idk(answer: str) -> bool:
    return answer.lstrip().lower().startswith(IDK_PREFIX.lower())


def idk_message(state: GraphState, threshold: float) -> str:
    lines = [f"{IDK_PREFIX}."]
    queries = state.get("queries") or [state["standalone"]]
    lines.append("I searched for: " + "; ".join(f"“{q}”" for q in queries[:4]) + ".")
    near = [r for r in state.get("chunks", []) if r.score < threshold][:2]
    if near:
        lines.append("The closest passages were in " + " and ".join(r.chunk.location() for r in near)
                     + ", but they don't answer the question.")
    failed = [t for t in state.get("table_results", []) if t.error]
    if failed:
        lines.append(f"A spreadsheet query also failed: {failed[0].error}")
    return "\n\n".join(lines)


async def _stream(d, prompt: str, system: str, model: str | None = None) -> str:
    parts = []
    async for tok in d.llm.stream(prompt, system=system, model=model):
        parts.append(tok)
        emit("token", text=tok)
    return "".join(parts).strip()


async def synthesis_node(state: GraphState, config: RunnableConfig) -> GraphState:
    d = deps(config)
    s = d.settings
    route = state.get("route", "docs")
    attempt = state.get("attempt", 0)

    if route == "chitchat":
        answer = await _stream(d, state["question"], prompts.CHITCHAT_SYSTEM.format(
            inventory=d.workspace.describe()), model=d.llm.fast)
        return {"answer": answer, "citations": [], "confidence": None, "idk": False, "needs_retry": False}

    sources = build_sources(state.get("chunks", []), state.get("table_results", []), s.relevance_threshold)
    if not sources:
        if attempt == 0:
            emit("step", agent="Synthesis", title="Nothing relevant found — widening the search", detail="")
            return {"needs_retry": True, "attempt": 1}
        answer = idk_message(state, s.relevance_threshold)
        emit("token", text=answer)
        return {"answer": answer, "citations": [], "confidence": None, "idk": True, "needs_retry": False}

    if attempt > 0:
        emit("reset")
    prompt = prompts.SYNTHESIS_USER.format(history=format_history(state.get("history", []), turns=2),
                                           sources=render_sources(sources), question=state["standalone"])
    answer = await _stream(d, prompt, prompts.SYNTHESIS_SYSTEM)

    if is_idk(answer) and attempt == 0 and route != "table":
        emit("step", agent="Synthesis", title="Answer not in the retrieved passages — widening the search",
             detail="")
        return {"needs_retry": True, "attempt": 1}
    return {"answer": answer, "citations": used_citations(answer, sources), "idk": is_idk(answer),
            "needs_retry": False}
