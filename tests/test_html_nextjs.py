"""Synthetic streamed page data; no network, browser, or models required."""

import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from docupilot.config import Settings
from docupilot.ingest import html, web
from docupilot.workspace import Workspace


def element(tag, children=None, **props):
    return ["$", tag, None, {"children": children, **props}]


def page(records, *, body="", tail=""):
    stream = "".join(f"{key}:{json.dumps(value)}\n" for key, value in records.items()) + tail
    # Split mid-record and mid-string, as a real streamed response does.
    scripts = "".join(f"<script>self.__next_f.push({json.dumps([1, stream[i:i + 83]])});</script>"
                      for i in range(0, len(stream), 83))
    return f"<html><head><title>Example</title></head><body>{body}{scripts}</body></html>"


def route(content, *, old_seed=False):
    seed = [content, {}, None, False]
    if old_seed:
        seed.insert(0, "__PAGE__")
    return {"f": [[[], seed, None, False]], "b": "BUILD_METADATA"}


@pytest.fixture
def embedded_html():
    main = element("main", [element("h1", "Our team"), element("h2", "Leadership"), "$L2",
                            element("div", "HIDDEN", hidden=True),
                            element("div", "ARIA_HIDDEN", **{"aria-hidden": "true"}),
                            element("div", "STYLE_HIDDEN", style={"display": "none"}),
                            element("script", "SCRIPT_TEXT"), element("nav", "MENU"),
                            element("$L9", "Client child", secret="PRIVATE_PROP"),
                            element("p", "$3"), element("p", "$$42")])
    layout = element("div", [element("$L8", notFound="$La", fallback="LOADING"),
                             element("footer", "Example © Copyright 2026.")])
    root = {"f": [[[], [layout, {"children": [main, {}, element("p", "LOADING"), False]}, None, False],
                    element("title", "HEAD_METADATA"), False]]}
    text = "$42 café\nsecond line"
    return page({"0": root, "2": element("p", "Ada leads the team."),
                 "a": element("h1", "404 UNUSED"), "b": element("p", "UNREACHABLE")},
                tail=f"3:T{len(text.encode('utf-8')):x},{text}")


def parse(tmp_path, content):
    path = tmp_path / "page.html"
    path.write_text(content)
    return html.parse_html(path, path.name)


def test_embedded_content_preserves_headings_and_excludes_non_page_data(tmp_path, embedded_html):
    sections = parse(tmp_path, embedded_html)
    body = "\n".join(s.text for s in sections)
    assert "Ada leads the team." in body and "Client child" in body
    assert "$42 café" in body and "second line" in body
    assert not any(token in body for token in ["HIDDEN", "SCRIPT_TEXT", "MENU", "PRIVATE_PROP", "404",
                                               "LOADING", "HEAD_METADATA", "UNREACHABLE", "BUILD_METADATA"])
    assert next(s for s in sections if "Ada" in s.text).heading_path == "Embedded page content > Our team > Leadership"
    assert next(s for s in sections if "Copyright" in s.text).heading_path == "Embedded page content > Example > Footer"
    assert all(s.file_name == "page.html" for s in sections)


@pytest.mark.parametrize("old_seed", [False, True])
def test_embedded_tables_and_code_remain_structured(tmp_path, old_seed):
    content = element("main", [element("h1", "Guide"), element("table", [
        element("tr", [element("th", "Item"), element("th", "Price")]),
        element("tr", [element("td", "Widget"), element("td", 42)])]),
        element("pre", "def run():\n    return 42")])
    sections = parse(tmp_path, page({"0": route(content, old_seed=old_seed)}))
    assert "|Widget|42|" in next(s.text for s in sections if s.kind == "table")
    code = next(s for s in sections if s.kind == "code")
    assert "    return 42" in code.text and code.heading_path.endswith("lines 1–2")


def test_readable_html_does_not_duplicate_or_mix_in_payload(tmp_path):
    content = page({"0": route(element("p", "PAYLOAD_ONLY"))}, body="<h1>Static</h1><p>Visible text.</p>")
    sections = parse(tmp_path, content)
    assert len(sections) == 1 and sections[0].heading_path == "Static"
    assert "PAYLOAD_ONLY" not in sections[0].text


@pytest.mark.parametrize("content", [
    '<script>self.__next_f.push([1, "broken"])</script>',
    '<script>self.__next_f.push([1, fetch("https://example.test")])</script>',
    '<script>self.__next_f.push([1, "0:{}\\n"]); alert("SCRIPT")</script>',
    page({"0": route("$L1"), "1": "$L1"}),
    page({"0": {"unrelated": element("p", "PRIVATE")}}),
    page({"0": route(element("p", "$1"))}, tail="1:Tff,truncated"),
])
def test_unsupported_or_malformed_payload_is_not_evidence(tmp_path, content):
    assert parse(tmp_path, content) == []


def test_shared_records_cannot_expand_without_bound(tmp_path):
    records = {"0": route("$L1")}
    for n in range(1, 25):
        records[f"{n:x}"] = [f"$L{n + 1:x}"] * 2
    records["19"] = element("p", "REPEATED")
    assert parse(tmp_path, page(records)) == []


def test_import_indexes_embedded_content_with_url_and_session(tmp_path, monkeypatch, embedded_html):
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=embedded_html)
    fetch = web.fetch_page
    monkeypatch.setattr(web, "fetch_page", lambda url, **kw: fetch(url, **kw, transport=httpx.MockTransport(response)))
    store = Mock()
    ws = Workspace("embedded", store, Settings(_env_file=None, data_dir=tmp_path))
    try:
        info = ws.ingest_url("https://example.test/about")
        assert info.source_url == "https://example.test/about"
        chunks = store.upsert.call_args.args[0]
        assert all(c.session_id == "embedded" and c.heading_path.startswith(info.source_url) for c in chunks)
        assert any("Ada leads" in c.text for c in chunks)
        assert any("Copyright" in c.text and "Footer" in c.heading_path for c in chunks)
        assert ws.ingest_url(info.source_url + "#team") is info
        assert len(requests) == 1
    finally:
        ws.close()


def test_empty_web_page_gives_recovery_steps_and_leaves_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "fetch_page", Mock(return_value=web.WebPage(
        b'<body><div id="root"></div><script src="/app.js"></script></body>',
        "https://example.test/about", "empty.html")))
    store = Mock()
    ws = Workspace("empty", store, Settings(_env_file=None, data_dir=tmp_path))
    try:
        with pytest.raises(ValueError, match="upload a PDF or screenshot"):
            ws.ingest_url("https://example.test/about")
        assert not ws.files and not ws.web_urls
        store.upsert.assert_not_called()
        assert not list(Path(ws.settings.uploads_dir).rglob("*.html"))
    finally:
        ws.close()
