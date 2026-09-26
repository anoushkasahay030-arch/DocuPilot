# Evaluation datasets

All fixtures are generated from repository code, contain synthetic facts, and require no downloaded dataset or personal documents. Generated binaries and question results live in ignored paths. The JSONL files are the reference questions and answers; they are never indexed as user documents.

```bash
# Generate everything for manual upload or inspection; no inference required.
uv run python -m eval.corpus eval/corpus --dataset all

# Existing 31-question baseline (default).
uv run python -m eval.run_eval --dataset baseline --retrieval-only

# New parsers: four files, 14 questions. Retrieval needs cached models but no Ollama.
uv run python -m eval.run_eval --dataset formats --retrieval-only
uv run python -m eval.run_eval --dataset formats
uv run python -m eval.run_eval --dataset formats --only p03,k03,h02

# Images/scans: four files, 10 questions. Ingestion itself uses local vision.
ollama pull qwen2.5vl:3b
uv run python -m eval.run_eval --dataset vision
uv run python -m eval.run_eval --dataset vision --only chart,screenshot

# Combined 14-file, 55-question evaluation.
uv run python -m eval.run_eval --dataset all --output eval/results-all-local.json
```

`--only` matches IDs, question types (`single`, `cross`, `table`, `followup`, `summary`, `unanswerable`) or format tags, and works in retrieval mode too. It selects questions while keeping the dataset's full file inventory. Retrieval-only metrics cover single-source and cross-document questions; summaries, follow-ups, SQL and refusals need the pipeline run.

## Formats

`format_corpus.py` generates this dataset; `questions_formats.jsonl` holds its questions.

| File | Facts and structure | Questions |
|---|---|---|
| `launch.pptx` | Slide 1: Zephyr launch, October 12, 2026, owner Ada. Slide 2: West/East pilot seats 40/60, speaker-note rollback threshold 2%. Slide 3: native bookings chart Q1/Q2 1.2/1.8 USD million. | `p01`–`p04` |
| `pricing.py` | A 10% discount rounded to two decimals; shipping costs 8 below a subtotal of 100, otherwise zero. | `k01`, `k02`, `n02` |
| `retry_policy.ts` | Three maximum attempts; exponential delay capped at 8000. | `k03`, `k04` |
| `guide.html` | Orion escalation to Mira after 45 minutes; Bronze/Silver/Gold response targets 24/4/1 hours; client timeout 20. A hidden phone number and script are excluded from visible evidence. | `h01`–`h03`, `fu01` |

`n01` combines the slide launch owner and HTML escalation owner and requires citations to **both** files. `n02` checks a code overview. `fu01` asks for a phone number that exists only in a hidden element and should produce an insufficient-evidence answer.

The slides have no raster pictures, so this dataset can be indexed without a vision model. Stored native chart values and labels remain available through the PowerPoint parser.

## Vision

`questions_vision.jsonl` holds questions over four generated visual fixtures:

| File | Visible ground truth | Questions |
|---|---|---|
| `scanned_invoice.pdf` | Page 1: invoice INV-2048, supplier Northstar Labs, total USD 1,280.00. Page 2: Net 30 terms and bank transfer. | `v01`–`v03` |
| `receipt.png` | Receipt R-731, Harbor Stationery, three notebooks at USD 6.00, total USD 18.00. | `v04`, `v05` |
| `support_tickets.png` | Q1/Q2/Q3/Q4 bars: 12/18/9/24 tickets. Q4 is orange; other bars are blue. No next-year forecast. | `v06`, `v07`, `vu01` |
| `deployment_console.png` | Orion API in Staging; E-104, missing DATABASE_URL; Retry deployment button below the error banner. | `v08`, `v09` |

Text uses PyMuPDF's built-in fonts and is rasterized at a fixed size. The scanned PDF contains only images, with no searchable text layer. The chart-color and screenshot-position questions exercise visual interpretation beyond transcription. Both image generation and ingestion remain local.

Generation requires no model. Evaluating `vision` or `all` does require the configured Ollama vision model, even for retrieval-only runs. The harness never substitutes reference answers or mocked OCR for live extraction. Missing-model errors and partial-document warnings abort a run; they are not scored as successful extraction. Automated tests separately verify fixture structure and runner behavior without live vision inference.

## Scoring and result provenance

- `expect`: at least one listed alternative must appear; `expect_all`: every listed fact must appear. When both are present, both conditions must hold. Refusal detection is scored separately for unanswerable questions.
- Scoring version **2** strips numbered citation markers before matching facts, uses token/number boundaries, and accepts thousands separators and trailing decimal zeros. A citation `[8]` or the number `8000` cannot satisfy an expected answer of `8`.
- `page` means the exact PDF page or slide. `heading_contains` checks speaker notes, HTML sections or code line labels. `require_all_files` requires all named sources for cross-file cases. Recorded source locations include headings as well as file/page labels.
- These are heuristic reference-answer checks, not an LLM judge. Equivalent phrasings can still be marked wrong, and an answer containing the right phrase can contain other errors. Review answers and citations in the result JSON.
- The baseline fixtures and 31 questions are unchanged. Historical reports used the older substring scorer, so record the scoring version when comparing new runs.
- Results include dataset/version, scoring version, actual selected question IDs, loaded file names, pipeline versus retrieval mode, models, context size and OCR configuration. Default output names separate datasets and modes; use `--output` to preserve additional runs.

This is a small regression corpus, not a benchmark of arbitrary documents, codebases or image quality.
