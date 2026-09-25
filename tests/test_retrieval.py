"""End-to-end ingest + hybrid retrieval over the corpus (local models, no API calls)."""

import pytest

from docupilot.index.store import VectorStore
from docupilot.retrieval import retrieve
from docupilot.workspace import UnsupportedFile, Workspace


@pytest.fixture(scope="module")
def ws(corpus):
    w = Workspace("test-session", VectorStore(None))
    for p in corpus.values():
        w.ingest(p)
    return w


def top(ws, q, **kw):
    return retrieve(ws.store, [q], ws.session_id, k=kw.pop("k", 3), **kw)


@pytest.mark.parametrize("q, file, page", [
    ("Who is the servo motor supplier?", "acme_annual_report_2025.pdf", 4),
    ("What was Q3 operating margin?", "acme_annual_report_2025.pdf", 2),
    ("How many Scout units were shipped?", "acme_annual_report_2025.pdf", 3),
    ("How many days of annual leave do employees get?", "employee_handbook.docx", 2),
    ("What is the daily meal reimbursement limit?", "employee_handbook.docx", 3),
    ("What is the maximum payload of Scout?", "scout_faq.md", None),
    ("When will the Austin facility open?", "board_minutes.txt", None),
])
def test_top1_citation(ws, q, file, page):
    best = top(ws, q)[0]
    assert best.chunk.file_name == file
    assert best.chunk.page == page
    assert best.score > 0.3


def test_keyword_match_on_rare_term(ws):
    # BM25 half of hybrid search: exact rare token.
    assert top(ws, "Kinetix")[0].chunk.page == 4


def test_schema_card_retrievable(ws):
    assert top(ws, "orders by region and product units revenue")[0].chunk.kind == "table_schema"


def test_irrelevant_query_scores_low(ws):
    assert top(ws, "What is the airspeed velocity of an unladen swallow?")[0].score < 0.1


def test_file_filter_and_subquery_quota(ws):
    res = retrieve(ws.store, ["Scout battery runtime", "Scout units shipped 2025"], ws.session_id, k=4)
    assert {r.chunk.file_name for r in res} >= {"scout_faq.md", "acme_annual_report_2025.pdf"}
    only = top(ws, "Scout", file_names=["scout_faq.md"], k=5)
    assert {r.chunk.file_name for r in only} == {"scout_faq.md"}


def test_dedupe_and_unsupported(ws, corpus, tmp_path):
    before = len(ws.files)
    ws.ingest(corpus["md"])
    assert len(ws.files) == before
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"MZ")
    with pytest.raises(UnsupportedFile):
        ws.ingest(bad)


def test_session_isolation(ws):
    assert retrieve(ws.store, ["payload"], "other-session") == []


def test_ensure_files_adds_missing_file_without_filtering_others(ws):
    res = retrieve(ws.store, ["servo motor supplier risk"], ws.session_id, k=2, ensure_files=["board_minutes.txt"])
    files = {r.chunk.file_name for r in res}
    assert "acme_annual_report_2025.pdf" in files and "board_minutes.txt" in files
