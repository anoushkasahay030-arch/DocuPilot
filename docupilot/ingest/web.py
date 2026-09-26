"""Explicit HTTP(S) page import. No script execution, crawling, cookies or resource loading."""

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

_URL = re.compile(r"https?://[^\s<>\"'`]+", re.I)
_CODE = re.compile(r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`")


def parse_web_message(content: str) -> tuple[list[str], str]:
    """Find pasted page links before routing. Ignore URLs inside source-code examples."""
    content = content.strip()
    command = re.match(r"^/url(?:\s|$)", content, re.I)
    if command:
        content = content[command.end():].strip()
    visible = _CODE.sub("", content)
    urls: list[str] = []
    for match in _URL.finditer(visible):
        url = match.group()
        # Strip prose/Markdown delimiters while preserving balanced URL path parentheses.
        while True:
            previous = url
            url = url.rstrip(".,;!")
            for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
                while url.endswith(closing) and url.count(closing) > url.count(opening):
                    url = url[:-1]
            if url == previous:
                break
        validate_url(url)
        if url not in urls:
            urls.append(url)
    if command and not urls:
        raise ValueError("Use `/url https://example.com/page` followed by an optional question.")
    remainder = _URL.sub("", content).strip(" \t\r\n()[]<>.,;!?\"")
    # A URL-only message imports the page; it doesn't need an invented conversational response.
    return urls, content if remainder or not urls else ""


def url_key(url: str) -> str:
    validate_url(url)
    return str(httpx.URL(url).copy_with(fragment=None))


@dataclass
class WebPage:
    content: bytes
    url: str
    file_name: str


def validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use an http:// or https:// page URL without embedded credentials.")


def fetch_page(url: str, *, max_bytes: int, transport: httpx.BaseTransport | None = None) -> WebPage:
    validate_url(url)
    with httpx.Client(timeout=httpx.Timeout(30, connect=5), trust_env=False, transport=transport) as client:
        for _ in range(6):
            validate_url(url)
            client.cookies.clear()
            with client.stream("GET", url, headers={"Accept": "text/html, application/xhtml+xml"}) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Web page redirect has no destination.")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if media_type not in {"text/html", "application/xhtml+xml"}:
                    raise ValueError("This URL does not serve HTML. Download the file and attach it instead.")
                content = bytearray()
                for part in response.iter_bytes(chunk_size=65536):
                    content.extend(part)
                    if len(content) > max_bytes:
                        raise ValueError("Web page exceeds the upload size limit.")
                key = hashlib.sha256(url.encode()).hexdigest()[:10]
                host = urlsplit(url).hostname
                return WebPage(bytes(content), str(response.url), f"{host}-{key}.html")
    raise ValueError("Web page has too many redirects.")
