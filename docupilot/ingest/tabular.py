"""CSV/XLSX → typed DuckDB tables + a "schema card" for retrieval and SQL generation.

Real spreadsheets are messy: title rows above the header, blank rows/columns, numbers stored as
"$1,200" strings. We clean those up so the Table agent can answer with exact SQL instead of guessing
from a text dump.
"""

import csv
import re
import threading
from pathlib import Path

import duckdb
import pandas as pd

from docupilot.models import TableInfo

_BOOLS = {"true": True, "false": False, "yes": True, "no": False, "y": True, "n": False}
_NUM_JUNK = re.compile(r"[,$€£¥%\s]")
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|pragma|install|load|export|import|call|"
    r"set|reset|checkpoint|vacuum|truncate|grant|use)\b|\bread_\w+\s*\(|\bglob\s*\(",
    re.I,
)


def sanitize(name: str, fallback: str = "col") -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(name)).strip("_").lower()
    if not s:
        s = fallback
    if s[0].isdigit():
        s = f"{fallback}_{s}"
    return s


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}_{seen[n]}")
        else:
            seen[n] = 1
            out.append(n)
    return out


def _is_texty(v) -> bool:
    if pd.isna(v):
        return False
    if isinstance(v, str):
        try:
            float(_NUM_JUNK.sub("", v))
            return False
        except ValueError:
            return True
    return False


def detect_header(raw: pd.DataFrame) -> pd.DataFrame:
    """Finds the real header row (skipping titles/blank rows) and returns a frame with proper columns."""
    raw = raw.dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
    if raw.empty:
        return raw
    widest = raw.head(30).notna().sum(axis=1).max()
    header_idx = 0
    for i in range(min(10, len(raw))):
        row = raw.iloc[i]
        filled = row.notna().sum()
        if filled >= max(1, 0.8 * widest) and sum(_is_texty(v) for v in row) >= 0.6 * filled:
            header_idx = i
            break
    header = raw.iloc[header_idx].tolist()
    names = [str(h).strip() if not pd.isna(h) else f"column_{j + 1}" for j, h in enumerate(header)]
    df = raw.iloc[header_idx + 1:].reset_index(drop=True)
    df.columns = names
    return df.dropna(how="all")


def _coerce(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        s = df[col]
        if not (pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s)):
            continue
        nonnull = s.dropna()
        if nonnull.empty:
            continue
        as_str = nonnull.astype(str)
        lowered = as_str.str.strip().str.lower()
        if lowered.isin(_BOOLS).all():
            df[col] = s.map(lambda v: None if pd.isna(v) else _BOOLS[str(v).strip().lower()]).astype("boolean")
            continue
        num = pd.to_numeric(as_str.str.replace(_NUM_JUNK, "", regex=True), errors="coerce")
        if num.notna().mean() >= 0.9:
            df[col] = pd.to_numeric(s.astype(str).str.replace(_NUM_JUNK, "", regex=True), errors="coerce")
            continue
        if as_str.str.contains(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}", regex=True).mean() >= 0.9:
            dt = pd.to_datetime(s, errors="coerce", format="mixed")
            if dt.notna().sum() >= 0.9 * len(nonnull):
                df[col] = dt
                continue
        df[col] = s.astype(str).where(s.notna(), None)
    return df


def _sniff_sep(path: Path) -> str:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(64_000)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def load_frames(path: Path) -> list[tuple[str | None, pd.DataFrame]]:
    ext = path.suffix.lower()
    if ext in {".csv", ".tsv"}:
        sep = "\t" if ext == ".tsv" else _sniff_sep(path)
        raw = pd.read_csv(path, sep=sep, header=None, dtype=object, encoding="utf-8-sig",
                          encoding_errors="replace", on_bad_lines="skip")
        return [(None, raw)]
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    return list(sheets.items())


def _md_value(v) -> str:
    if v is None or (not isinstance(v, (list, dict)) and pd.isna(v)):
        return ""
    if isinstance(v, float):
        return f"{v:,.4g}" if abs(v) < 1e-3 or abs(v) >= 1e15 else f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d") if v == v.normalize() else str(v)
    return str(v).replace("|", "\\|").replace("\n", " ")


