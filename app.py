"""DocuPilot Chainlit UI.  Run:  uv run chainlit run app.py"""

import asyncio
import logging
import shutil
from pathlib import Path

import chainlit as cl

from docupilot.agents.context import Deps
from docupilot.agents.graph import ask, build_graph
from docupilot.agents.verifier import confidence_label
from docupilot.config import get_settings
from docupilot.index import embed
from docupilot.index.store import VectorStore
from docupilot.llm import LLM
from docupilot.models import Citation
from docupilot.workspace import UnsupportedFile, Workspace

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("docupilot")
settings = get_settings()

_store: VectorStore | None = None
_graph = None
_llm: LLM | None = None


def store() -> VectorStore:
    global _store
    if _store is None:
        s = VectorStore(settings.qdrant_path)
        s.reset()  # sessions (and their DuckDB tables) don't survive a restart, so start clean
        _store = s
    return _store


def graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = LLM(settings)
    return _llm


WELCOME = """### 👋 Welcome to DocuPilot
Attach files with the 📎 button (or drag & drop) and ask anything about them.

**Supported:** PDF · Word (.docx) · Markdown · TXT · CSV · Excel (.xlsx)

- Every claim is cited: click a **[n]** marker to see the exact passage, page, or the SQL behind a number.
- Spreadsheets are queried with real SQL, so totals and rankings are computed rather than guessed.
- Answers are fact-checked and get a confidence rating. If it's not in your files, I'll say so.
- Parsing, search and ranking run locally; only the few relevant passages reach the LLM.
"""


@cl.on_chat_start
async def on_chat_start():
    ws = Workspace(cl.context.session.id, await asyncio.to_thread(store))
    cl.user_session.set("ws", ws)
    await cl.Message(content=WELCOME).send()
    # Load local models in the background so the first question isn't slow.
    asyncio.get_running_loop().run_in_executor(None, embed.warmup)


@cl.on_chat_end
async def on_chat_end():
    ws: Workspace | None = cl.user_session.get("ws")
    if ws:
        await asyncio.to_thread(ws.close)
        shutil.rmtree(settings.uploads_dir / ws.session_id, ignore_errors=True)


async def ingest(ws: Workspace, files: list) -> None:
    loop = asyncio.get_running_loop()
    dest_dir = settings.uploads_dir / ws.session_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    report: list[str] = []
    async with cl.Step(name="Ingest", type="tool", default_open=True, show_input=False) as step:
        for f in files:
            dest = dest_dir / Path(f.name).name
            shutil.copy(f.path, dest)  # keep our own copy: the PDF viewer serves it later

            def progress(message: str) -> None:
                asyncio.run_coroutine_threadsafe(step.stream_token(message + "\n"), loop)

            try:
                info = await asyncio.to_thread(ws.ingest, dest, f.name, progress)
                if info.kind == "spreadsheet":
                    what = f"{len(info.tables)} table(s): {', '.join(info.tables)}"
                else:
                    what = (f"{info.pages} pages, " if info.pages else "") + f"{info.chunks} passages"
                report.append(f"✅ **{info.name}** ({what})")
            except UnsupportedFile as e:
                report.append(f"⚠️ {e}")
            except Exception as e:  # noqa: BLE001 - one bad file must not break the session
                log.exception("ingest failed for %s", f.name)
                report.append(f"❌ **{f.name}**: couldn't be parsed ({type(e).__name__}: {e})")
        step.output = "\n".join(report)
    await cl.Message(content="\n".join(report) + "\n\n" + inventory(ws)).send()


def inventory(ws: Workspace) -> str:
    if not ws.files:
        return ""
    docs = ", ".join(f.name for f in ws.doc_files)
    sheets = ", ".join(f"{f.name} ({', '.join(f.tables)})" for f in ws.sheet_files)
    parts = [f"**Loaded:** {len(ws.files)} file(s)"]
    if docs:
        parts.append(f"📄 {docs}")
    if sheets:
        parts.append(f"📊 {sheets}")
    return " · ".join(parts)


