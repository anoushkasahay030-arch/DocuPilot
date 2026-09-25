"""DocuPilot eval harness.

    uv run python -m eval.run_eval                   # full pipeline (needs GEMINI_API_KEY)
    uv run python -m eval.run_eval --retrieval-only  # offline: retrieval hit@k only

Metrics
- retrieval hit@k     expected file (and page) among the retrieved passages (docs questions)
- citation accuracy   the answer cites the expected file (and page)
- answer accuracy     expected fact appears in the answer (numbers compared without separators)
- IDK rate            "I don't know" on unanswerable questions; false-IDK rate on answerable ones
- latency             time to first token and total time per question
"""

import argparse
from typing import Any
import asyncio
import json
import re
import statistics
import time
from pathlib import Path

from docupilot.agents.context import Deps
from docupilot.agents.graph import ask, build_graph
from docupilot.config import get_settings
from docupilot.index.store import VectorStore
from docupilot.retrieval import retrieve
from docupilot.workspace import Workspace
from eval.corpus import build_corpus, table_facts

HERE = Path(__file__).parent
_IDK = re.compile(r"couldn'?t find|can(?:not|'t) answer|could not find|not (?:mentioned|specified|stated|included|available|provided|"
                  r"contain)|no information|doesn'?t (?:mention|specify|say|contain|include)|does not (?:mention|"
                  r"specify|say|contain|include)|don'?t (?:have|contain)|isn'?t (?:mentioned|specified)", re.I)


def norm(s: str) -> str:
    return re.sub(r"(?<=\d),(?=\d{3})", "", s).lower()


def load_questions() -> list[dict]:
    facts = table_facts()
    out = []
    for line in (HERE / "questions.jsonl").read_text().splitlines():
        if line.strip():
            q = json.loads(line)
            q["expect"] = [e.format(**facts) for e in q.get("expect", [])]
            out.append(q)
    return out


def cites_expected(citations, q) -> bool:
    for c in citations:
        if c.file_name in q["files"] and (q.get("page") is None or c.page == q["page"] or q["type"] == "cross"):
            return True
    return False


def hit(chunks, q) -> bool:
    return any(r.chunk.file_name in q["files"] and (q.get("page") is None or r.chunk.page == q["page"])
               for r in chunks)


def retrieval_only(ws: Workspace, questions: list[dict], k: int) -> None:
    rows = []
    for q in questions:
        if q["type"] not in ("single", "cross", "followup"):
            continue
        text = q.get("question") or q["turns"][-1]
        if q["type"] == "followup":
            continue  # needs the router's rewrite; covered by the full run
        t = time.perf_counter()
        res = retrieve(ws.store, [text], ws.session_id, k=k)
        dt = time.perf_counter() - t
        if q["type"] == "cross":
            ok = set(q["files"]) <= {r.chunk.file_name for r in res}
        else:
            ok = hit(res, q)
        top1 = bool(res) and res[0].chunk.file_name in q["files"] and (q.get("page") in (None, res[0].chunk.page))
        rows.append((q["id"], ok, top1, dt))
        print(f"{q['id']:4} hit@{k}={'✓' if ok else '✗'} top1={'✓' if top1 else '✗'} {dt * 1000:5.0f} ms  {text}")
    n = len(rows)
    print(f"\nhit@{k}: {sum(r[1] for r in rows)}/{n}   top-1: {sum(r[2] for r in rows)}/{n}   "
          f"median latency: {statistics.median(r[3] for r in rows) * 1000:.0f} ms")


async def full_run(ws: Workspace, questions: list[dict], only: set[str] | None) -> list[dict]:
    from docupilot.llm import LLM

    settings = get_settings()
    deps = Deps(ws, LLM(settings), settings)
    graph = build_graph()
    results = []
    for q in questions:
        if only and q["id"] not in only and q["type"] not in only:
            continue
        turns = q.get("turns") or [q["question"]]
        thread = f"eval-{q['id']}"
        first_token: list[float] = []
        state: dict = {}
        t0 = time.perf_counter()
        for turn in turns:
            first_token.clear()
            t0 = time.perf_counter()

            async def on_event(e, t0=t0):
                if e["event"] == "token" and not first_token:
                    first_token.append(time.perf_counter() - t0)

            state = await ask(graph, deps, thread, turn, on_event)
        total = time.perf_counter() - t0
        answer = state.get("answer", "")
        is_idk = bool(state.get("idk")) or bool(_IDK.search(answer))
        r = {"id": q["id"], "type": q["type"], "question": turns[-1], "answer": answer, "route": state.get("route"),
             "confidence": state.get("confidence"), "idk": is_idk, "latency_s": round(total, 2),
             "ttft_s": round(first_token[0], 2) if first_token else None, "attempt": state.get("attempt"),
             "citations": [c.label() for c in state.get("citations", [])]}
        if q["type"] == "unanswerable":
            r["correct"] = is_idk
        else:
            expect_all = q.get("expect_all")
            if expect_all:
                r["correct"] = all(norm(e) in norm(answer) for e in expect_all)
            else:
                r["correct"] = any(norm(e) in norm(answer) for e in q["expect"])
            r["cited_ok"] = cites_expected(state.get("citations", []), q)
            if q["type"] != "table":
                r["retrieval_hit"] = hit(state.get("chunks", []), q)
        results.append(r)
        mark = "✓" if r["correct"] else "✗"
        conf = f"{r['confidence']:.2f}" if r["confidence"] is not None else "  - "
        print(f"{mark} {q['id']:4} {r['route'] or '':8} conf={conf} {r['latency_s']:5.1f}s  {turns[-1]}")
        if not r["correct"]:
            print(f"     ↳ {answer[:300]!r}")
    return results


