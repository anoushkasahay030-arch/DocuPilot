# DocuPilot

DocuPilot is a multi-agent chat app for your files. Upload documents, slides, spreadsheets, images, code, or HTML, or import a web page, then ask questions across them. Answers use retrieved passages, include source citations, and pass through a verification step. Spreadsheet questions are computed with real SQL; image text and visual descriptions are extracted with a local vision model. Model mistakes remain possible; see the measured results and limitations below.

> **Local inference:** Qwen2.5 7B through Ollama, with no API keys. On the existing evaluation, the local model answered **23/27** answerable questions correctly with **24.4 s** median total latency on an M1 Mac with 16 GB RAM. See [Evaluation](#evaluation) for limitations and the historical Gemini baseline.

| | What you get |
|---|---|
| **Citations** | Each claim links to its file, **page number and section path** (e.g. `report.pdf, p. 3, 3 Products > 3.1 Atlas Arm`). Click a `[n]` marker to see the exact passage; PDF sources open at the cited page. |
| **Spreadsheets** | CSV and XLSX files become SQL tables. Totals, rankings and filters are **computed**, and the SQL is shown as the source. |
| **Trust** | A verifier checks every claim against its source, removes unsupported ones, and rates confidence. If the answer isn't in your files, it says so. |
| **Privacy** | Parsing, search, reranking and LLM inference run **locally** with the default Ollama configuration. No API key or hosted inference service is required. |

---

## Setup and running

### Prerequisites
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you if needed)
- [Ollama](https://ollama.com/download) installed and running locally
- Recommended starting hardware: Apple Silicon with 16 GB RAM, or an equivalent machine capable of running a quantized 7B model
- About 4.7 GB of disk for `qwen2.5:7b`, plus runtime memory for the model and its context
- About 200 MB of disk for the local models (embeddings, BM25, reranker), downloaded once on first run

### Install

Open the Ollama application before pulling the model, or run `OLLAMA_NO_CLOUD=1 ollama serve` in a separate terminal. This local-only server setting also disables Ollama cloud features. Do not start a second server if Ollama is already running.

```bash
git clone <this repo> && cd DocuPilot
uv sync                     # creates .venv and installs dependencies
cp .env.example .env        # optional: defaults work without any API keys
ollama pull qwen2.5:7b      # one-time model download
```

### Run the app

```bash
uv run chainlit run app.py          # opens http://localhost:8000
```

1. Attach files with 📎 or by drag and drop. You can add more at any point in the conversation.
2. Ask questions, for example *"What was Q3 operating margin?"*, *"Total revenue by region"*, *"Compare the report's Q4 revenue with the sales sheet"* or *"Summarize the handbook"*.
3. Click any `[n]` to open its source. Expand the agent steps above an answer to see the route, the retrieved passages with scores, the SQL, and the per-claim fact-check.

### Supported files

| Content | Support |
|---|---|
| Documents | PDF, DOCX, TXT, Markdown (`.md`, `.markdown`) |
| Slides | PPTX text, grouped shapes, tables, native chart values, speaker notes and raster pictures; citations identify the slide |
| Spreadsheets | CSV, TSV, XLSX, XLSM; each sheet becomes a DuckDB table for structured filters, aggregates, rankings and joins |
| Images | PNG, JPEG, WebP, BMP, TIFF and GIF; OCR transcription plus visual descriptions of scans, charts and screenshots; multipage/animated images retain frame labels |
| Code | Python, JS/TS/JSX/TSX, Java, C/C++, C#, Go, Rust, Ruby, PHP, Swift, Kotlin, Scala, shell, SQL, R, Lua, Perl, Objective-C, Vue/Svelte, CSS/SCSS, JSON/YAML/TOML/XML/INI/CFG, Dockerfile and Makefile; whitespace and line ranges are preserved; code is read, never executed |
| HTML/web pages | `.html`, `.htm`, `.xhtml`, or pasted HTTP(S) URLs; visible static text, headings, tables, code, footer notices and image alt text; scripts and remote resources are not loaded |

For web imports, paste an HTTP(S) URL with your question, for example `https://example.com/page` followed by `Find the copyright text` on the next line. Links within a sentence also work. The app imports each linked page before answering; a URL by itself just loads the page for later questions. `/url https://example.com/page` remains supported. URLs inside inline or fenced code examples are not imported automatically.

Imports download the supplied pages (following up to five redirects); parsing and Q&A remain local. Each response is limited to `MAX_FILE_MB` and network operations have a 30-second timeout. The final URL is retained with the source. Repeated URLs, including fragment links and redirect aliases, reuse the page already loaded in that chat. Start a new chat to fetch a fresh snapshot. Visible footer copyright and legal notices are retained as citable content.

### Images and scanned documents

Install the separate local vision model before uploading images or scanned PDFs:

```bash
ollama pull qwen2.5vl:3b
```

Set `OLLAMA_VISION_MODEL` to use another locally installed vision model. The default [Qwen2.5-VL 3B](https://ollama.com/library/qwen2.5vl:3b) is a roughly 3.2 GB download and requires Ollama 0.7.0 or newer. Text routing, synthesis and verification still use the existing chat model.

Images are normalized locally, resized to at most `IMAGE_MAX_SIDE` pixels on either side, and sent to the configured Ollama server for structured OCR transcription and visual description. These are labelled as model-generated evidence in source panels. Scanned PDF pages without native text use the same path; blank pages are skipped. PowerPoint raster pictures also use it. Normal text documents do not require the vision model.

If vision is unavailable, image-only uploads fail with setup instructions. Mixed PDFs and slide decks keep their readable content and visibly report skipped visual content. After installing the model, start a new chat and reupload partially indexed documents. `USE_OCR=true` retains the optional Tesseract PDF OCR path; Tesseract must be installed locally with its language data.

To try it without your own files, generate all 14 synthetic sample files: the original six-document corpus plus slides, Python/TypeScript, HTML, an image-only scanned invoice, a receipt image, a chart and a screenshot. Generation runs locally and does not need Ollama:

```bash
uv run python -m eval.corpus eval/corpus --dataset all
```

### Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server address; inference stays local with this default |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Routing, SQL generation, verification, and default answer synthesis |
| `OLLAMA_STRONG_MODEL` | inherits `OLLAMA_MODEL` | Optional separate model for answer synthesis |
| `OLLAMA_VISION_MODEL` | `qwen2.5vl:3b` | Local image OCR and visual descriptions; separate model download |
| `OLLAMA_NUM_CTX` | `16384` | Context window in tokens; larger windows require more memory |
| `OLLAMA_TIMEOUT_S` | `300` | Request/read timeout in seconds; connection timeout is 5 seconds |
| `RERANK_MODEL` | `Xenova/ms-marco-MiniLM-L-12-v2` | Local cross-encoder (`BAAI/bge-reranker-base` is also supported, but slower) |
| `TOP_K` / `PREFETCH_K` | `8` / `40` | Passages given to the LLM / candidates fetched per search mode |
| `RELEVANCE_THRESHOLD` | `0.08` | Reranker score below which a passage counts as irrelevant |
| `CONFIDENCE_THRESHOLD` | `0.5` | Below this, the verifier triggers one wider retry |
| `MAX_FILES` / `MAX_FILE_MB` | `25` / `200` | Upload limits |
| `USE_OCR` | `false` | OCR for PDF pages without a text layer (Tesseract) |
| `IMAGE_MAX_SIDE` | `2048` | Maximum image width/height sent to the local vision model |
| `IMAGE_MAX_FRAMES` | `50` | Reject images above this frame/page count rather than silently truncating them |
| `DATA_DIR` | `data` | Local models, vector index and uploads |

No `.env` file is required. Existing `GEMINI_*` and `STRONG_THINKING_BUDGET` settings are ignored and can be removed. Keep unrelated `.env` settings when upgrading.

To change models, pull a local chat/instruct model and set `OLLAMA_MODEL` to its exact tag, for example `llama3.2:3b`, `mistral:7b`, or `phi3:mini`. Leave `OLLAMA_STRONG_MODEL` unset to use that same model everywhere. Alternative models need their own quality checks. Use local model tags; the application has no cloud fallback or API-key configuration.

### Tests and evaluation

```bash
uv run pytest                                 # offline after model caching; no API key or Ollama server needed
uv run python -m eval.run_eval --retrieval-only # retrieval metrics, offline
uv run python -m eval.run_eval                  # local Ollama pipeline; writes eval/results.json
uv run python -m eval.run_eval --only table,u01 # a subset, by question type or id
uv run python -m eval.run_eval --dataset formats --retrieval-only
uv run python -m eval.run_eval --dataset formats --only pptx,code,html
uv run python -m eval.run_eval --dataset vision # needs the local vision model as well as the chat model
```

The test suite covers the parsers, chunker, SQL engine and sandbox, and hybrid retrieval, and runs the **full agent graph** with a scripted fake LLM. It checks routing, SQL self-correction, parallel agents, the "I don't know" gate, low-confidence retries and conversation memory. New format tests use generated slides, images, scans, HTML and code; vision and web HTTP responses are mocked. The Ollama wrapper is tested against mock HTTP responses for structured output, image payloads, configuration, transient errors, cancellation and interrupted streams. Mocked image tests validate integration, not OCR or visual-model accuracy.

Evaluation output includes the configured model names, context size, client version, platform, accuracy, citations, and latency. Run the full evaluation before comparing models; the smaller local model may not reproduce the historical Gemini results.

| Evaluation dataset | Files | Questions | Content |
|---|---:|---:|---|
| `baseline` (default) | 6 | 31 | Original PDF/DOCX/MD/TXT/CSV/XLSX regression set |
| `formats` | 4 | 14 | PPTX text/tables/chart/notes, Python and TypeScript, static HTML, cross-file questions, summary and insufficient-evidence checks |
| `vision` | 4 | 10 | Two-page scanned PDF, receipt, colored bar chart and screenshot; OCR, chart values/color, spatial layout and missing information |
| `all` | 14 | 55 | Combined datasets |

`--only` accepts question IDs, types or format tags and applies to both retrieval and pipeline runs. Each run loads the entire selected dataset as retrieval context. The `vision` and `all` datasets require `OLLAMA_VISION_MODEL` **even with `--retrieval-only`**, because ingestion extracts evidence from pixels. Missing or partial extraction stops the evaluation instead of silently treating missing images as successful tests. The formats retrieval evaluation needs only the cached local search models.

Results are separated by dataset and mode: `eval/results.json` for the baseline pipeline, `eval/results-formats.json`, `eval/results-vision.json`, or `eval/results-all.json` for the new pipelines, and `eval/results-<dataset>-retrieval.json` for new retrieval runs (`eval/results-retrieval.json` for baseline). Use `--output <path>` to keep a named run. Metadata records dataset/version, selected questions, corpus files, model configuration and scoring version. All default result paths and generated corpus files are ignored by Git.

The [dataset guide](eval/DATASETS.md) documents the fixtures and question conventions. The [file-support validation record](eval/file-support-validation.md) records new checks separately from the historical model results.

### Troubleshooting
- **PDF, Office or code files are greyed out in the file picker**: restart Chainlit and hard-refresh the browser (Cmd+Shift+R on macOS) to load the updated upload filters. Chainlit's bundled picker ignores `application/*`, including extensions listed under it; the configuration uses concrete MIME types and explicit extensions instead.
- **`Too many packets in payload`**: the UI is configured to use WebSocket directly, avoiding Engine.IO's HTTP polling batch limit. After updating `.chainlit/config.toml`, restart Chainlit and hard-refresh the browser (Cmd+Shift+R on macOS) so existing tabs load the new transport setting.
- **Missing `en-GB` translation**: harmless; Chainlit uses the bundled `en-US` translation and `chainlit.md` instead.
- **Chainlit rejects `DEBUG=release`**: an existing `DEBUG` environment variable can conflict with Chainlit's boolean debug option. Launch with `env -u DEBUG uv run chainlit run app.py`.
- **Cannot connect to Ollama**: open the Ollama application or run `ollama serve`; check `OLLAMA_BASE_URL` and `ollama list`.
- **Model not found**: run `ollama pull qwen2.5:7b`, or pull the exact model configured in `OLLAMA_MODEL` / `OLLAMA_STRONG_MODEL`. The application does not download models automatically.
- **Timeout or memory pressure**: wait for model loading, close other memory-heavy applications, increase `OLLAMA_TIMEOUT_S`, or select a smaller model. Lowering `OLLAMA_NUM_CTX` reduces memory use but can truncate evidence for long questions.
- **Invalid structured output**: DocuPilot retries once with validation feedback, then reports an error. Retry the question or use a model with stronger JSON support.
- **Interrupted answer**: retry the question. Partial streamed answers are never silently replayed.
- **The first question is slow**: the local models load on first use. After that they are cached in `data/models/`.
- **An image or scanned PDF cannot be read**: install the model named by `OLLAMA_VISION_MODEL` and start Ollama. Alternatively, scanned PDF text can use `USE_OCR=true` with local Tesseract installed. See the upload report for skipped pages.

---

## Architecture

### Ingestion

```mermaid
flowchart LR
    U[Uploaded files] --> R{File type}
    R -->|PDF| P[pymupdf4llm<br/>layout-aware markdown<br/>per page]
    R -->|DOCX| W[python-docx<br/>body walk in order<br/>+ page breaks]
    R -->|MD / TXT| T[heading parser]
    R -->|PPTX| SL[slide text, charts, tables, notes]
    R -->|Images / scanned PDF pages| V[local Ollama vision<br/>OCR + visual descriptions]
    R -->|Code / HTML| H[line-preserving code / static HTML parser]
    R -->|CSV / XLSX| S[pandas<br/>header detection<br/>+ type inference]
    P & W & T & SL & V & H --> SEC[Sections<br/>text, code or table<br/>+ page + heading path]
    SEC --> CH[Chunker<br/>page and heading bounded<br/>tables kept whole]
    S --> DB[(DuckDB<br/>one table per sheet<br/>sandboxed)]
    S --> SC[Schema cards<br/>columns, ranges, samples]
    CH & SC --> EMB[Local embeddings<br/>bge-small dense + BM25 sparse]
    EMB --> Q[(Qdrant, embedded<br/>dense + sparse vectors)]
```

### Answering a question

```mermaid
flowchart TD
    Q([User question]) --> RT[Router / Planner<br/>standalone rewrite from memory<br/>route + sub-queries]
    RT -->|docs / summary| RET[Retrieval agent<br/>hybrid search, RRF fusion<br/>cross-encoder rerank]
    RT -->|table| TAB[Table agent<br/>text-to-SQL on DuckDB<br/>self-correcting]
    RT -->|both: in parallel| RET & TAB
    RT -->|chitchat| SYN
    RET --> SYN[Synthesis agent<br/>numbered sources, cited answer<br/>streamed]
    TAB --> SYN
    SYN -->|nothing relevant, 1st try| RET
    SYN -->|answer drafted| VER[Verifier<br/>claim-by-claim check<br/>confidence score]
    SYN -->|no evidence after retry| IDK[I don't know<br/>+ what was searched]
    VER -->|low confidence, 1st try| RET
    VER --> OUT([Answer + citations + confidence])
    IDK --> OUT
    OUT -.->|history| MEM[(Conversation memory<br/>LangGraph checkpointer)]
    MEM -.-> RT
```

### Components

| Concern | Implementation |
|---|---|
| Orchestration | LangGraph `StateGraph` with a checkpointer (conversation memory per chat) |
| LLM | Local Qwen2.5 7B through Ollama's async Python client; configurable models for routing/SQL/verification and synthesis. JSON-schema structured output, Pydantic validation, streaming, bounded retries |
| Embeddings | `BAAI/bge-small-en-v1.5` (dense) + `Qdrant/bm25` (sparse), ONNX via fastembed, local |
| Reranker | `Xenova/ms-marco-MiniLM-L-12-v2` cross-encoder, local |
| Vector DB | Qdrant in embedded mode (no server), named dense and sparse vectors, RRF fusion |
| Parsing | `pymupdf4llm` (PDF), `python-docx` (DOCX), `python-pptx` (PPTX), BeautifulSoup (static HTML), Pillow + local Ollama vision (images), custom Markdown/TXT and line-preserving code parsers |
| Tables | pandas + DuckDB (in-memory per chat, external access disabled) |
| UI | Chainlit (file upload, streaming, per-agent steps, citation side panels, PDF viewer) |

### The agents

| Agent | Model | Responsibility |
|---|---|---|
| **Router / Planner** | fast, JSON | Rewrites follow-ups into a standalone question (*"and its warranty?"* becomes *"What is Scout's warranty?"*). Picks a route: `docs`, `table`, `both`, `summary` or `chitchat`. Writes 1–4 search sub-queries and names the files likely to matter. Downgrades routes that can't run with the files loaded. |
| **Retrieval** | local | Hybrid search, then rerank, with a quota per sub-query. Guarantees each file the router named is represented. For `summary` it samples passages evenly across whole files instead. A retry doubles k and adds the user's original wording as a query. |
| **Table / Data** | fast, JSON | Generates 1–3 DuckDB queries from the tables' schema cards and runs them in the sandbox. SQL errors go back to the model for a fix, up to 2 times. The results become citable sources. |
| **Synthesis** | strong, streamed | Answers only from numbered sources, with `[n]` after every factual sentence. Gives both figures when a document and a spreadsheet disagree. If no source is relevant, it skips the LLM call entirely. |
| **Verifier** | fast, JSON | Splits the answer into atomic claims and judges each against its cited source (supported / partial / unsupported). Removes unsupported claims, computes a confidence score, and triggers one retry when confidence is low. |

---

## Key design decisions and trade-offs

### Retrieval and grounding

**1. Chunks never cross a page or heading boundary.**
*Why:* a citation then points to exactly one page and section, which is what makes "p. 3, 3 Products > 3.1 Atlas Arm" possible and precise.
*Trade-off:* short sections produce small chunks with less surrounding context. We offset this by prefixing each chunk's embedding text with `[file › heading path]`.

**2. Hybrid search (dense + BM25) with RRF, then a cross-encoder reranker.**
*Why:* dense vectors capture paraphrase, while BM25 catches rare exact tokens such as supplier names, SKUs and codes that embeddings blur. The reranker then produces a calibrated relevance score, which the "I don't know" gate depends on.
*Trade-off:* reranking costs about 0.3 s per query on CPU and adds a model to ship.

**3. Tables are linearized before embedding and reranking.**
*Why:* cross-encoders are trained on prose and score raw markdown tables terribly. A perfectly relevant quarterly-results table scored 0.05. Rewriting each row as `Quarter: Q3; Revenue ($M): 104.7; Operating margin: 13.1%` raised that to 0.99. Users still see the markdown.
*Trade-off:* the embedded text differs from the displayed text, which adds a little complexity.

**4. A small, fast reranker, measured rather than assumed.**

| Reranker | Latency per query | Relevant vs. off-topic score |
|---|---|---|
| `bge-reranker-base` | 2.9 s | 0.74–1.0 vs ≤ 0.003 |
| `jina-v1-turbo` | 0.6 s | 0.38–0.87 vs ≤ 0.04 |
| **`ms-marco-MiniLM-L-12`** (default) | 1.0 s, 0.29 s after length-sorting | ≥ 0.99 vs 0.0 |

All three got the top result right on every query. MiniLM separates relevant from irrelevant most sharply, which makes the relevance threshold robust.
*Trade-off:* it's trained on English web search, so non-English documents will rank worse (see [limitations](#known-limitations)).

We also sort candidates by length and rerank in batches of 4. Batches are padded to their longest member, so one long chunk slowed every short one; this cut median retrieval latency from 967 ms to 287 ms.

**5. The router's file guesses are a hint, not a filter.**
*Why:* the first version filtered search to the files the router named. On *"When will the Austin facility open, and is it in the annual report?"* it named only the report, and the board minutes holding the answer were never searched. Now the named files are guaranteed to appear, and all files are searched.
*Trade-off:* the context can include slightly more off-target passages.

**6. Keep the top 4 passages once a question is answerable.**
*Why:* the reranker scores each passage against the *whole* question. For multi-hop questions (*"What supplier risk does the report describe, and who was assigned to address it?"*) the passage naming the person scored 0.0 and was dropped. Now, once any passage clears the threshold, the top 4 are kept regardless.
*Trade-off:* more noise reaches the LLM. The verifier is the safeguard.

### Spreadsheets

**7. Spreadsheets are SQL tables, not text.**
*Why:* LLMs can't reliably sum 120 rows from a text dump. DuckDB computes exact answers, and the SQL itself is shown to the user as the source. The loader handles real-world mess: it detects title rows above the header, drops blank rows and columns, and converts `"$1,200"` to a number, `yes`/`no` to booleans and date strings to dates.
*Trade-off:* text-to-SQL can misread the question. Each query, its result and any self-corrections are visible in the UI so users can check them.

**8. Defence-in-depth SQL sandbox.**
*Why:* the model generates code that we execute. A validation step allows only a single `SELECT`/`WITH` statement, and a row limit and timeout apply. The real boundary is DuckDB's `enable_external_access = false`, which blocks reading files or URLs even if the checks are bypassed and can't be re-enabled at runtime.
*Trade-off:* the validation is intentionally conservative. A column literally named `set` or `load` would be rejected.

### Trust

**9. Separate verification pass with claim-level checks.**
*Why:* self-consistency at generation time isn't enough. A second, structured pass lists each claim with its citation and verdict, which gives a confidence score, lets us remove unsupported claims, and shows its reasoning in the UI.
*Trade-offs:*
- It adds a model call after the answer finishes streaming. The answer appears immediately and the confidence badge follows; verification time depends on the local model and answer length.
- The verifier is from the same model family as the writer, so they can share blind spots.

**10. "I don't know" is enforced in code, not just in the prompt.**
*Why:* if no passage clears the relevance threshold, synthesis is skipped entirely, since an LLM with no evidence is exactly when it hallucinates. The search is widened once, and then the user gets a clear "I couldn't find that in your files", along with what was searched and the closest near-misses.
*Trade-off:* a question phrased very differently from the source text or routed incorrectly could be refused. Hybrid search and the widened retry help, but the local evaluation had 3/27 false refusals.

### Model and deployment choices

**11. One local model by default, with separate roles available.**
*Why:* Qwen2.5 7B handles routing, SQL, verification and synthesis without loading two models into memory. The existing fast/strong interfaces remain, and `OLLAMA_STRONG_MODEL` can select a different synthesis model.
*Trade-off:* the default model has measured routing errors. Median time to first token was 13.12 s on the M1 Mac; other model choices need their own quality and latency checks.

**12. Local embeddings and local LLM inference.**
*Why:* your whole corpus is indexed on-device. After the first model download, loading makes zero network calls, verified by logging outgoing HTTP. Only the handful of passages relevant to each question reach the local Ollama model.
*Trade-off:* dependencies and models must be downloaded initially, and Ollama must remain running. Subsequent inference uses local hardware with no API key.

**13. Embedded, per-session storage.**
*Why:* Qdrant runs embedded, and DuckDB and conversation memory live in memory per chat. No separate database service is needed; Ollama runs alongside the app for inference.
*Trade-off:* sessions don't survive a restart, and a single process owns the index (see [limitations](#known-limitations)).

**14. Parsing choices.**
*Why:* `pymupdf4llm` is fast (about 70 ms per page), keeps tables and heading levels, and runs locally. We preferred it to heavier ML parsers (Docling, Marker) to keep ingestion interactive.
*Trade-off:* complex layouts such as multi-column scientific papers, rotated tables or forms may parse less cleanly than with ML-based layout models.

---

## Evaluation

The default `baseline` dataset, `eval/questions.jsonl`, has 31 questions over the original sample corpus. The historical results in this section refer to that dataset; the added format and vision sets are separate:
- 16 single-document facts with an expected file and page
- 3 cross-document questions
- 6 spreadsheet calculations, with ground truth computed independently by pandas
- 2 multi-turn follow-ups
- 4 unanswerable questions

### Local Ollama results

Measured on 2026-09-26 with `qwen2.5:7b` (Q4_K_M), Ollama 0.32.5, a 16,384-token context, and a Mac mini with Apple M1 / 16 GB RAM. All 31 questions completed without API keys or runtime errors.

| Metric | Local result |
|---|---|
| Answer accuracy (answerable) | **23/27 (85.2%)** |
| Citation accuracy (expected file/page) | **23/27 (85.2%)** |
| Single-document / cross-document / spreadsheet / follow-up accuracy | 13/16 · 3/3 · 5/6 · 2/2 |
| Automated refusal score on unanswerable questions | **3/4** |
| False refusal on answerable questions | 3/27 |
| Median time to first token / total, including verification | **13.12 s / 24.4 s** |

Four answerable questions were routed incorrectly, including a customer-count question that produced an incorrect number through document lookup instead of SQL. The fourth unanswerable response safely redirected the question back to the files, but its wording did not match the evaluator's refusal regex. A separate short summary request passed with citations to all four report pages; a more detailed summary request incorrectly chose document lookup. These results do not match the historical Gemini baseline. See the [local evaluation report](eval/ollama-evaluation.md) for environment details, failed questions, and additional checks.

### Historical Gemini baseline

These measurements were recorded before the Ollama migration, using Gemini. They are not results for the current local defaults:

| Metric | Result |
|---|---|
| Answer accuracy (answerable) | **27/27 (100%)** |
| · single-document facts | 16/16 |
| · cross-document | 3/3 |
| · spreadsheet calculations | 6/6 |
| · multi-turn follow-ups | 2/2 |
| Citation accuracy (right file and page cited) | **27/27 (100%)** |
| Retrieval hit@8 | **21/21 (100%)** |
| "I don't know" on unanswerable | **4/4 (100%)** |
| False "I don't know" on answerable | **0/27** |
| Median time to first token / total (incl. verification) | 5.1 s / 7.3 s |

Retrieval alone (offline): top-1 correct file and page on 19/19 questions, with a median of 287 ms.

The first version scored 24/27, with 3/4 "I don't know" on unanswerable questions. Every miss was a cross-document question, and each one led to a design change (decisions 5 and 6 and the source-reconciliation prompt rule). **Caveat:** 31 questions over a synthetic corpus is a regression suite, not a benchmark. It proves each mechanism works and catches regressions, but it isn't evidence of accuracy on arbitrary real-world documents.

---

## Known limitations

**Formats and parsing**
- **Unsupported formats.** Legacy DOC/PPT/XLS, archives, audio and video are not supported.
- **Visual evidence is an extraction, not a pixel-level fact-check.** The verifier sees the vision model's text, so an OCR error or an invented description can survive verification. Tiny text, handwriting, dense charts and image downscaling can lose details. Visual extraction is performed at upload time; follow-up questions search that extraction. The recorded evaluation does not measure vision accuracy.
- **PDF and slide visuals.** Native PDF text pages use layout-aware text parsing; their embedded charts are not automatically interpreted. Export such charts as images to ask visual questions. PPTX native charts expose stored values, but SmartArt, embedded OLE objects, vector pictures and full slide-layout rendering are not supported. Tesseract integration still depends on the local installation.
- **Web pages.** Static HTML only: no JavaScript rendering, login session, crawling, CSS layout interpretation or remote image extraction. Save authenticated pages as HTML or upload a screenshot. URL import intentionally accesses the address supplied by the user, including intranet addresses; this is a local, single-user app, not a hardened public URL-fetching service.
- **Code.** Files are split along line boundaries, with long minified lines split to fit the chunk budget. There is no execution, repository-wide dependency resolution or language-server analysis.
- **DOCX page numbers are approximate** (shown as `p. ~N`) unless Word saved rendered page breaks. DOCX has no fixed pages, since pagination depends on the renderer.
- **Complex spreadsheets.**
  - Multi-row or merged headers and several tables stacked on one sheet aren't detected; each sheet is one table with one header row.
  - Formula cells use their cached values, so files generated by tools that don't store computed values will show empties.

**Answer quality**
- **English-centric retrieval.** Both the embedding model and the reranker are English-trained, so other languages will retrieve noticeably worse.
- **Summaries of long documents are shallow.** The summary route samples 16 passages evenly, which is fine for reports but misses detail in a 300-page document, because there is no map-reduce summarization.
- **Confidence is not calibrated.** It's a heuristic (claim support plus retrieval relevance), not a probability of correctness. In the local evaluation, an incorrect customer count received a confidence score of 0.73.
- **Verifier bias.** The verifier and the writer are the same model family, so a claim both misread the same way will pass.
- **Prompt injection.** Document text goes into prompts. A malicious document could try to steer answers. The SQL sandbox protects data access, but the answer text itself isn't protected.

**Deployment**
- **Ephemeral, single-process sessions.** Uploads, tables and memory are cleared on restart. There are no user accounts or persistence, and embedded Qdrant can't be shared by several app processes.
- **Scale.** Embedded Qdrant keeps vectors in memory. It's comfortable at tens of thousands of chunks per instance; bigger corpora need Qdrant server mode. Files are ingested one after another.
- **Initial downloads required.** Install dependencies and download the Ollama, embedding and reranker models before working offline. Subsequent inference requires the local Ollama service, with no API key. Local speed and answer quality depend on hardware and model size.

## Future work

**Near term**
- **Richer visual extraction.** Question-specific image inspection, native PDF chart interpretation, full slide rendering and measured OCR/vision quality.
- **More formats.** Audio/video transcripts and richer language-aware code indexing.
- **Highlight the cited span** inside the PDF viewer, using PyMuPDF text positions.
- **Map-reduce summaries** for long documents.

**Quality**
- **A bigger, real-world eval.** Public document-QA sets (financial reports, DocVQA-style scans), plus human-labelled citation checks.
- **Calibrate confidence** on that eval so the score means an actual probability of being correct.
- **An independent verifier model**, from a different family, to reduce shared blind spots.
- **Multi-row header and multi-table sheet detection** for spreadsheets.
- **Multilingual retrieval**, e.g. `bge-m3` and `jina-reranker-v2-base-multilingual`, which fastembed already supports.

**Deployment**
- **Persistence and multi-user support.** Qdrant server, DuckDB on disk, a persistent checkpointer, and authentication (all supported by Chainlit).
- **Lower latency** by running retrieval speculatively in parallel with the router.

---

## Project layout

```
app.py               Chainlit UI: uploads, streaming, agent steps, citations, PDF viewer
docupilot/
  config.py          settings (.env)
  llm.py             Local Ollama wrapper: JSON-schema output, validation, streaming, retries
  models.py          Section / Chunk / Citation / TableInfo …
  workspace.py       per-session files: parse → chunk → index; DuckDB tables
  retrieval.py       hybrid search + rerank + sub-query quotas + file guarantees
  prompts.py         all agent prompts
  agents/            router, retriever, table, synthesis, verifier, graph (LangGraph wiring)
  ingest/            PDF, Word, PPTX, image, code, HTML/web, text and tabular parsers; chunker
  index/             embed.py (local models), store.py (Qdrant)
eval/
  corpus.py          baseline corpus and dataset selection
  format_corpus.py   generated PPTX/code/HTML and image/scan fixtures
  questions*.jsonl   31 baseline + 14 format + 10 vision questions
  DATASETS.md        dataset contents, generation, scoring and evaluation commands
  run_eval.py        eval harness (retrieval-only or full pipeline)
tests/               pytest suite (offline)
```