def frame_to_markdown(df: pd.DataFrame, max_rows: int = 50) -> str:
    cols = [str(c) for c in df.columns]
    lines = ["|" + "|".join(cols) + "|", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.head(max_rows).itertuples(index=False):
        lines.append("|" + "|".join(_md_value(v) for v in row) + "|")
    if len(df) > max_rows:
        lines.append(f"\n… {len(df) - max_rows} more rows")
    return "\n".join(lines)


class UnsafeSQLError(ValueError):
    pass


def guard_sql(sql: str) -> str:
    """Allows exactly one read-only SELECT/WITH statement."""
    s = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", s, re.I):
        raise UnsafeSQLError("Only SELECT queries are allowed.")
    if ";" in s:
        raise UnsafeSQLError("Only a single statement is allowed.")
    if m := _FORBIDDEN.search(s):
        raise UnsafeSQLError(f"Disallowed keyword: {m.group(0)!r}")
    return s


class TableStore:
    """An in-memory DuckDB database holding every spreadsheet table of one chat session."""

    def __init__(self) -> None:
        self.con = duckdb.connect(":memory:")
        # Real sandbox (the regex guard is only a first line of defence): generated SQL can't read or write
        # files or URLs, and this can't be switched back on while the database is open.
        self.con.execute("SET enable_external_access = false")
        self.tables: dict[str, TableInfo] = {}
        self._lock = threading.Lock()

    def _unique(self, base: str) -> str:
        name, i = base, 2
        while name in self.tables:
            name, i = f"{base}_{i}", i + 1
        return name

    def add_file(self, path: Path, file_name: str) -> list[TableInfo]:
        frames = load_frames(path)
        stem = sanitize(Path(file_name).stem, "t")
        out = []
        for sheet, raw in frames:
            df = detect_header(raw)
            if df.empty or len(df.columns) == 0:
                continue
            original = [str(c) for c in df.columns]
            df.columns = _dedupe([sanitize(c) for c in original])
            df = _coerce(df)
            name = self._unique(f"{stem}__{sanitize(sheet, 's')}" if sheet is not None else stem)
            with self._lock:
                self.con.register("_incoming", df)
                self.con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM _incoming')
                self.con.unregister("_incoming")
                types = self.con.execute(
                    "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? "
                    "ORDER BY ordinal_position", [name]).fetchall()
            columns = [(c, t, o) for (c, t), o in zip(types, original)]
            info = TableInfo(name=name, file_name=file_name, sheet=sheet, columns=columns, row_count=len(df),
                             schema_card="")
            info.schema_card = self._schema_card(info, df)
            self.tables[name] = info
            out.append(info)
        return out

    def _schema_card(self, info: TableInfo, df: pd.DataFrame) -> str:
        where = f"file {info.file_name}" + (f", sheet '{info.sheet}'" if info.sheet else "")
        lines = [f"Table `{info.name}` ({where}) — {info.row_count} rows.", "Columns:"]
        for (col, dtype, orig), src in zip(info.columns, df.columns):
            s = df[src].dropna()
            detail = ""
            if not s.empty:
                if pd.api.types.is_numeric_dtype(s):
                    detail = f"range {_md_value(s.min())} to {_md_value(s.max())}"
                elif pd.api.types.is_datetime64_any_dtype(s):
                    detail = f"from {_md_value(s.min())} to {_md_value(s.max())}"
                else:
                    uniq = s.astype(str).unique()
                    shown = ", ".join(v[:40] for v in uniq[:8])
                    detail = f"{len(uniq)} distinct, e.g. {shown}"
            label = f" (header: \"{orig}\")" if orig != col else ""
            lines.append(f"- {col} {dtype}{label}: {detail}")
        lines += ["Sample rows:", frame_to_markdown(df, max_rows=5)]
        return "\n".join(lines)

    def run_select(self, sql: str, *, max_rows: int = 200, timeout_s: float = 10.0) -> pd.DataFrame:
        safe = guard_sql(sql)
        cur = self.con.cursor()
        timer = threading.Timer(timeout_s, cur.interrupt)
        timer.start()
        try:
            return cur.execute(f"SELECT * FROM ({safe}) AS _q LIMIT {int(max_rows)}").df()
        except duckdb.InterruptException as e:
            raise TimeoutError(f"Query exceeded {timeout_s}s") from e
        finally:
            timer.cancel()
            cur.close()

