"""Static HTML → text, headings, tables and code, without executing scripts or loading resources."""

import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

from docupilot.ingest.code import code_sections
from docupilot.ingest.text import HeadingStack, table_markdown
from docupilot.models import Section


def parse_html(path: Path, file_name: str, *, target_tokens: int = 450) -> list[Section]:
    soup = BeautifulSoup(path.read_bytes(), "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in list(soup.find_all(True)):
        if tag.attrs is None:  # removed with an ancestor
            continue
        style = re.sub(r"\s+", "", tag.get("style", "")).lower()
        if (tag.name in {"script", "style", "noscript", "template", "head", "nav", "form"}
                or tag.has_attr("hidden") or tag.get("aria-hidden") == "true"
                or "display:none" in style or "visibility:hidden" in style):
            tag.decompose()

    headings = HeadingStack()
    if title:
        headings.push(1, title)
    sections: list[Section] = []
    buf: list[str] = []

    def emit() -> None:
        body = re.sub(r"[ \t]+", " ", "".join(buf)).strip()
        if body:
            sections.append(Section(body, file_name, heading_path=headings.path))
        buf.clear()

    def walk(node) -> None:
        if isinstance(node, NavigableString):
            # Comments are also NavigableString subclasses; only ordinary visible text is content.
            if type(node) is NavigableString:
                buf.append(re.sub(r"\s+", " ", str(node)))
            return
        if not isinstance(node, Tag):
            return
        if node.name == "footer" or node.get("role") == "contentinfo":
            emit()
            previous = headings.items[:]
            headings.items = [(1, title)] if title else []
            headings.push(2, "Footer")
            for child in node.children:
                walk(child)
            emit()
            headings.items = previous
        elif re.fullmatch(r"h[1-6]", node.name):
            emit()
            heading = node.get_text(" ", strip=True)
            headings.push(int(node.name[1]), heading)
            buf.append(heading + "\n")
        elif node.name == "table":
            emit()
            rows = [[cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"], recursive=False)]
                    for row in node.find_all("tr")]
            body = table_markdown([row for row in rows if row])
            if body:
                sections.append(Section(body, file_name, heading_path=headings.path, kind="table"))
        elif node.name == "pre":
            emit()
            body = node.get_text().strip("\n")
            if body.strip():
                sections.extend(code_sections(body, file_name, heading_path=headings.path,
                                              target_tokens=target_tokens))
        elif node.name == "img":
            if node.get("alt"):
                buf.append(" " + node["alt"] + " ")
        elif node.name == "br":
            buf.append("\n")
        else:
            block = node.name in {"p", "div", "section", "article", "main", "li", "ul", "ol", "blockquote"}
            if block:
                buf.append("\n")
            for child in node.children:
                walk(child)
            if block:
                buf.append("\n")

    walk(soup.body or soup)
    emit()
    return sections
