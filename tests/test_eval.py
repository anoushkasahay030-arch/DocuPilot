"""Dataset integrity, source locations, and evaluation scoring without live inference."""

from unittest.mock import Mock

from PIL import Image
import pymupdf
import pytest

from docupilot.config import Settings
from docupilot.models import Chunk, Citation, RetrievedChunk
from docupilot.workspace import Workspace
from eval.corpus import build_corpus
from eval.run_eval import (answer_correct, cites_expected, contains_expected, hit, load_questions,
                           retrieval_only, select_questions)


@pytest.fixture(scope="module")
def datasets(tmp_path_factory):
    return build_corpus(tmp_path_factory.mktemp("eval-datasets"), dataset="all")


def test_datasets_have_unique_questions_and_existing_sources(datasets):
    questions = load_questions("all")
    assert len(load_questions()) == 31
    assert len(load_questions("formats")) == 14
    assert len(load_questions("vision")) == 10
    assert len(questions) == len({q["id"] for q in questions}) == 55
    names = {path.name for path in datasets.values()}
    assert len(names) == 14
    for q in questions:
        assert set(q.get("files", [])) <= names
        assert q.get("question") or q.get("turns")
        if q["type"] != "unanswerable":
            assert q["files"]
            assert q.get("expect") or q.get("expect_all")
        assert all("{" not in value for field in ("expect", "expect_all") for value in q[field])


def test_baseline_and_formats_dont_generate_vision_files(tmp_path):
    baseline = build_corpus(tmp_path / "baseline")
    assert set(baseline) == {"pdf", "docx", "md", "txt", "xlsx", "csv"}
    formats = build_corpus(tmp_path / "formats", dataset="formats")
    assert {path.suffix for path in formats.values()} == {".pptx", ".py", ".ts", ".html"}


def test_new_format_evidence_has_expected_locations(datasets, tmp_path):
    store = Mock()
    workspace = Workspace("eval-formats", store, Settings(_env_file=None, data_dir=tmp_path))
    chunks = []
    try:
        for key in ("pptx", "python", "typescript", "html"):
            workspace.ingest(datasets[key])
            chunks.extend(store.upsert.call_args.args[0])
        for q in load_questions("formats"):
            if q["type"] != "unanswerable":
                assert hit([RetrievedChunk(c, 1) for c in chunks], q), q["id"]
        assert not any("555-0199" in c.text or "Mallory" in c.text for c in chunks)
        assert any("Rollback" in c.text and c.page == 2 and "Speaker notes" in c.heading_path for c in chunks)
        assert any("USD millions" in c.text and c.page == 3 for c in chunks)
    finally:
        workspace.close()


def test_vision_corpus_contains_real_pixels_and_no_pdf_text_layer(datasets):
    with pymupdf.open(datasets["scanned_pdf"]) as document:
        assert len(document) == 2
        assert all(not page.get_text().strip() and page.get_images() for page in document)
    for key in ("receipt", "chart", "screenshot"):
        with Image.open(datasets[key]) as image:
            assert image.width >= 1000 and image.height >= 750
            assert image.convert("L").getextrema()[0] < 100  # actual rendered content, not blank placeholders


@pytest.mark.parametrize("answer, expected, matches", [
    ("The cost is 8 [1].", "8", True),
    ("The delay is 8000 [8].", "8", False),
    ("Total is $1,280.00 [2].", "1280", True),
    ("Forecast is 1.80 million [3].", "1.8", True),
    ("Forecast is 1.89 million [3].", "1.8", False),
    ("The cost is -8.", "8", False),
    ("Ten percent is applied [1, 10].", "10", False),
    ("The owner is MIRANDA.", "Mira", False),
    ("The owner is Mira.", "Mira", True),
])
def test_expected_facts_use_boundaries_and_ignore_citation_numbers(answer, expected, matches):
    assert contains_expected(answer, expected) is matches


def test_summary_scoring_requires_every_fact_and_one_discount_variant():
    q = next(q for q in load_questions("formats") if q["id"] == "n02")
    assert answer_correct("10% discount; shipping costs 8 below 100.", q)
    assert answer_correct("10 percent discount; shipping costs 8 below 100.", q)
    assert not answer_correct("Shipping costs 8 below 100.", q)
    assert not answer_correct("10% discount and free shipping.", q)


def test_chart_reference_accepts_singular_and_plural_units():
    q = next(q for q in load_questions("formats") if q["id"] == "p04")
    assert answer_correct("Bookings are 1.8 USD million [2].", q)
    assert answer_correct("Bookings are 1.8 USD millions [2].", q)
    assert not answer_correct("Bookings are 1.8 [2].", q)
    assert not answer_correct("Bookings are 18 USD millions [2].", q)


def test_source_scoring_checks_slide_heading_and_cross_file_coverage():
    q = next(q for q in load_questions("formats") if q["id"] == "p03")
    wrong = Citation(1, "launch.pptx", "text", page=1, heading_path="Speaker notes")
    correct = Citation(1, "launch.pptx", "text", page=2, heading_path="Slide 2 > Speaker notes")
    assert not cites_expected([wrong], q)
    assert cites_expected([correct], q)
    assert not cites_expected([Citation(1, "launch.pptx", "text", page=2)], q)
    cross = next(q for q in load_questions("formats") if q["id"] == "n01")
    assert not cites_expected([correct], cross)
    assert cites_expected([correct, Citation(2, "guide.html", "text")], cross)


def test_filter_selects_ids_types_and_formats():
    questions = load_questions("all")
    selected = select_questions(questions, {"pptx", "v01"})
    assert {q["id"] for q in selected} == {"p01", "p02", "p03", "p04", "v01"}
    assert all(q["type"] == "table" for q in select_questions(questions, {"table"}))
    assert select_questions(questions, {"unknown"}) == []


def test_retrieval_eval_uses_selected_questions_and_returns_recordable_results(monkeypatch):
    import eval.run_eval as runner
    chunk = Chunk("id", "Mira after 45 minutes", "file", "guide.html", "eval", heading_path="Escalation")
    search = Mock(return_value=[RetrievedChunk(chunk, 1)])
    monkeypatch.setattr(runner, "retrieve", search)
    questions = select_questions(load_questions("formats"), {"h01"})
    result = retrieval_only(Mock(session_id="eval"), questions, 8)
    assert [r["id"] for r in result["results"]] == ["h01"]
    assert result["summary"]["hit_at_k"] == [1, 1]
    assert search.call_count == 1
    empty = retrieval_only(Mock(), select_questions(load_questions("formats"), {"summary"}), 8)
    assert empty["summary"]["latency_median_s"] is None


def test_unknown_dataset_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown dataset"):
        build_corpus(tmp_path, dataset="missing")
    with pytest.raises(ValueError, match="Unknown dataset"):
        load_questions("missing")
