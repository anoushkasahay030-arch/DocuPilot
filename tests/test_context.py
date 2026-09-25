from docupilot.agents.context import build_sources, cited_numbers
from docupilot.models import Chunk, RetrievedChunk, TableResult


def rc(i, score):
    return RetrievedChunk(Chunk(f"f:{i}", f"text {i}", "f", "f.pdf", "s", page=i), score)


def test_keeps_low_scoring_context_only_when_question_is_answerable():
    chunks = [rc(1, 0.9), rc(2, 0.01), rc(3, 0.0), rc(4, 0.0), rc(5, 0.0)]
    assert [s.citation.page for s in build_sources(chunks, [], 0.08, min_keep=3)] == [1, 2, 3]
    assert build_sources([rc(1, 0.05), rc(2, 0.01)], [], 0.08) == []


def test_sql_results_are_sources_and_errors_are_not():
    ok = TableResult("total", "SELECT 1", ["t"], ["a.xlsx"], markdown="|x|\n|---|\n|1|", row_count=1)
    bad = TableResult("x", "SELECT", ["t"], ["a.xlsx"], error="boom")
    [s] = build_sources([], [ok, bad], 0.08)
    assert s.citation.kind == "sql" and s.citation.sql == "SELECT 1"


def test_cited_numbers_handles_grouped_and_repeated_markers():
    assert cited_numbers("a [2] b [1, 3] c [2][4]") == [2, 1, 3, 4]
