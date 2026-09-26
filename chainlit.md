# DocuPilot

Grounded, cited chat over your files.

1. **Attach files** with 📎 or drag & drop: PDF, DOCX, PPTX, Markdown, TXT, CSV/TSV, XLSX/XLSM, images, code, and HTML. You can add more at any time. Paste a web page URL to import it automatically; `/url` also works.
2. **Ask** anything: facts, comparisons across files, summaries, code explanations, image/chart questions, or calculations over spreadsheets. Include a question with your URL to ask immediately, or ask after the page loads.
3. **Check the sources.** Click any **[n]** to see the exact passage, or the SQL behind a number. PDF sources open at the cited page.

Expand the agent steps above each answer to see how it was produced: the route, the retrieved passages and their relevance scores, the SQL, and the fact-check verdicts.

Images and scanned PDF pages need the local vision model: `ollama pull qwen2.5vl:3b` (or configure `OLLAMA_VISION_MODEL`).