def citation_element(c: Citation) -> cl.Text:
    if c.kind == "sql":
        body = f"**{c.file_name}**, computed with SQL\n\n```sql\n{c.sql}\n```\n\n{c.snippet}"
    else:
        where = c.label() + (f"\n\n*Section:* {c.heading_path}" if c.heading_path else "")
        relevance = f"\n\n*Relevance:* {c.score:.2f}" if c.score is not None else ""
        body = f"**{where}**{relevance}\n\n---\n\n{c.snippet}"
    return cl.Text(name=f"[{c.n}]", content=body, display="side")


def sources_footer(ws: Workspace, state: dict) -> tuple[str, list]:
    citations: list[Citation] = state.get("citations") or []
    elements: list = []
    lines: list[str] = []
    pdf_pages: set[tuple[str, int]] = set()
    for c in citations:
        elements.append(citation_element(c))
        line = f"- [{c.n}] {c.label()}" + (f" — {c.heading_path}" if c.heading_path else "")
        f = ws.file_by_name(c.file_name)
        if f and f.name.lower().endswith(".pdf") and c.page:
            # Opens the original PDF at the cited page.
            name = f"📄 {f.name} p.{c.page}"
            if (f.name, c.page) not in pdf_pages:
                pdf_pages.add((f.name, c.page))
                elements.append(cl.Pdf(name=name, path=f.path, page=c.page, display="side"))
            line += f" · {name}"
        lines.append(line)

    footer = []
    conf = state.get("confidence")
    if conf is not None:
        label = confidence_label(conf)
        icon = {"High": "🟢", "Medium": "🟡", "Low": "🔴"}[label]
        claims = state.get("claims") or []
        ok = sum(1 for c in claims if c["verdict"] == "supported")
        footer.append(f"{icon} **Confidence: {label}** ({conf:.2f}) · {ok}/{len(claims)} claims verified")
    if lines:
        footer.append("**Sources**\n" + "\n".join(lines))
    return ("\n\n---\n" + "\n\n".join(footer)) if footer else "", elements


@cl.on_message
async def on_message(message: cl.Message):
    ws: Workspace = cl.user_session.get("ws")
    files = [e for e in (message.elements or []) if getattr(e, "path", None)]
    if files:
        await ingest(ws, files)
    question = (message.content or "").strip()
    if not question:
        return
    try:
        model = llm()
    except RuntimeError as e:
        await cl.Message(content=f"⚠️ {e}").send()
        return

    answer = cl.Message(content="")
    streamed = False
    step_types = {"Router": "run", "Retrieval": "retrieval", "Table": "tool", "Verifier": "tool"}

    async def on_event(e: dict) -> None:
        kind = e["event"]
        nonlocal streamed
        if kind == "token":
            streamed = True
            await answer.stream_token(e["text"])
        elif kind == "reset":
            answer.content = ""
            await answer.update()
        elif kind == "step":
            async with cl.Step(name=e["agent"], type=step_types.get(e["agent"], "tool"),  # type: ignore[arg-type]
                               show_input=False) as s:
                s.output = f"**{e['title']}**" + (f"\n\n{e['detail']}" if e.get("detail") else "")

    try:
        state = await ask(graph(), Deps(ws, model, settings), ws.session_id, question, on_event)
    except Exception as e:  # noqa: BLE001
        log.exception("turn failed")
        answer.content = (answer.content or "") + f"\n\n⚠️ Something went wrong: {type(e).__name__}: {e}"
        await (answer.update() if streamed else answer.send())
        return

    footer, elements = sources_footer(ws, state)
    answer.content = state.get("answer", "") + footer
    answer.elements = elements
    await (answer.update() if streamed else answer.send())


@cl.set_starters
async def starters():
    return [
        cl.Starter(label="What can you do?", message="What can you do, and which file types do you support?"),
    ]

