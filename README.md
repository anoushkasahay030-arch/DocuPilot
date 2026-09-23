# DocuPilot

DocuPilot is a multi-agent chat app for your files. Upload PDFs, Word documents, Markdown, text files and spreadsheets, then ask questions across all of them. Every answer is grounded in your files, cites the file and page it came from, and is checked for hallucinations before you see it.

> **Status: MVP in progress.** Ingestion, hybrid retrieval, the spreadsheet engine and the tests are done. The agent graph and the Chainlit UI are being built next (see the [Roadmap](#roadmap)).

## Why it's different

| | DocuPilot |
|---|---|
| **Citations** | Each claim links to a file plus a **page number and section path** (e.g. `report.pdf, p. 3, 3 Products > 3.1 Atlas Arm`). A chunk never crosses a page or heading boundary, so a citation always points to exactly one place. |
| **Spreadsheets** | CSV and XLSX files become real SQL tables in DuckDB, so aggregations are **computed, not guessed**. Header detection skips title and blank rows. Values like `"$1,200"` are converted to numbers, `yes`/`no` to booleans and date strings to dates. |
| **Trust** | A verification agent checks each claim against its cited source and produces a **confidence score**. If the context doesn't contain the answer, you get a clear *"I don't know"*. |
| **Privacy** | Embedding, keyword indexing and reranking run **locally** (ONNX via fastembed). Only the few retrieved passages are sent to the LLM. |

## Architecture

```
            upload ─► parse (layout-aware) ─► chunk (page/heading-bounded) ─► embed locally ─► Qdrant (dense + BM25)
                                   └─ CSV/XLSX ─► DuckDB tables + schema cards ───────────────────────┘

question ─► Router ──┬─► Retrieval agent (hybrid RRF ─► cross-encoder rerank) ─┐
 (+ memory)          ├─► Table agent (text-to-SQL on DuckDB, self-correcting) ─┼─► Synthesis ─► Verifier ─► answer
                     └─► both, in parallel ─────────────────────────────────────┘      (cited)     (confidence)
```

| Component | Implementation |
|---|---|
| Orchestration | LangGraph |
| LLM | Google Gemini (`google-genai`). A Flash model handles routing, SQL and verification; a Pro model handles synthesis. Both are configurable. |
| Embeddings | `BAAI/bge-small-en-v1.5` (dense) + `Qdrant/bm25` (sparse), local |
| Reranker | `Xenova/ms-marco-MiniLM-L-12-v2` cross-encoder, local |
| Vector DB | Qdrant, embedded mode (no server) |
| Parsing | `pymupdf4llm` (layout-aware PDF → markdown with tables), `python-docx`, a Markdown/TXT parser |
| Tables | pandas + DuckDB, in-memory per session |
| UI | Chainlit |

### Engineering notes
- **Layout-aware chunking.**
  - PDFs are converted page by page to markdown, which preserves headings and tables. The heading hierarchy carries over across pages.
  - DOCX bodies are walked in document order, so paragraphs and tables stay interleaved. Page numbers come from Word's rendered page breaks when present; otherwise they're estimated from explicit breaks and labelled `p. ~N`.
  - Tables are never split mid-row. Oversized tables are split by rows with the header repeated.
- **Tables are linearized before embedding and reranking.**
  - Cross-encoders score raw markdown tables badly. A perfectly relevant quarterly-results table scored **0.05**.
  - Each row is rewritten as `Quarter: Q3; Revenue ($M): 104.7; Operating margin: 13.1%`, which lifts it to **0.74 with bge-reranker-base and 0.99 with MiniLM-L-12**.
  - The display text stays markdown.
- **Hybrid search.** Dense and BM25 candidates are fused with Reciprocal Rank Fusion, then reranked by the cross-encoder. BM25 catches rare exact terms such as supplier names and SKUs that dense vectors miss.
- **Per-sub-query quotas.** For cross-document questions (*"compare A with B"*), each sub-query gets its own share of the context, so one document can't crowd out the other.
- **Reranker choice (measured).** On the eval corpus, all three candidates got top-1 right on every query.

  | Reranker | Avg latency / query | Relevant vs. off-topic score |
  |---|---|---|
  | `bge-reranker-base` | 2.9 s | 0.74–1.0 vs ≤ 0.003 |
  | `jina-v1-turbo` | 0.6 s | 0.38–0.87 vs ≤ 0.04 (weaker separation) |
  | `ms-marco-MiniLM-L-12` (default) | 1.0 s | ≥ 0.99 vs 0.0 |

- **SQL sandbox.**
  - Generated SQL must be a single `SELECT`/`WITH` statement, and results are capped at a row limit with a timeout.
  - The DuckDB connection also runs with `enable_external_access = false`. Even SQL that slips past the checks can't read files or URLs, and the setting can't be switched back on at runtime.

## Supported files (MVP)
- **Documents:** `.pdf`, `.docx`, `.md`, `.txt`
- **Spreadsheets:** `.csv`, `.tsv`, `.xlsx`

Limits are configurable (`MAX_FILES`, `MAX_FILE_MB`).

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12. The first run downloads about 200 MB of local models into `data/models/`.

```bash
uv sync
cp .env.example .env        # then set GEMINI_API_KEY
```

## Tests

```bash
uv run pytest               # offline: parsers, chunker, SQL engine + sandbox, hybrid retrieval
```

To build the sample corpus (a PDF report, a DOCX handbook, an MD FAQ, TXT minutes, an XLSX with a title row, and a CSV):

```bash
uv run python -m eval.corpus eval/corpus
```

## Project layout

```
docupilot/
  config.py        settings (.env)
  llm.py           Gemini wrapper: JSON-schema output, streaming, retries
  models.py        Section / Chunk / Citation / TableInfo …
  workspace.py     per-session files: parse → chunk → index; DuckDB tables
  retrieval.py     hybrid search + rerank + sub-query quotas
  ingest/          pdf.py, word.py, text.py, tabular.py, chunker.py
  index/           embed.py (local models), store.py (Qdrant)
eval/corpus.py     deterministic multi-format test corpus with known answers
tests/             pytest suite
```

## Roadmap
- [x] Layout-aware parsing (PDF, DOCX, MD, TXT) and structure-bounded chunking
- [x] Spreadsheet engine (header detection, typing, sandboxed SQL)
- [x] Hybrid retrieval (dense + BM25 + RRF) with local reranking
- [ ] Agent graph: router, retrieval, table, synthesis and verification agents, plus memory
- [ ] Chainlit UI with streaming, per-agent steps, citation side panels and a PDF page viewer
- [ ] Eval harness (hit@k, citation accuracy, table exact match, IDK rate)
- [ ] Later phases: images and scanned PDFs (Gemini vision + Tesseract), PPTX, code files, audio, HTML