def summarize(results: list[dict]) -> dict:
    def rate(rows, key):
        rows = [r for r in rows if key in r]
        return (sum(bool(r[key]) for r in rows), len(rows))

    answerable = [r for r in results if r["type"] != "unanswerable"]
    unanswerable = [r for r in results if r["type"] == "unanswerable"]
    summary: dict[str, Any] = {
        "answer_accuracy": rate(answerable, "correct"),
        "citation_accuracy": rate(answerable, "cited_ok"),
        "retrieval_hit": rate(answerable, "retrieval_hit"),
        "idk_on_unanswerable": rate(unanswerable, "correct"),
        "false_idk_on_answerable": (sum(r["idk"] for r in answerable), len(answerable)),
    }
    by_type = {}
    for t in ("single", "cross", "table", "followup"):
        rows = [r for r in answerable if r["type"] == t]
        if rows:
            by_type[t] = rate(rows, "correct")
    summary["by_type"] = by_type
    lat = [r["latency_s"] for r in results]
    ttft = [r["ttft_s"] for r in results if r["ttft_s"] is not None]
    summary["latency_median_s"] = round(statistics.median(lat), 2) if lat else None
    summary["ttft_median_s"] = round(statistics.median(ttft), 2) if ttft else None
    conf_ok = [r["confidence"] for r in answerable if r["correct"] and r["confidence"] is not None]
    conf_bad = [r["confidence"] for r in answerable if not r["correct"] and r["confidence"] is not None]
    summary["mean_conf_correct"] = round(statistics.mean(conf_ok), 2) if conf_ok else None
    summary["mean_conf_incorrect"] = round(statistics.mean(conf_bad), 2) if conf_bad else None
    return summary


def print_summary(s: dict) -> None:
    def fmt(pair):
        a, b = pair
        return f"{a}/{b} ({a / b:.0%})" if b else "n/a"

    print("\n| Metric | Result |\n|---|---|")
    print(f"| Answer accuracy (answerable) | {fmt(s['answer_accuracy'])} |")
    for t, pair in s["by_type"].items():
        print(f"| · {t} | {fmt(pair)} |")
    print(f"| Citation accuracy (right file/page cited) | {fmt(s['citation_accuracy'])} |")
    print(f"| Retrieval hit@k | {fmt(s['retrieval_hit'])} |")
    print(f"| \"I don't know\" on unanswerable | {fmt(s['idk_on_unanswerable'])} |")
    print(f"| False \"I don't know\" on answerable | {fmt(s['false_idk_on_answerable'])} |")
    print(f"| Median time to first token | {s['ttft_median_s']} s |")
    print(f"| Median total latency (incl. verification) | {s['latency_median_s']} s |")
    print(f"| Mean confidence: correct / incorrect | {s['mean_conf_correct']} / {s['mean_conf_incorrect']} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--retrieval-only", action="store_true")
    ap.add_argument("--only", help="comma-separated question ids or types")
    ap.add_argument("--k", type=int, default=get_settings().top_k)
    args = ap.parse_args()

    corpus = build_corpus(HERE / "corpus")
    ws = Workspace("eval", VectorStore(None))
    t = time.perf_counter()
    for p in corpus.values():
        ws.ingest(p)
    print(f"Ingested {len(ws.files)} files ({sum(f.chunks for f in ws.files.values())} chunks, "
          f"{len(ws.tables.tables)} tables) in {time.perf_counter() - t:.1f}s\n")

    questions = load_questions()
    if args.retrieval_only:
        retrieval_only(ws, questions, args.k)
        return
    results = asyncio.run(full_run(ws, questions, set(args.only.split(",")) if args.only else None))
    summary = summarize(results)
    print_summary(summary)
    out = HERE / "results.json"
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2, default=str))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
