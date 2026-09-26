# File support validation — 2026-09-26

These are new integration checks for the added formats, not a model comparison or a replacement for the historical evaluation. Existing `eval/results.json` was preserved.

## Configuration

- Python 3.12, macOS 26.5.2 ARM64; Ollama Python client 0.6.2.
- Routing, SQL, synthesis and verification: `qwen2.5:7b`.
- Context: 16,384 tokens; request timeout: 300 seconds.
- Default local embedding/BM25/reranker configuration, `TOP_K=8`.
- Configured vision model: `qwen2.5vl:3b`; absent from the local Ollama installation. Vision integration used synthetic images and mocked model responses. OCR and chart-reading quality were **not** measured.

## Automated checks

- `uv run pytest -q`: **178 passed**, including the real local retrieval models and fake-LLM graph suite.
- `uv run python -m eval.run_eval --retrieval-only`: **19/19 hit@8 and 19/19 top-1**, median retrieval latency 443 ms on the existing corpus.
- `uv lock --check --offline` and `git diff --check`: passed.
- Chainlit app import and the installed server's MIME validation: passed for code, PowerPoint, HTML and image uploads.

New regression coverage includes grouped slide text, tables, chart values, notes, picture extraction and slide citations; HTML headings/tables/code and hidden-content removal; source whitespace, line ranges, encodings and minified files; image normalization, frame limits, scanned-PDF page metadata, missing-model errors, partial documents and HTTP-client cleanup; and web redirects, size/type limits, URL provenance and session-local storage.

## Live pipeline smoke checks

The existing six-document synthetic corpus was loaded alongside these three generated files:

- `pricing.py`: `def apply_discount(price):` followed by an indented `return price * 0.9`.
- `guide.html`: heading `Orion support`, subheading `Escalation`, paragraph `Escalate unresolved Orion incidents to Mira after 45 minutes.`
- `launch.pptx`, slide 1: title `Project Zephyr launch`, body `The Zephyr launch is scheduled for October 12, 2026. The launch owner is Ada.`

The real graph, local retrieval, DuckDB, and Ollama were used. Six answerable questions returned the expected facts and cited the expected file; the unanswerable question correctly declined to invent a figure.

| Case | Question / expected behavior | Result |
|---|---|---|
| Existing `s01` | CEO → Maria Lindqvist, PDF page 1 | Passed |
| Existing `t01` | Total order revenue → $30,814,900, computed SQL | Passed |
| Existing `u01` | Revenue in 2031 → insufficient evidence | Passed |
| Code | What discount does `apply_discount` apply? → 10% | Passed |
| HTML | Escalation owner and delay → Mira, 45 minutes | Passed |
| PPTX | Launch date and owner → October 12, 2026, Ada; slide 1 citation | Passed |
| Code overview | Explain `pricing.py` → function behavior and 0.9 multiplier | Passed |

The initial smoke run completed before the final router wording explicitly categorized general code/image overviews as summaries. The focused final-prompt code-overview rerun also passed: correct function behavior, a citation to `pricing.py` lines 1–2, and no retry. The model still chose the `docs` route for that filename-specific question, which retrieved the relevant code successfully. This small check set does not establish general answer quality or visual accuracy.

## Reproducible dataset expansion

The previous ad hoc smoke fixtures are now expanded into repository generators and reference questions. See [DATASETS.md](DATASETS.md) for commands and exact contents:

- `formats`: four files and 14 questions covering slides, native charts, speaker notes, Python/TypeScript, HTML, cross-file evidence, code summary and a hidden-content refusal.
- `vision`: four files and 10 questions covering a genuinely image-only two-page PDF, a receipt, chart values/color and screenshot text/layout.
- `all`: 14 files and 55 questions including the unchanged 31-question baseline.

