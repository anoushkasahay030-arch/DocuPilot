"""Read source as data, preserving indentation and citable line ranges. Never execute it."""

import re
from pathlib import Path

from docupilot.ingest.text import decode_text
from docupilot.models import Section


def parse_code(path: Path, file_name: str, *, target_tokens: int = 450) -> list[Section]:
    return code_sections(decode_text(path.read_bytes()), file_name, target_tokens=target_tokens)


def code_sections(source: str, file_name: str, *, target_tokens: int = 450,
                  heading_path: str = "") -> list[Section]:
    if "\x00" in source:
        raise ValueError("This source file contains binary data.")
    lines = source.splitlines(keepends=True)
    sections: list[Section] = []
    start, end, size, buf = 1, 0, 0, []

    def emit(end: int) -> None:
        body = "".join(buf)
        if body.strip():
            # A longer fence also safely contains source that itself includes Markdown fences.
            fence = "`" * max(3, max(map(len, re.findall(r"`+", body)), default=0) + 1)
            sections.append(Section(text=f"{fence}\n{body.rstrip(chr(10))}\n{fence}", file_name=file_name,
                                    heading_path=" > ".join(filter(None, [heading_path, f"lines {start}–{end}"])),
                                    kind="code"))

    for number, line in enumerate(lines, 1):
        budget = target_tokens * 4
        # Minified files may be a single very long line. Bound them without losing any source text.
        for offset in range(0, len(line), budget):
            fragment = line[offset:offset + budget]
            if buf and size + len(fragment) > budget:
                emit(end)
                start, size, buf = number, 0, []
            buf.append(fragment)
            size += len(fragment)
            end = number
    emit(end)
    return sections
