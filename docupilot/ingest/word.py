"""DOCX → Sections. Walks the body in document order so headings, paragraphs and tables stay interleaved.

DOCX has no fixed pages. We track explicit page breaks and Word's `lastRenderedPageBreak` markers:
when Word saved rendered-break markers the page numbers are reliable, otherwise they are approximate.
"""

import re
from pathlib import Path

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from docupilot.ingest.text import HeadingStack, markdown_to_sections
from docupilot.models import Section

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_STYLE = re.compile(r"^heading\s*(\d)$", re.I)


def _heading_level(p: Paragraph, has_title: bool) -> int | None:
    name = (p.style.name if p.style is not None else "") or ""
    if name.lower() == "title":
        return 1
    m = _HEADING_STYLE.match(name)
    if not m:
        return None
    # Nest section headings under the document title when there is one.
    return min(int(m.group(1)) + int(has_title), 6)


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).replace("|", "\\|").strip()


def table_to_markdown(table: Table) -> str:
    rows: list[list[str]] = []
    for row in table.rows:
        cells, prev = [], None
        for c in row.cells:
            # Horizontally merged cells are repeated by python-docx; keep one copy.
            if prev is not None and c._tc is prev:
                continue
            prev = c._tc
            cells.append(_cell(c.text))
        rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["|" + "|".join(rows[0]) + "|", "|" + "|".join(["---"] * width) + "|"]
    lines += ["|" + "|".join(r) + "|" for r in rows[1:]]
    return "\n".join(lines)


def _breaks(p: Paragraph) -> tuple[int, int, bool]:
    """(explicit page breaks, rendered page breaks, page_break_before)."""
    el = p._p
    explicit = sum(1 for br in el.iter(f"{_W}br") if br.get(f"{_W}type") == "page")
    rendered = sum(1 for _ in el.iter(f"{_W}lastRenderedPageBreak"))
    before = bool(p.paragraph_format.page_break_before)
    return explicit, rendered, before


def parse_docx(path: Path, file_name: str) -> list[Section]:
    document = docx.Document(str(path))
    body = document.element.body

    has_rendered = any(True for _ in body.iter(f"{_W}lastRenderedPageBreak"))
    has_title = any((p.style is not None and (p.style.name or "").lower() == "title") for p in document.paragraphs)
    pages: list[list[str]] = [[]]

    def new_page() -> None:
        pages.append([])

    for child in body.iterchildren():
        if child.tag == f"{_W}p":
            p = Paragraph(child, document)
            explicit, rendered, before = _breaks(p)
            # Rendered breaks sit at the start of the run that begins the new page.
            for _ in range(rendered if has_rendered else int(before)):
                new_page()
            text = p.text.strip()
            if text:
                level = _heading_level(p, has_title)
                style = (p.style.name if p.style is not None else "") or ""
                if level:
                    pages[-1].append(f"{'#' * level} {text}")
                elif style.lower().startswith("list"):
                    pages[-1].append(f"- {text}")
                else:
                    pages[-1].append(text)
                pages[-1].append("")
            if explicit and not has_rendered:
                for _ in range(explicit):
                    new_page()
        elif child.tag == f"{_W}tbl":
            md = table_to_markdown(Table(child, document))
            if md:
                pages[-1] += ["", md, ""]

    single_page = len(pages) == 1
    headings = HeadingStack()
    sections: list[Section] = []
    for i, lines in enumerate(pages, start=1):
        sections += markdown_to_sections("\n".join(lines), file_name, page=None if single_page else i,
                                         page_exact=has_rendered, headings=headings)
    return sections
