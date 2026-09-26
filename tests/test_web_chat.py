"""Regression for pasted links being treated as chat without actually loading their pages."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from docupilot.config import Settings
from docupilot.ingest import html, web
from docupilot.workspace import Workspace


@pytest.mark.parametrize("message, urls, question", [
    ("https://example.test/page\nfind copywrite text", ["https://example.test/page"], True),
    ("access the web page at https://example.test/page", ["https://example.test/page"], True),
    ("Find copyright on https://example.test/page.", ["https://example.test/page"], True),
    ("Read [this page](https://example.test/page).", ["https://example.test/page"], True),
    ("https://example.test/wiki/Function_(math)", ["https://example.test/wiki/Function_(math)"], False),
    ("<https://example.test/page>", ["https://example.test/page"], False),
    ("/url https://example.test/page", ["https://example.test/page"], False),
    ("/url https://example.test/page find copyright", ["https://example.test/page"], True),
    ("https://example.test/a\nhttps://example.test/b\nCompare", ["https://example.test/a", "https://example.test/b"], True),
    ("Compare https://example.test/a with https://example.test/a", ["https://example.test/a"], True),
    ("Explain `fetch('https://example.test')`", [], True),
    ("```python\nurl = 'https://example.test'\n```", [], True),
    ("```python\nurl = 'https://example.test'", [], True),
    ("https://example.test/page\nExplain this: `x = 1`", ["https://example.test/page"], True),
    ("yes", [], True),
])
def test_pasted_url_detection(message, urls, question):
    actual_urls, actual_question = web.parse_web_message(message)
    assert actual_urls == urls
    assert bool(actual_question) == question


@pytest.mark.parametrize("message", ["/url", "/url file:///etc/passwd", "Read https://u:p@example.test"])
def test_invalid_import_is_reported(message):
    with pytest.raises(ValueError):
        web.parse_web_message(message)


def test_copyright_footer_has_its_own_section(tmp_path):
    path = tmp_path / "page.html"
    path.write_text('''<html><head><title>Example</title></head><body>
        <h1>Product</h1><p>Main content.</p><footer><p>Example © Copyright 2026.</p>
        <p><a href="/privacy">Privacy policy</a></p><script>bad()</script></footer>
        <p>After footer.</p><div role="contentinfo">Contact help@example.test</div></body></html>''')
    sections = html.parse_html(path, path.name)
    footer = next(s for s in sections if "Copyright" in s.text)
    assert footer.heading_path == "Example > Footer"
    assert "Privacy policy" in footer.text and "bad()" not in footer.text
    assert next(s for s in sections if "After footer" in s.text).heading_path == "Product"
    assert next(s for s in sections if "Contact" in s.text).heading_path == "Example > Footer"


@pytest.fixture
def chat(monkeypatch, tmp_path):
    monkeypatch.delenv("DEBUG", raising=False)
    import app

    store = Mock()
    ws = Workspace("web-chat", store, Settings(_env_file=None, data_dir=tmp_path, max_files=2))
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=(
            f"<h1>Example page {request.url.path}</h1><p>Product information.</p>"
            "<footer>Example © Copyright 2026.</footer>"
        ).encode())
    fetch = web.fetch_page
    monkeypatch.setattr(web, "fetch_page", lambda url, **kw: fetch(url, **kw, transport=httpx.MockTransport(response)))

    messages = []
    class Message:
        def __init__(self, content="", **kwargs):
            self.content = content
            self.elements = []
        async def send(self):
            messages.append(self)
            return self
        async def update(self):
            return self
    class Step:
        def __init__(self, **kwargs):
            self.output = ""
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def stream_token(self, text):
            pass

    async def answer(*args):
        assert ws.files  # Import must finish before the graph runs, including the first message.
        assert any("Copyright 2026" in c.text for c in store.upsert.call_args.args[0])
        return {"answer": "Example © Copyright 2026. [1]", "citations": []}

    ask = AsyncMock(side_effect=answer)
    monkeypatch.setattr(app.cl, "Message", Message)
    monkeypatch.setattr(app.cl, "Step", Step)
    monkeypatch.setattr(app.cl, "user_session", SimpleNamespace(get=lambda _: ws))
    monkeypatch.setattr(app, "llm", Mock())
    monkeypatch.setattr(app, "graph", Mock())
    monkeypatch.setattr(app, "ask", ask)
    yield SimpleNamespace(app=app, ws=ws, requests=requests, messages=messages, ask=ask)
    ws.close()


def send(chat, content):
    asyncio.run(chat.app.on_message(SimpleNamespace(content=content, elements=[])))


def test_screenshot_message_imports_then_answers_without_confirmation(chat):
    content = "https://example.test/page\nfind copywrite text"
    send(chat, content)
    assert len(chat.requests) == 1
    chat.ask.assert_awaited_once()
    assert chat.ask.call_args.args[3] == content
    assert chat.messages[-1].content == "Example © Copyright 2026. [1]"


@pytest.mark.parametrize("content", ["https://example.test/page", "/url https://example.test/page"])
def test_url_only_import_does_not_ask_the_llm_to_pretend_to_browse(chat, content):
    send(chat, content)
    assert len(chat.ws.files) == 1
    chat.ask.assert_not_awaited()


def test_followup_and_repeated_url_reuse_loaded_page(chat):
    chat.ws.settings.max_files = 1
    send(chat, "https://example.test/page")
    send(chat, "Find copyright on https://example.test/page#footer")
    send(chat, "What year is that copyright?")
    assert len(chat.requests) == 1
    assert chat.ask.await_count == 2


def test_multiple_links_load_before_answering(chat):
    send(chat, "Compare https://example.test/a and https://example.test/b")
    assert len(chat.requests) == len(chat.ws.files) == 2
    chat.ask.assert_awaited_once()


def test_import_failure_stops_the_turn_instead_of_promising_access(chat, monkeypatch):
    monkeypatch.setattr(web, "fetch_page", Mock(side_effect=ValueError("Page unavailable")))
    send(chat, "https://example.test/page\nFind copyright")
    chat.ask.assert_not_awaited()
    assert not chat.ws.files
    assert "Page unavailable" in chat.messages[-1].content


def test_removed_page_can_be_imported_again(chat):
    send(chat, "https://example.test/page")
    chat.ws.remove(next(iter(chat.ws.files)))
    send(chat, "https://example.test/page")
    assert len(chat.requests) == 2
