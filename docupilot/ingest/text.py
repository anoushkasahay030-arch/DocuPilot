"""Markdown/plain-text → structure-aware Sections. Shared by the PDF and DOCX parsers."""

import re
from pathlib import Path

from docupilot.models import Section

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)")


def clean_heading(text: str) -> str:
    return re.sub(r"\s+", " ", _EMPHASIS.sub("", text)).strip()


def is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


class HeadingStack:
    """Tracks the current heading hierarchy; carries over across pages."""

    def __init__(self) -> None:
        self.items: list[tuple[int, str]] = []

    def push(self, level: int, title: str) -> None:
        self.items = [(lvl, t) for lvl, t in self.items if lvl < level]
        self.items.append((level, title))

    @property
    def path(self) -> str:
        return " > ".join(t for _, t in self.items)


def markdown_to_sections(md: str, file_name: str, *, page: int | None = None, page_exact: bool = True,
                         headings: HeadingStack | None = None, detect_headings: bool = True) -> list[Section]:
    headings = headings if headings is not None else HeadingStack()
    sections: list[Section] = []
    buf: list[str] = []
    table: list[str] = []
    in_code = False

    def emit(lines: list[str], kind: str) -> None:
        text = "\n".join(lines).strip()
        if text:
            sections.append(Section(text=text, file_name=file_name, page=page, page_exact=page_exact,
                                    heading_path=headings.path, kind=kind))  # type: ignore[arg-type]

    for line in md.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            buf.append(line)
            continue
        if in_code:
            buf.append(line)
            continue
        if is_table_line(line):
            if not table:
                emit(buf, "text")
                buf = []
            table.append(line.strip())
            continue
        if table:
            emit(table, "table")
            table = []
        m = _HEADING.match(line) if detect_headings else None
        if m:
            title = clean_heading(m.group(2))
            if title:
                emit(buf, "text")
                buf = []
                headings.push(len(m.group(1)), title)
                continue
        buf.append(line.rstrip())
    emit(table, "table")
    emit(buf, "text")
    return sections


def parse_text_file(path: Path, file_name: str) -> list[Section]:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    is_md = path.suffix.lower() in {".md", ".markdown"}
    return markdown_to_sections(text, file_name, page=None, detect_headings=is_md)
