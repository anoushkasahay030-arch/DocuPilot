"""Agent-graph behaviour with a scripted fake LLM (no API key needed); retrieval and SQL are real."""

import asyncio
from dataclasses import dataclass, field

import pytest

from docupilot.agents.context import Deps
from docupilot.agents.graph import ask, build_graph
from docupilot.agents.router import RouteDecision
from docupilot.agents.table import SQLFix, SQLPlan, SQLQuery
from docupilot.agents.verifier import Claim, Verification
from docupilot.config import get_settings
from docupilot.index.store import VectorStore
from docupilot.workspace import Workspace
from eval.corpus import table_facts


@dataclass
class FakeLLM:
    route: RouteDecision
    answers: list[str] = field(default_factory=list)
    sql: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    verdicts: list[list[str]] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    fast: str = "fast"
    strong: str = "strong"

    async def generate_json(self, prompt, schema, *, system=None, model=None, temperature=0.0):
        self.calls.append((schema.__name__, prompt))
        if schema is RouteDecision:
            return self.route
        if schema is SQLPlan:
            return SQLPlan(queries=[SQLQuery(purpose="q", sql=s) for s in self.sql])
        if schema is SQLFix:
            return SQLFix(sql=self.fixes.pop(0))
        if schema is Verification:
            verdicts = self.verdicts.pop(0) if self.verdicts else ["supported"]
            return Verification(claims=[Claim(claim=f"c{i}", citations=[1], verdict=v)
                                        for i, v in enumerate(verdicts)], revised_answer="")
        raise AssertionError(schema)

    async def stream(self, prompt, *, system=None, model=None, temperature=0.2):
        self.calls.append(("stream", prompt))
        text = self.answers.pop(0) if self.answers else "ok [1]"
        for word in text.split(" "):
            yield word + " "


@pytest.fixture(scope="module")
def ws(corpus):
    w = Workspace("graph-test", VectorStore(None))
    for p in corpus.values():
        w.ingest(p)
    return w


def run(ws, llm, question, thread="t", graph=None):
    graph = graph or build_graph()
    events = []

    async def on_event(e):
        events.append(e)

    state = asyncio.run(ask(graph, Deps(ws, llm, get_settings()), thread, question, on_event))
    return state, events, graph


def route(r, q, queries=None, files=None):
    return RouteDecision(standalone_question=q, route=r, search_queries=queries or [q], target_files=files or [])


def test_docs_route_cites_correct_page(ws):
    llm = FakeLLM(route("docs", "Who supplies servo motors?"), answers=["Kinetix GmbH in Stuttgart [1]."])
    state, events, _ = run(ws, llm, "Who supplies servo motors?")
    assert state["answer"].startswith("Kinetix")
    [c] = state["citations"]
    assert (c.file_name, c.page) == ("acme_annual_report_2025.pdf", 4)
    assert state["confidence"] >= 0.8
    assert [e["agent"] for e in events if e["event"] == "step"] == ["Router", "Retrieval", "Verifier"]
    assert "".join(e["text"] for e in events if e["event"] == "token").strip() == state["answer"]


def test_table_route_runs_sql(ws):
    facts = table_facts()
    llm = FakeLLM(route("table", "Total revenue?"), sql=["SELECT SUM(revenue) AS total FROM sales_2025__orders"],
                  answers=[f"Total revenue was ${facts['total_revenue']} [1]."])
    state, _, _ = run(ws, llm, "Total revenue?")
    [t] = state["table_results"]
    assert t.error is None and t.file_names == ["sales_2025.xlsx"]
    assert facts["total_revenue"] in t.markdown
    assert state["citations"][0].kind == "sql"


def test_sql_self_correction(ws):
    llm = FakeLLM(route("table", "Units by product"),
                  sql=["SELECT product, SUM(qty) FROM sales_2025__orders GROUP BY 1"],
                  fixes=["SELECT product, SUM(units) AS units FROM sales_2025__orders GROUP BY 1"])
    state, _, _ = run(ws, llm, "Units by product")
    [t] = state["table_results"]
    assert t.error is None and t.attempts == 2 and "units" in t.sql


