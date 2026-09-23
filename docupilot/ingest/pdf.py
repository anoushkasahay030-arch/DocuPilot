"""PDF → Sections using pymupdf4llm's layout-aware markdown (headers, reading order, tables)."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pymupdf4llm

from docupilot.ingest.text import HeadingStack, markdown_to_sections
from docupilot.models import Section

log = logging.getLogger(__name__)

# Pages converted per pymupdf4llm call; keeps memory flat for very large PDFs.
_BATCH = 20


def page_count(path: Path) -> int:
    with pymupdf.open(path) as doc:
        return doc.page_count


def iter_pdf_sections(path: Path, file_name: str, *, use_ocr: bool = False) -> Iterator[tuple[int, int, list[Section]]]:
    """Yields (page_number, total_pages, sections) one page at a time."""
    headings = HeadingStack()
    with pymupdf.open(path) as doc:
        total = doc.page_count
        for start in range(0, total, _BATCH):
            pages = list(range(start, min(start + _BATCH, total)))
            try:
                chunks = pymupdf4llm.to_markdown(doc, pages=pages, page_chunks=True, use_ocr=use_ocr,
                                                 header=False, footer=False, show_progress=False)
            except TypeError:
                # Fallback for engines that don't accept the header/footer/ocr kwargs.
                chunks = pymupdf4llm.to_markdown(doc, pages=pages, page_chunks=True, show_progress=False)
            for chunk in chunks:
                page_no = int(chunk["metadata"].get("page_number") or chunk["metadata"].get("page", 0))
                if page_no == 0:
                    page_no = pages[0] + 1
                yield page_no, total, markdown_to_sections(chunk["text"], file_name, page=page_no, headings=headings)


def parse_pdf(path: Path, file_name: str, *, use_ocr: bool = False) -> list[Section]:
    out: list[Section] = []
    for _, _, sections in iter_pdf_sections(path, file_name, use_ocr=use_ocr):
        out.extend(sections)
    return out
