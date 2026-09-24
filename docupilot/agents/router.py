"""Router/Planner agent: resolves follow-ups into a standalone question and picks the specialist agent(s)."""

from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig

from docupilot import prompts
from docupilot.agents.context import deps, emit, format_history
from docupilot.agents.state import GraphState, Route


class RouteDecision(BaseModel):
    standalone_question: str
    route: Route
    target_files: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)


def _table_overview(ws) -> str:
    lines = []
    for t in ws.tables.tables.values():
        cols = ", ".join(c for c, _, _ in t.columns)
        lines.append(f"- {t.name} ({t.file_name}{', sheet ' + t.sheet if t.sheet else ''}; {t.row_count} rows): {cols}")
    return "\n".join(lines) or "(none)"


async def router_node(state: GraphState, config: RunnableConfig) -> GraphState:
    d = deps(config)
    ws = d.workspace
    question = state["question"]
    if not ws.files:
        emit("step", agent="Router", title="No files loaded", detail="Answering conversationally.")
        return {"standalone": question, "route": "chitchat", "target_files": [], "queries": []}

    prompt = (f"Files:\n{ws.describe()}\n\nSpreadsheet tables:\n{_table_overview(ws)}\n\n"
              f"Conversation so far:\n{format_history(state.get('history', []))}\n\n"
              f"Latest message: {question}")
    decision = await d.llm.generate_json(prompt, RouteDecision, system=prompts.ROUTER_SYSTEM)

    route = decision.route
    has_docs, has_tables = bool(ws.doc_files), bool(ws.tables.tables)
    # Keep the plan executable for what's actually loaded.
    if route in ("table", "both") and not has_tables:
        route = "docs"
    elif route == "both" and not has_docs:
        route = "table"
    elif route == "docs" and not has_docs and has_tables:
        route = "table"

    known = {f.name for f in ws.files.values()}
    targets = [f for f in decision.target_files if f in known]
    standalone = decision.standalone_question.strip() or question
    queries = list(dict.fromkeys([standalone, *[q for q in decision.search_queries if q.strip()]]))[:5]

    emit("step", agent="Router", title=f"Route: {route}",
         detail=f"**Standalone question:** {standalone}\n\n**Search queries:** "
                + "; ".join(queries) + (f"\n\n**Target files:** {', '.join(targets)}" if targets else ""))
    return {"standalone": standalone, "route": route, "target_files": targets, "queries": queries}
