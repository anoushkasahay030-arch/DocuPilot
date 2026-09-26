"""Read embedded Next.js App Router page data without executing JavaScript.

This is a fallback for otherwise empty HTML, not a React renderer. Only elements
in the initial route's server-provided children are recovered. Arbitrary props,
module code, loading/error fallbacks, and records outside that tree are ignored.
"""

import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

_PUSH = re.compile(r"\s*self\.__next_f\.push\((\[.*\])\)\s*;?\s*", re.S)
_REFERENCE = re.compile(r"\$(?:L)?([0-9a-f]+)$")
_RECORD = re.compile(rb"([0-9a-f]*):")
_TEXT_LENGTH = re.compile(rb"T([0-9a-f]+),")
_TAGS = set("""article section main header footer div span p a b strong i em u s small
    h1 h2 h3 h4 h5 h6 ul ol li dl dt dd blockquote pre code br hr
    table thead tbody tfoot tr th td caption img sub sup time address""".split())
_SKIP = {"script", "style", "noscript", "template", "head", "nav", "form", "svg"}


@dataclass
class _Text:
    value: str  # Length-delimited text is literal, including leading dollar signs.


def _records(soup: BeautifulSoup) -> dict[str, object]:
    parts = []
    for script in soup.find_all("script"):
        if script.get("src"):
            continue
        match = _PUSH.fullmatch(script.get_text())
        if match:
            packet = json.loads(match[1])
            if isinstance(packet, list) and len(packet) == 2 and packet[0] == 1 and isinstance(packet[1], str):
                parts.append(packet[1])
    # Flight text records use byte lengths, and records can span several script tags.
    data = "".join(parts).encode("utf-8")
    records: dict[str, object] = {}
    offset = 0
    while offset < len(data):
        header = _RECORD.match(data, offset)
        if not header:
            raise ValueError("Invalid page data record")
        key = header[1].decode()
        offset = header.end()
        if data[offset:offset + 1] == b"T":
            length = _TEXT_LENGTH.match(data, offset)
            if not length:
                raise ValueError("Invalid page text record")
            start = length.end()
            offset = start + int(length[1], 16)
            if offset > len(data):
                raise ValueError("Incomplete page text record")
            records[key] = _Text(data[start:offset].decode("utf-8"))
        else:
            end = data.find(b"\n", offset)
            if end < 0:
                raise ValueError("Incomplete page data record")
            row = data[offset:end]
            # Only untagged JSON model records. Module imports, hints and errors
            # are protocol metadata, never evidence.
            if row[:1] in (b"[", b"{", b'"', b"-") or row[:1].isdigit() or row == b"null":
                records[key] = json.loads(row)
            offset = end + 1
    return records


def embedded_page(soup: BeautifulSoup) -> BeautifulSoup | None:
    """Return a text-only DOM for supported initial route data, or no fallback."""
    try:
        records = _records(soup)
        root = records.get("0")
        if not isinstance(root, dict) or not isinstance(root.get("f"), list):
            return None
        output = BeautifulSoup("<body></body>", "html.parser")
        remaining = 50_000  # Bound cyclic/shared record expansion, as well as depth.
        text_left = 2_000_000

        def visit(value, parent: Tag, *, text: bool = False, depth: int = 0,
                  active: frozenset[str] = frozenset()) -> None:
            nonlocal remaining, text_left
            remaining -= 1
            if remaining < 0 or depth > 100:
                raise ValueError("Page data is too complex")
            if isinstance(value, _Text):
                if text:
                    text_left -= len(value.value)
                    if text_left < 0:
                        raise ValueError("Page text is too large")
                    parent.append(value.value)
            elif isinstance(value, str):
                reference = _REFERENCE.fullmatch(value)
                if reference:
                    key = reference[1]
                    if key in active:
                        raise ValueError("Cyclic page data")
                    if key in records:
                        visit(records[key], parent, text=text, depth=depth + 1, active=active | {key})
                    return
                if value.startswith("$$"):
                    value = value[1:]
                elif value.startswith("$"):
                    return  # Unsupported protocol values, not visible strings.
                if text:
                    text_left -= len(value)
                    if text_left < 0:
                        raise ValueError("Page text is too large")
                    parent.append(value)
            elif isinstance(value, list):
                if len(value) >= 4 and value[0] == "$" and isinstance(value[3], dict):
                    name, props = value[1], value[3]
                    if not isinstance(name, str):
                        return
                    style = props.get("style")
                    if (name in _SKIP or props.get("hidden") not in (None, False)
                            or props.get("aria-hidden") in (True, "true")
                            or isinstance(style, dict) and (style.get("display") == "none"
                                                           or style.get("visibility") == "hidden")):
                        return
                    # Client components and fragments are transparent containers;
                    # do not infer content from their other properties.
                    if name not in _TAGS and not name.startswith("$"):
                        return
                    node = output.new_tag(name if name in _TAGS else "span")
                    if props.get("role") == "contentinfo":
                        node["role"] = "contentinfo"
                    if name == "img" and isinstance(props.get("alt"), str):
                        node["alt"] = props["alt"]
                    parent.append(node)
                    visit(props.get("children"), node, text=True, depth=depth + 1, active=active)
                    slots = props.get("slots")
                    if name.startswith("$") and isinstance(slots, dict):
                        for child in slots.values():
                            visit(child, node, text=True, depth=depth + 1, active=active)
                else:
                    for child in value:
                        visit(child, parent, text=text, depth=depth + 1, active=active)
            elif text and type(value) in (int, float):
                parent.append(str(value))

        def seed(value, depth: int = 0) -> None:
            if not isinstance(value, list) or len(value) < 2:
                return
            if depth > 100:
                raise ValueError("Page route is too deep")
            # Current CacheNodeSeedData starts with the element; older versions
            # prepend a segment name. Never visit the loading fallback slot.
            index = 0 if isinstance(value[1], dict) else 1
            if len(value) <= index + 1 or not isinstance(value[index + 1], dict):
                return
            visit(value[index], output.body)
            for child in value[index + 1].values():
                seed(child, depth + 1)

        for route in root["f"]:
            if isinstance(route, list) and len(route) >= 3:
                # FlightDataPath ends with [seedData, head, isHeadPartial].
                seed(route[-3])
        return output
    except (ValueError, TypeError, RecursionError):
        # Unsupported/malformed payloads must not leak raw script text as evidence.
        return None
