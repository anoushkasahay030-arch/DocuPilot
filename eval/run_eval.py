"""DocuPilot eval harness.

    uv run python -m eval.run_eval                   # full pipeline (needs local Ollama + model)
    uv run python -m eval.run_eval --retrieval-only  # offline: retrieval hit@k only
    uv run python -m eval.run_eval --dataset formats # new PPTX/code/HTML fixtures
    uv run python -m eval.run_eval --dataset vision  # images/scans; needs local vision model at ingestion

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
import platform
import re
import statistics
import time
from pathlib import Path
from datetime import datetime, timezone
from importlib.metadata import version

from docupilot.agents.context import Deps
from docupilot.agents.graph import ask, build_graph
from docupilot.config import get_settings
from docupilot.index.store import VectorStore
from docupilot.retrieval import retrieve
from docupilot.workspace import Workspace
from eval.corpus import DATASETS, build_corpus, table_facts

HERE = Path(__file__).parent
_IDK = re.compile(r"couldn'?t find|can(?:not|'t) answer|could not find|not (?:mentioned|specified|stated|included|available|provided|"
                  r"contain)|no information|doesn'?t (?:mention|specify|say|contain|include)|does not (?:mention|"
                  r"specify|say|contain|include)|don'?t (?:have|contain)|isn'?t (?:mentioned|specified)", re.I)


def norm(s: str) -> str:
    return re.sub(r"(?<=\d),(?=\d{3})", "", s).lower()


def load_questions(dataset: str = "baseline") -> list[dict]:
    if dataset not in DATASETS:
        raise ValueError(f"Unknown dataset: {dataset}")
    facts = table_facts()
    out = []
    names = {"baseline": "questions.jsonl", "formats": "questions_formats.jsonl", "vision": "questions_vision.jsonl"}
    for name in names if dataset == "all" else [dataset]:
        for line in (HERE / names[name]).read_text().splitlines():
            if line.strip():
                q = json.loads(line)
                q["dataset"] = name
                for field in ("expect", "expect_all"):
                    q[field] = [e.format(**facts) for e in q.get(field, [])]
                out.append(q)
    return out


def select_questions(questions: list[dict], only: set[str] | None) -> list[dict]:
    return [q for q in questions if not only or only.intersection({q["id"], q["type"], q.get("format", "")})]


def contains_expected(answer: str, expected: str) -> bool:
    # Citation numbers are not answer facts; neither is the 8 inside 8000 evidence for a charge of 8.
    answer = norm(re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", "", answer))
    expected = norm(expected)
    if re.fullmatch(r"-?\d+(?:\.\d+)?", expected):
        number = re.escape(expected) + (r"0*" if "." in expected else r"(?:\.0+)?")
        return bool(re.search(r"(?<![\w.\-])" + number + r"(?!\w|\.\d)", answer))
    return bool(re.search(r"(?<!\w)" + re.escape(expected) + r"(?!\w)", answer))


def answer_correct(answer: str, q: dict) -> bool:
    every = q.get("expect_all", [])
    alternatives = q.get("expect", [])
    return (bool(every or alternatives)
            and all(contains_expected(answer, e) for e in every)
            and (not alternatives or any(contains_expected(answer, e) for e in alternatives)))


def source_matches(source, q: dict) -> bool:
    return (source.file_name in q["files"]
            and (q.get("page") is None or source.page == q["page"])
            and (not q.get("heading_contains")
                 or q["heading_contains"].lower() in source.heading_path.lower()))


def cites_expected(citations, q) -> bool:
    matching = {c.file_name for c in citations if source_matches(c, q)}
    return set(q["files"]) <= matching if q.get("require_all_files") else bool(matching)


def hit(chunks, q) -> bool:
    return cites_expected([r.chunk for r in chunks], q)


def retrieval_only(ws: Workspace, questions: list[dict], k: int) -> dict:
    rows = []
    for q in questions:
        if q["type"] not in ("single", "cross"):
            continue
        text = q.get("question") or q["turns"][-1]
        t = time.perf_counter()
        res = retrieve(ws.store, [text], ws.session_id, k=k)
        dt = time.perf_counter() - t
        if q["type"] == "cross":
            ok = hit(res, {**q, "require_all_files": True})
        else:
            ok = hit(res, q)
        top1 = bool(res) and source_matches(res[0].chunk, q)
        rows.append({"id": q["id"], "hit": ok, "top1": top1, "latency_s": round(dt, 4)})
        print(f"{q['id']:4} hit@{k}={'✓' if ok else '✗'} top1={'✓' if top1 else '✗'} {dt * 1000:5.0f} ms  {text}")
    n = len(rows)
    summary = {"hit_at_k": [sum(r["hit"] for r in rows), n], "top1": [sum(r["top1"] for r in rows), n],
               "latency_median_s": round(statistics.median(r["latency_s"] for r in rows), 4) if rows else None}
    if rows:
        print(f"\nhit@{k}: {summary['hit_at_k'][0]}/{n}   top-1: {summary['top1'][0]}/{n}   "
              f"median latency: {summary['latency_median_s'] * 1000:.0f} ms")
    else:
        print("No single-source or cross-document retrieval questions selected.")
    return {"summary": summary, "results": rows}


async def full_run(ws: Workspace, questions: list[dict], only: set[str] | None) -> list[dict]:
    from docupilot.llm import LLM

    settings = get_settings()
    deps = Deps(ws, LLM(settings), settings)
    graph = build_graph()
    results = []
    for q in select_questions(questions, only):
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
        r = {"id": q["id"], "type": q["type"], "dataset": q.get("dataset", "baseline"),
             "format": q.get("format"), "question": turns[-1], "answer": answer, "route": state.get("route"),
             "confidence": state.get("confidence"), "idk": is_idk, "latency_s": round(total, 2),
             "ttft_s": round(first_token[0], 2) if first_token else None, "attempt": state.get("attempt"),
             "citations": [c.label() for c in state.get("citations", [])],
             "source_locations": [{"file": c.file_name, "page": c.page, "heading": c.heading_path}
                                  for c in state.get("citations", [])]}
        if q["type"] == "unanswerable":
            r["correct"] = is_idk
        else:
            r["correct"] = not is_idk and answer_correct(answer, q)
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
    for t in ("single", "cross", "table", "followup", "summary"):
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
    ap.add_argument("--dataset", choices=DATASETS, default="baseline")
    ap.add_argument("--retrieval-only", action="store_true")
    ap.add_argument("--only", help="comma-separated question ids, types, or formats (e.g. pptx,code,html)")
    ap.add_argument("--k", type=int, default=get_settings().top_k)
    ap.add_argument("--output", type=Path, help="result JSON path; defaults to a separate file for each dataset/mode")
    args = ap.parse_args()
    if args.k < 1:
        ap.error("--k must be positive")
    only = {item.strip() for item in args.only.split(",") if item.strip()} if args.only else None
    questions = select_questions(load_questions(args.dataset), only)
    if not questions:
        ap.error("No questions matched --only in the selected dataset.")

    corpus = build_corpus(HERE / "corpus", dataset=args.dataset)
    ws = Workspace("eval", VectorStore(None))
    t = time.perf_counter()
    try:
        for p in corpus.values():
            info = ws.ingest(p)
            if info.warnings:
                raise RuntimeError(f"Incomplete extraction of {info.name}: {' '.join(info.warnings)}")
        print(f"Dataset: {args.dataset}; {len(questions)} selected questions\n"
              f"Ingested {len(ws.files)} files ({sum(f.chunks for f in ws.files.values())} chunks, "
              f"{len(ws.tables.tables)} tables) in {time.perf_counter() - t:.1f}s\n")

        if args.retrieval_only:
            output = retrieval_only(ws, questions, args.k)
        else:
            results = asyncio.run(full_run(ws, questions, None))
            output = {"summary": summarize(results), "results": results}
            print_summary(output["summary"])
    finally:
        ws.close()

    suffix = "" if args.dataset == "baseline" else f"-{args.dataset}"
    suffix += "-retrieval" if args.retrieval_only else ""
    out = args.output or HERE / f"results{suffix}.json"
    settings = get_settings()
    metadata = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "dataset": args.dataset,
        "dataset_version": 1,
        "scoring_version": 2,
        "mode": "retrieval-only" if args.retrieval_only else "pipeline",
        "question_ids": [q["id"] for q in questions],
        "corpus_files": [p.name for p in corpus.values()],
        "vision_model": settings.ollama_vision_model if args.dataset in {"vision", "all"} else None,
        "use_ocr": settings.use_ocr,
        "k": args.k if args.retrieval_only else settings.top_k,
        "provider": "ollama",
        "model": settings.ollama_model,
        "strong_model": settings.ollama_strong_model or settings.ollama_model,
        "num_ctx": settings.ollama_num_ctx,
        "timeout_s": settings.ollama_timeout_s,
        "ollama_client_version": version("ollama"),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"metadata": metadata, **output}, indent=2, default=str))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