New validation uses the same local `qwen2.5:7b` configuration above and records scoring version 2. The full suite passed **197 tests**. Format retrieval passed **12/12 hit@8 and 12/12 top-1**; baseline retrieval remained **19/19** on both metrics. The `--only h02` retrieval check evaluated exactly that question and wrote the selected-question metadata to its custom output path. Generated images were visually inspected; tests verify that the scanned PDF has no native text layer.

The live command `uv run python -m eval.run_eval --dataset formats --only p01,p04,k03,h02,fu01 --output eval/results-formats-smoke.json` returned the expected facts and citations for the four answerable cases and correctly declined to expose the hidden HTML phone number. Its initial automated score was 3/4 answerable: `p04` answered “1.8 USD millions” while the reference accepted only singular “million.” The reference now explicitly accepts both forms, with a regression test requiring the numeric value and units. The original smoke output is preserved; a focused rerun uses `eval/results-formats-chart.json`.

The focused `p04` rerun passed both the corrected answer check and the slide-citation check (1/1 each), recorded in `eval/results-formats-chart.json`.

The vision model is still absent from `ollama list`. These fixtures are ready to evaluate, but no live OCR or visual-quality result is claimed. The live evaluation harness never substitutes mocked extraction.

## Pasted web-link regression

The UI now imports pasted HTTP(S) links before routing the question, accepts inline links as well as `/url`, reuses previously imported URLs within the chat, and stops on fetch failure. The HTML parser retains footer/contentinfo sections, including copyright notices.

- Full suite: **223 passed**, including the exact URL-plus-question UI flow, URL-only messages, repeated links/fragments, follow-ups, multiple links, failed imports, ignored code-example URLs, and footer citation metadata.
- Formats retrieval: **12/12 hit@8 and 12/12 top-1** after the parser change.
- A manual live smoke check fetched `https://crewai.com/open-source` through the actual importer and passed its captured HTML to the local `qwen2.5:7b` graph. The exact question `https://crewai.com/open-source` followed by `find copywrite text` returned the visible copyright notice with a citation to `Footer > Resources`, rather than asking for permission to browse. This public-page smoke check is separate from the deterministic synthetic datasets and is not part of offline tests.

## Upload-picker regression and final checks

Chainlit bundles react-dropzone 14.2.3, whose native-picker conversion discards `application/*` and its extension list. The upload configuration now uses concrete application MIME types and explicit extensions under a valid fallback entry. Running the bundled JavaScript conversion confirmed that the corrected filter retains all 63 supported extensions. Server MIME regressions cover PDF, Office documents, CSV browser variants, code, images and HTML.

- Focused file-support suite: **103 passed**.
- Final full suite: **235 passed**.
- `uv lock --check --offline` and `git diff --check`: passed.
- The restarted app's settings endpoint serves the corrected upload filter.
- Generated `:memory:.ses` runtime files are ignored by Git.

## Embedded Next.js import regression

The empty-content failure on `https://nova9.ai/about` was reproduced on 2026-09-26.
Its response contained a loading shell and streamed Next.js page data. The new
data-only fallback recovered eight passages from the captured response, retaining
headings, the footer and source URL while excluding the unused 404 view. No page
scripts were executed; the captured page is not part of the committed test corpus.

- Full suite: **248 passed**, including synthetic streamed records, Unicode text,
  hidden/error/loading exclusions, malformed and cyclic data, expansion limits,
  code/table structure, static-HTML precedence and session/URL provenance.
- Formats retrieval: **12/12 hit@8 and 12/12 top-1**, median 171 ms, using the
  existing local search models and scoring version 2. Output:
  `eval/results-formats-nextjs-retrieval.json` (ignored by Git).
- Manual retrieval over the captured public page ranked the correct passage first
  for the CTO and footer-year questions. The generic query `Find the copyright text`
  remained below the relevance threshold despite the notice being indexed; this
  import fix does not establish general retrieval or answer accuracy. No live LLM
  pipeline or vision evaluation was run for this change.
