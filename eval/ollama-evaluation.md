# Local Ollama evaluation

Measured on 2026-09-26 after replacing Gemini. These are results from one local run of the existing synthetic regression corpus, not a general accuracy benchmark.

## Environment

- Hardware: Mac mini, Apple M1, 16 GB unified memory.
- Runtime: Ollama 0.32.5 with cloud disabled (`OLLAMA_NO_CLOUD=1`); Ollama Python client 0.6.2.
- Model: `qwen2.5:7b`, 7.6B parameters, Q4_K_M, used for every LLM stage.
- Model digest: `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`.
- Context: 16,384 tokens; request/read timeout: 300 seconds.
- Initial model download: 4,683,087,332 bytes. No API keys were required.

## Method and results

Ran all 31 questions over the six sample files with `GEMINI_API_KEY`, `GOOGLE_API_KEY`, and `OLLAMA_API_KEY` unset and `HF_HUB_OFFLINE=1`. Questions, prompts, retrieval, SQL safeguards, and scoring were unchanged. LLM cold-start loading is included in the first question; corpus ingestion is measured separately. The harness reports the final turn for follow-up questions.

```bash
env -u GEMINI_API_KEY -u GOOGLE_API_KEY -u OLLAMA_API_KEY HF_HUB_OFFLINE=1 uv run python -m eval.run_eval
```

| Metric | Local result |
|---|---|
| Answer accuracy on answerable questions | 23/27 (85.2%) |
| Citation accuracy (expected file/page) | 23/27 (85.2%) |
| Retrieval hit | 18/21 (85.7%) |
| Automated refusal score on unanswerable questions | 3/4 (75.0%) |
| False refusal on answerable questions | 3/27 (11.1%) |
| Median time to first token | 13.12 s |
| Median total time, including verification | 24.4 s |

| Question type | Answer accuracy |
|---|---|
| single | 13/16 (81.2%) |
| cross | 3/3 (100.0%) |
| table | 5/6 (83.3%) |
| followup | 2/2 (100.0%) |

Raw answers, citations, timings, and model metadata are saved locally in `eval/results.json` (ignored by Git). The preceding results were preserved as `eval/results.pre-ollama.json`.

## Questions that did not meet the expected-answer check

- **s02** — How much did Acme's revenue grow in 2025? Route: `table`. Answer: I couldn't find that in your files.
- **s06** — How many items per hour can the Atlas Arm pick? Route: `chitchat`. Answer: It seems like you're asking about the Atlas Arm's picking capacity, but I don't have any information on that in my loaded files. Could you please check if this data is available in any of the uploaded documents or provide more context related to the files I have?
- **s13** — How long does the Scout battery last on a full charge? Route: `chitchat`. Answer: It seems like you're asking about the Scout battery life, but I don't have any information about Scout batteries in the files I've uploaded. Could you please check if this question is related to one of the files or provide more context?
- **t05** — How many enterprise customers are there? Route: `docs`. Answer: The `segment` column only includes three distinct segments: SMB, Enterprise, and Mid-market. There are 16 rows with the segment value 'Enterprise'. The file covers customer segments, but not specific enterprise customers by name or detail.
- **u03** — What is the capital of Australia? Route: `chitchat`. Answer: Let's focus on the files you've uploaded. Would you like to know something specific about them, or are you looking for information related to your documents?

The four answerable misses were routing errors. For `t05`, document lookup produced 16 enterprise customers instead of the correct 19; it should have computed the count with SQL. For `u03`, Qwen safely redirected the Australia question back to the uploaded files without answering it; the existing refusal regex did not recognize that wording. The automated refusal score remains 3/4, with this manual-review distinction recorded rather than changing the scorer.

## Additional checks

- Automated tests: **85 passed**, including 36 Ollama/configuration cases with mocked HTTP and the existing agent-graph tests.
- The app started without API keys and returned HTTP 200 at `http://localhost:8000`.
- Plain generation returned the requested text without an API key.
- The short request `Summarize the annual report` selected `summary`: 7 citations, 302 streamed chunks, 107.21 seconds including verification. Its output is saved locally in `eval/results.ollama-smoke.json`.
- A longer request covering financial performance, products, and outlook selected `docs` instead of the expected `summary` route. It still produced a streamed answer with six citations in 139.6 seconds, but failed the route assertion. Its output is preserved in `eval/results.ollama-summary-detailed.json`; summary routing is another model limitation.
- Gemini SDK removed; dependency lockfile validated.

The historical Gemini measurements remain separately labeled in the README. Local inference eliminates paid API-key requirements, but this model does not reproduce the historical accuracy or latency.
