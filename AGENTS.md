# Repository guidance

DocuPilot is a local document-chat app: Chainlit UI, LangGraph orchestration,
Ollama inference, hybrid Qdrant retrieval, and DuckDB spreadsheet queries.
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
```

The UI and full pipeline evaluation need a running Ollama server and the
configured model (default `qwen2.5:7b`, installed with `ollama pull qwen2.5:7b`).
Tests use fake or mocked LLM responses and do not need Ollama or API keys.
Retrieval and graph tests use real embedding/reranking models: the first run may
download them into `data/models/`; subsequent runs work offline with cached models.
Retrieval-only evaluation has the same cache requirement.

Configuration lives in `docupilot/config.py`. `.env` is optional; use
`.env.example` as the template and preserve existing local settings.
If Chainlit rejects an inherited `DEBUG` value, launch with
`env -u DEBUG uv run chainlit run app.py`.

## Where to make changes

- `app.py`: Chainlit sessions, uploads, streamed responses, agent steps, and source panels.
- `docupilot/workspace.py`: file ingestion, deduplication, and session resources.
- `docupilot/ingest/`: document parsers, citation-aware chunking, tabular loading, and SQL sandbox.
- `docupilot/index/` and `docupilot/retrieval.py`: local models, vector storage, hybrid search, and reranking.
- `docupilot/agents/`: graph wiring, turn state, routing, retrieval, SQL, synthesis, and verification.
- `docupilot/llm.py`: asynchronous Ollama calls, structured output, streaming, and retry handling.
- `docupilot/models.py` and `docupilot/prompts.py`: shared data structures and agent prompts.
- `tests/`: pytest coverage; `tests/conftest.py` generates a temporary synthetic corpus.
- `eval/`: corpus generation, question sets, evaluation runner, and recorded analysis.
- `.chainlit/config.toml` and `chainlit.md`: UI configuration and welcome text.

## Behavior to preserve

- Keep parsing, embeddings, retrieval, reranking, and default inference local.
  Do not introduce hosted inference or an API-key requirement unless requested.
- Preserve file, page, heading, and session metadata through parsing, chunking,
  retrieval, and citations. Chunks must not cross page or heading boundaries.
- Keep retrieval, spreadsheet tables, and conversation history isolated by chat.
  Router-selected files are coverage hints, not exclusive retrieval filters.
- Compute spreadsheet answers through DuckDB. Retain single-statement read-only
  SQL validation, disabled external access, result limits, and query timeouts.
- Ground factual answers in numbered sources; retain verification, removal of
  unsupported claims, and the insufficient-evidence response.
- Keep retries bounded, including the graph's one wider retry. Never replay a
  streamed answer after partial text has already reached the user.
- Keep blocking parsing, indexing, and SQL work off the asynchronous UI path.

## Change and validation practices

- Follow nearby Python style and type annotations. Keep business logic in
  `docupilot/` and UI presentation in `app.py`.
- Manage dependency changes through `uv` and keep `pyproject.toml` and `uv.lock`
  consistent. No separate formatter or linter is currently configured.
- Run focused tests for the affected behavior; run the full suite for changes
  spanning ingestion, retrieval, shared models, or graph orchestration. Use
  existing fake-LLM and HTTP-mock patterns for deterministic regression tests.
- For retrieval changes, run retrieval-only evaluation. For prompt or model
  changes, use relevant pipeline evaluation cases; use a full evaluation before
  publishing model comparisons. Record configuration and distinguish new results
  from historical baselines. Full evaluation overwrites `eval/results.json`.
- Documentation-only changes need review of paths, commands, and consistency;
  they do not require model loading or pipeline evaluation.
- Update `README.md` and `.env.example` when changing documented behavior or
  configuration. Report checks run and any unavailable models or services.
- Keep `.env`, uploads, model caches, vector indexes, `.files/`, generated
  `eval/corpus/`, and `eval/results*.json` out of commits, as in `.gitignore`.
  Use the synthetic corpus rather than personal documents for tests.
