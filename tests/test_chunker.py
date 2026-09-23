from docupilot.ingest.chunker import chunk_sections, n_tokens, split_table, split_text
from docupilot.models import Section


def test_split_text_respects_target_and_overlaps():
    paras = [f"Paragraph {i}. " + "word " * 80 for i in range(20)]
    chunks = split_text("\n\n".join(paras), target=300, overlap=120)
    assert len(chunks) > 3
    assert all(n_tokens(c) <= 300 + 5 for c in chunks)
    # consecutive chunks share a paragraph (overlap)
    assert chunks[0].split("\n\n")[-1] == chunks[1].split("\n\n")[0]


def test_split_text_hard_wraps_unbroken_text():
    chunks = split_text("x" * 10_000, target=100, overlap=0)
    assert len(chunks) > 1 and all(len(c) <= 400 for c in chunks)


def test_big_table_split_by_rows_with_header_repeated():
    md = "|a|b|\n|---|---|\n" + "\n".join(f"|{i}|{i * 2}|" for i in range(400))
    parts = split_table(md, max_tokens=200)
    assert len(parts) > 1
    assert all(p.startswith("|a|b|\n|---|---|") for p in parts)
    rows = sum(len(p.splitlines()) - 2 for p in parts)
    assert rows == 400


def test_chunks_keep_citation_metadata():
    secs = [Section("hello world", "f.pdf", page=3, heading_path="A > B")]
    [c] = chunk_sections(secs, file_id="abc", session_id="s")
    assert (c.page, c.heading_path, c.chunk_id) == (3, "A > B", "abc:0")
    assert c.location() == "f.pdf, p. 3, A > B"