def test_unsafe_sql_never_executes(ws):
    llm = FakeLLM(route("table", "x"), sql=["DROP TABLE customers"], fixes=["DELETE FROM customers"] * 4)  # 2 fixes x (first try + one retry)
    state, _, _ = run(ws, llm, "x")
    assert state["table_results"][0].error
    assert "customers" in ws.tables.tables
    assert ws.tables.run_select("SELECT COUNT(*) n FROM customers").n[0] == 50
    assert state["idk"]


def test_both_route_runs_agents_in_parallel(ws):
    llm = FakeLLM(route("both", "Compare reported Q4 revenue with the orders data",
                        ["Q4 revenue annual report"]), sql=["SELECT SUM(revenue) FROM sales_2025__orders"],
                  answers=["Report says 117.3M [1]; orders total is X [2]."])
    state, _, _ = run(ws, llm, "compare")
    assert state["chunks"] and state["table_results"]
    kinds = {c.kind for c in state["citations"]}
    assert "sql" in kinds


def test_irrelevant_question_says_idk_without_llm_answer(ws):
    q = "What is the airspeed velocity of an unladen swallow?"
    llm = FakeLLM(route("docs", q))
    state, events, _ = run(ws, llm, q)
    assert state["idk"] and state["answer"].startswith("I couldn't find that in your files")
    assert not any(kind == "stream" for kind, _ in llm.calls)  # gated before synthesis
    assert state["attempt"] == 1  # widened once before giving up


def test_low_confidence_triggers_one_retry(ws):
    llm = FakeLLM(route("docs", "How many Scout units shipped?"), answers=["Lots [1].", "1,850 units [1]."],
                  verdicts=[["unsupported"], ["supported"]])
    state, events, _ = run(ws, llm, "How many Scout units shipped?")
    assert state["attempt"] == 1 and state["answer"] == "1,850 units [1]."
    assert any(e["event"] == "reset" for e in events)
    assert state["confidence"] >= 0.8


def test_unsupported_everything_becomes_idk(ws):
    llm = FakeLLM(route("docs", "Scout payload"), answers=["Made up [1].", "Still made up [1]."],
                  verdicts=[["unsupported"], ["unsupported"]])
    state, _, _ = run(ws, llm, "Scout payload")
    assert state["idk"] and state["citations"] == []


def test_memory_across_turns(ws):
    graph = build_graph()
    llm = FakeLLM(route("docs", "What is Scout's max payload?"), answers=["150 kg [1]."])
    run(ws, llm, "What is Scout's max payload?", thread="mem", graph=graph)
    llm2 = FakeLLM(route("docs", "What is Scout's warranty?"), answers=["2 years [1]."])
    state, _, _ = run(ws, llm2, "and its warranty?", thread="mem", graph=graph)
    router_prompt = next(p for kind, p in llm2.calls if kind == "RouteDecision")
    assert "What is Scout's max payload?" in router_prompt and "150 kg" in router_prompt
    assert [m["role"] for m in state["history"]] == ["user", "assistant", "user", "assistant"]
    # A different thread has no memory
    llm3 = FakeLLM(route("docs", "x"), answers=["y [1]."])
    run(ws, llm3, "hello", thread="other", graph=graph)
    assert "150 kg" not in next(p for kind, p in llm3.calls if kind == "RouteDecision")


def test_summary_samples_whole_file(ws):
    llm = FakeLLM(route("summary", "Summarize the annual report", files=["acme_annual_report_2025.pdf"]),
                  answers=["Summary [1][2]."])
    state, _, _ = run(ws, llm, "Summarize the annual report")
    pages = {r.chunk.page for r in state["chunks"]}
    assert pages == {1, 2, 3, 4}


def test_router_downgrades_impossible_routes(corpus):
    w = Workspace("docs-only", VectorStore(None))
    w.ingest(corpus["md"])
    llm = FakeLLM(route("table", "Scout battery"), answers=["10 hours [1]."])
    state, _, _ = run(w, llm, "Scout battery")
    assert state["route"] == "docs"
