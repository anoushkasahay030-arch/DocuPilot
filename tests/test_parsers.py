from docupilot.ingest import pdf, text, word


def test_pdf_pages_headings_and_tables(corpus):
    sections = pdf.parse_pdf(corpus["pdf"], "report.pdf")
    by_page = {s.page for s in sections}
    assert by_page == {1, 2, 3, 4}

    atlas = next(s for s in sections if "Atlas Arm is a warehouse" in s.text)
    assert atlas.page == 3
    assert atlas.heading_path.endswith("3 Products > 3.1 Atlas Arm")

    table = next(s for s in sections if s.kind == "table")
    assert table.page == 2
    assert "|Q4|117.3|14.5%|" in table.text.replace(" ", "")
    # Page footers are dropped, not indexed as content.
    assert not any(s.text.strip() == "Page 2" for s in sections)


def test_docx_page_breaks_headings_and_tables(corpus):
    sections = word.parse_docx(corpus["docx"], "handbook.docx")
    leave = next(s for s in sections if "25 days" in s.text)
    assert leave.page == 2 and not leave.page_exact
    assert leave.heading_path == "Employee Handbook > 2 Leave Policy"
    table = next(s for s in sections if s.kind == "table")
    assert "|Bereavement|5|" in table.text and table.page == 2
    expenses = next(s for s in sections if "$75" in s.text)
    assert expenses.page == 3


def test_markdown_headings(corpus):
    sections = text.parse_text_file(corpus["md"], "faq.md")
    payload = next(s for s in sections if "150 kg" in s.text)
    assert payload.heading_path == "Scout AMR FAQ > Payload" and payload.page is None


def test_txt_does_not_invent_headings(corpus):
    sections = text.parse_text_file(corpus["txt"], "minutes.txt")
    assert all(s.heading_path == "" for s in sections)
    assert "Austin" in "".join(s.text for s in sections)


def test_markdown_code_fence_is_not_a_heading():
    md = "# Title\n```python\n# not a heading\nx = 1\n```\n"
    [s] = text.markdown_to_sections(md, "a.md")
    assert s.heading_path == "Title" and "# not a heading" in s.text
