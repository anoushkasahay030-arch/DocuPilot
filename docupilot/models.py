from dataclasses import dataclass, field, asdict
from typing import Literal

SectionKind = Literal["text", "table", "table_schema", "code"]


@dataclass
class Section:
    """A structurally coherent piece of a document: text under one heading on one page, or one table."""

    text: str
    file_name: str
    page: int | None = None
    page_exact: bool = True
    heading_path: str = ""
    kind: SectionKind = "text"


@dataclass
class Chunk:
    chunk_id: str
    text: str
    file_id: str
    file_name: str
    session_id: str
    page: int | None = None
    page_exact: bool = True
    heading_path: str = ""
    kind: SectionKind = "text"

    def payload(self) -> dict:
        return asdict(self)

    @classmethod
    def from_payload(cls, p: dict) -> "Chunk":
        return cls(**{k: p[k] for k in cls.__dataclass_fields__ if k in p})

    def location(self) -> str:
        parts = [self.file_name]
        if self.page is not None:
            if self.file_name.lower().endswith(".pptx"):
                parts.append(f"slide {self.page}")
            else:
                parts.append(f"p. {self.page}" if self.page_exact else f"p. ~{self.page}")
        if self.heading_path:
            parts.append(self.heading_path)
        return ", ".join(parts)


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float  # rerank relevance in [0, 1]
    query: str = ""


@dataclass
class TableInfo:
    name: str  # DuckDB table name
    file_name: str
    sheet: str | None
    columns: list[tuple[str, str, str]]  # (sql_name, dtype, original header)
    row_count: int
    schema_card: str


@dataclass
class TableResult:
    question: str
    sql: str
    tables: list[str]
    file_names: list[str]
    markdown: str = ""
    row_count: int = 0
    error: str | None = None
    attempts: int = 1


@dataclass
class Citation:
    n: int
    file_name: str
    kind: str
    page: int | None = None
    page_exact: bool = True
    heading_path: str = ""
    snippet: str = ""
    sql: str | None = None
    score: float | None = None

    def label(self) -> str:
        parts = [self.file_name]
        if self.page is not None:
            if self.file_name.lower().endswith(".pptx"):
                parts.append(f"slide {self.page}")
            else:
                parts.append(f"p. {self.page}" if self.page_exact else f"p. ~{self.page}")
        if self.kind == "sql":
            parts.append("SQL query")
        return ", ".join(parts)


@dataclass
class FileInfo:
    file_id: str
    name: str
    path: str
    kind: Literal["document", "spreadsheet"]
    pages: int | None = None
    chunks: int = 0
    tables: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_url: str | None = None
