# Repository guidance

DocuPilot is a local document-chat app: Chainlit UI, LangGraph orchestration,
Ollama inference, hybrid Qdrant retrieval, and DuckDB spreadsheet queries.
It supports documents, slides, spreadsheets, images/scans, code, static HTML,
and user-supplied web page URLs.
Use `README.md` for setup, architecture, design trade-offs, and evaluation context.

## Development commands

Use Python 3.12 (the version in `.python-version`) and `uv`. Run commands from
the repository root.

```bash
uv sync                                      # Install runtime and dev dependencies
uv run chainlit run app.py                    # Start the UI on localhost:8000
uv run pytest                                # Run the test suite
uv run pytest tests/test_tabular.py           # Example focused test run
uv run python -m eval.corpus eval/corpus      # Generate sample documents
uv run python -m eval.run_eval --retrieval-only
uv run python -m eval.run_eval --only table,u01
uv run python -m eval.run_eval                # Full pipeline evaluation
uv run python -m eval.corpus eval/corpus --dataset all
uv run python -m eval.run_eval --dataset formats --retrieval-only
uv run python -m eval.run_eval --dataset formats --only pptx,code,html
uv run python -m eval.run_eval --dataset vision
```

The UI and full pipeline evaluation need a running Ollama server and the
configured model (default `qwen2.5:7b`, installed with `ollama pull qwen2.5:7b`).
Images, scanned PDF pages, and PowerPoint raster pictures use the separate
`OLLAMA_VISION_MODEL` (default `qwen2.5vl:3b`; `ollama pull qwen2.5vl:3b`).
`USE_OCR=true` optionally enables local Tesseract PDF OCR, which needs Tesseract
and its language data installed.
Tests use fake or mocked LLM, vision, and web HTTP responses and do not need
Ollama or API keys. Mocked vision tests do not measure extraction accuracy.
Retrieval and graph tests use real embedding/reranking models: the first run may
download them into `data/models/`; subsequent runs work offline with cached models.
Retrieval-only evaluation has the same cache requirement. The `baseline` (default)
and `formats` datasets need no Ollama in retrieval-only mode; `vision` and `all`
need the vision model even in that mode because ingestion extracts image evidence.
Corpus generation itself needs no inference. See `eval/DATASETS.md` for datasets,
question selection, scoring, and result provenance.

Configuration lives in `docupilot/config.py`. `.env` is optional; use
`.env.example` as the template and preserve existing local settings.
If Chainlit rejects an inherited `DEBUG` value, launch with
`env -u DEBUG uv run chainlit run app.py`.

## Where to make changes

- `app.py`: Chainlit sessions, uploads, pasted URL imports, streamed responses, agent steps, and source panels.
- `docupilot/workspace.py`: file/URL ingestion, deduplication, extraction warnings, and session resources.
- `docupilot/ingest/`: document/slide/image/code/HTML parsers, web fetching, citation-aware chunking, tabular loading, and SQL sandbox.
- `docupilot/ingest/formats.py`: supported extensions and special code filenames; keep UI upload filters in sync.
- `docupilot/ingest/nextjs.py`: bounded, data-only fallback for otherwise empty Next.js HTML; never execute payloads.
- `docupilot/index/` and `docupilot/retrieval.py`: local models, vector storage, hybrid search, and reranking.
- `docupilot/agents/`: graph wiring, turn state, routing, retrieval, SQL, synthesis, and verification.
- `docupilot/agents/context.py`: shared runtime dependencies, UI events, numbered sources, and citation helpers.
- `docupilot/llm.py`: asynchronous Ollama calls, structured output, streaming, and retry handling.
- `docupilot/models.py` and `docupilot/prompts.py`: shared data structures and agent prompts.
- `tests/`: pytest coverage; `tests/conftest.py` generates a temporary synthetic corpus.
- `eval/`: corpus generation, question sets, evaluation runner, and recorded analysis.
- `.chainlit/config.toml` and `chainlit.md`: UI configuration and welcome text.

## Behavior to preserve

- Keep parsing, embeddings, retrieval, reranking, and default inference local.
  Do not introduce hosted inference or an API-key requirement unless requested.
  Explicit web imports fetch user-supplied HTTP(S) pages; they do not crawl, run
  scripts, or load remote page resources.
  Embedded Next.js fallback evidence must come from the initial route's child
  elements, retain its source label, and exclude unused error/loading views and
  arbitrary application data. Keep ordinary readable HTML on the static path.
- Preserve file, page/slide, heading, code line/frame labels, source URLs, and
  session metadata through ingestion and citations. Chunks must not cross page
  or heading boundaries. Preserve code whitespace and never execute uploaded code.
- Keep vision evidence labelled as model-generated and surface skipped-content
  warnings. Mixed documents retain readable content when vision is unavailable;
  files with no extractable content must fail rather than register as indexed.
- Import pasted URLs before answering; URL-only messages just load the page.
  Ignore URLs in code examples and reuse imported pages within the chat,
  including fragment links and redirect aliases. Retain size, timeout, and
  redirect limits, and keep visible HTML footer notices citable.
- Keep retrieval, spreadsheet tables, and conversation history isolated by chat.
  Router-selected files are coverage hints, not exclusive retrieval filters.
- Compute spreadsheet answers through DuckDB. Retain single-statement read-only
  SQL validation, disabled external access, result limits, and query timeouts.
- Ground factual answers in numbered sources; retain verification, removal of
  unsupported claims, and the insufficient-evidence response.
- Keep retries bounded, including the graph's one wider retry. Never replay a
  streamed answer after partial text has already reached the user.
- Keep blocking parsing, web fetching, vision extraction, indexing, and SQL work
  off the asynchronous UI path.

## Change and validation practices

- Follow nearby Python style and type annotations. Keep business logic in
  `docupilot/` and UI presentation in `app.py`.
- Manage dependency changes through `uv` and keep `pyproject.toml` and `uv.lock`
  consistent. No separate formatter or linter is currently configured.
- When adding formats, update `docupilot/ingest/formats.py`, workspace dispatch,
  `.chainlit/config.toml`, and user-facing format lists together. Keep concrete
  application MIME types and extension fallbacks: Chainlit's picker drops
  `application/*`. Retain the WebSocket transport setting.
- Run focused tests for the affected behavior; run the full suite for changes
  spanning ingestion, retrieval, shared models, or graph orchestration. Use
  existing fake-LLM and HTTP-mock patterns for deterministic regression tests.
- For retrieval changes, run retrieval-only evaluation. For prompt or model
  changes, use relevant pipeline evaluation cases; use a full evaluation before
  publishing model comparisons. Record configuration and distinguish new results
  from historical baselines, including dataset and scoring version. Use the
  relevant `formats`/`vision` datasets for format or extraction changes; missing
  or partial extraction must fail evaluation. Baseline pipeline evaluation
  overwrites `eval/results.json`; other datasets/modes have separate default
  paths. Use `--output eval/results-<run>.json` to preserve a named run. `--only`
  selects question IDs, types, or format tags while loading the full selected dataset.
- Documentation-only changes need review of paths, commands, and consistency;
  they do not require model loading or pipeline evaluation.
- Update `README.md` and `.env.example` when changing documented behavior or
  configuration. Report checks run and any unavailable models or services.
- Keep `.env`, uploads, model caches, vector indexes, `.files/`, generated
  `eval/corpus/`, and `eval/results*.json` out of commits, as in `.gitignore`.
  Use the synthetic corpus rather than personal documents for tests.
