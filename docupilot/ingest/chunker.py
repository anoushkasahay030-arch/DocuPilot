"""Structure-aware chunking.

- Chunks never cross a heading or page boundary, so every chunk has one precise citation.
- Tables are never split mid-row; oversized tables are split by rows with the header repeated.
- Text is packed by paragraph (then sentence) with a small overlap.
"""

import re

from docupilot.models import Chunk, Section

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def n_tokens(text: str) -> int:
    return len(text) // 4 + 1


def _units(text: str, target: int) -> list[str]:
    """Paragraphs, with over-long paragraphs broken into sentences (and hard-wrapped as a last resort)."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if n_tokens(para) <= target:
            out.append(para)
            continue
        for sent in _SENT.split(para):
            while n_tokens(sent) > target:
                cut = sent.rfind(" ", 0, target * 4)
                if cut <= 0:
                    cut = target * 4
                out.append(sent[:cut].strip())
                sent = sent[cut:].strip()
            if sent:
                out.append(sent)
    return out


def split_text(text: str, target: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for unit in _units(text, target):
        t = n_tokens(unit)
        if cur and size + t > target:
            chunks.append("\n\n".join(cur))
            # Seed the next chunk with trailing units from this one, up to `overlap` tokens.
            tail, tail_size = [], 0
            for u in reversed(cur):
                if tail_size + n_tokens(u) > overlap:
                    break
                tail.insert(0, u)
                tail_size += n_tokens(u)
            cur, size = tail, tail_size
        cur.append(unit)
        size += t
    if cur:
        chunks.append("\n\n".join(cur))
    return chunks


def split_table(md: str, max_tokens: int) -> list[str]:
    lines = [ln for ln in md.splitlines() if ln.strip()]
    if n_tokens(md) <= max_tokens or len(lines) <= 3:
        return [md]
    has_sep = len(lines) > 1 and set(lines[1].replace("|", "").strip()) <= set("-: ")
    header = lines[:2] if has_sep else lines[:1]
    rows = lines[len(header):]
    parts, cur = [], []
    budget = max_tokens - n_tokens("\n".join(header))
    for row in rows:
        if cur and n_tokens("\n".join(cur + [row])) > budget:
            parts.append("\n".join(header + cur))
            cur = []
        cur.append(row)
    if cur:
        parts.append("\n".join(header + cur))
    return parts


def chunk_sections(sections: list[Section], *, file_id: str, session_id: str, target_tokens: int = 450,
                   overlap_tokens: int = 60, max_table_tokens: int = 900) -> list[Chunk]:
    chunks: list[Chunk] = []
    for s in sections:
        if s.kind == "text":
            bodies = split_text(s.text, target_tokens, overlap_tokens)
        elif s.kind == "table":
            bodies = split_table(s.text, max_table_tokens)
        else:
            bodies = [s.text]
        for body in bodies:
            chunks.append(Chunk(chunk_id=f"{file_id}:{len(chunks)}", text=body, file_id=file_id,
                                file_name=s.file_name, session_id=session_id, page=s.page,
                                page_exact=s.page_exact, heading_path=s.heading_path, kind=s.kind))
    return chunks


def embed_text(chunk: Chunk) -> str:
    """Contextual header + body. The header helps both dense and BM25 match file/section names."""
    header = chunk.file_name + (f" › {chunk.heading_path}" if chunk.heading_path else "")
    return f"[{header}]\n{chunk.text}"
