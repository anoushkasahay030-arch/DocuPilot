"""Citation/Verification agent: claim-level fact check against cited sources → confidence score.

Unsupported claims are removed from the answer. When confidence is low on the first attempt the graph
retries with a wider search; if nothing is supported at all the answer becomes an explicit "I don't know".
"""

from typing import Literal

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from docupilot import prompts
from docupilot.agents.context import build_sources, deps, emit, render_sources, used_citations
from docupilot.agents.state import GraphState
from docupilot.agents.synthesis import idk_message


class Claim(BaseModel):
    claim: str
    citations: list[int] = Field(default_factory=list)
    verdict: Literal["supported", "partial", "unsupported"]


class Verification(BaseModel):
    claims: list[Claim] = Field(default_factory=list)
    revised_answer: str = ""


def score(claims: list[Claim], retrieval_signal: float) -> float:
    """75% claim support (partial = half credit), 25% how relevant the best cited source was."""
    if not claims:
        support = 1.0
    else:
        support = sum(1.0 if c.verdict == "supported" else 0.5 if c.verdict == "partial" else 0.0
                      for c in claims) / len(claims)
    return round(0.75 * support + 0.25 * retrieval_signal, 2)


def confidence_label(conf: float | None) -> str:
    if conf is None:
        return ""
    return "High" if conf >= 0.8 else "Medium" if conf >= 0.55 else "Low"


async def verifier_node(state: GraphState, config: RunnableConfig) -> GraphState:
    d = deps(config)
    s = d.settings
    answer = state.get("answer", "")
    sources = build_sources(state.get("chunks", []), state.get("table_results", []), s.relevance_threshold)

    v = await d.llm.generate_json(prompts.VERIFIER_USER.format(sources=render_sources(sources), answer=answer),
                                  Verification, system=prompts.VERIFIER_SYSTEM)
    cited = used_citations(answer, sources)
    conf = score(v.claims, max((c.score or 0.0) for c in cited) if cited else 0.0)
    counts = {k: sum(1 for c in v.claims if c.verdict == k) for k in ("supported", "partial", "unsupported")}
    detail = "\n".join(f"- {'✅' if c.verdict == 'supported' else '⚠️' if c.verdict == 'partial' else '❌'} "
                       f"{c.claim} {''.join(f'[{n}]' for n in c.citations)}" for c in v.claims)
    emit("step", agent="Verifier",
         title=f"{counts['supported']}/{len(v.claims)} claims supported · confidence {conf:.2f} "
               f"({confidence_label(conf)})", detail=detail)

    if conf < s.confidence_threshold and state.get("attempt", 0) == 0 and state.get("route") != "summary":
        emit("step", agent="Verifier", title="Low confidence — retrying with a wider search", detail="")
        return {"needs_retry": True, "attempt": 1}

    claims = [c.model_dump() for c in v.claims]
    if v.claims and counts["supported"] + counts["partial"] == 0:
        answer = idk_message(state, s.relevance_threshold)
        return {"answer": answer, "citations": [], "claims": claims, "confidence": conf, "idk": True,
                "needs_retry": False}
    if counts["unsupported"] and v.revised_answer.strip():
        answer = v.revised_answer.strip()
    return {"answer": answer, "citations": used_citations(answer, sources), "claims": claims, "confidence": conf,
            "needs_retry": False}
