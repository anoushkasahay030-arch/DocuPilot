"""Table/Data agent: text-to-SQL over the session's DuckDB tables, with self-correction on errors."""

import asyncio
import re

from pydantic import BaseModel, Field
from langchain_core.runnables import RunnableConfig

from docupilot import prompts
from docupilot.agents.context import deps, emit
from docupilot.agents.state import GraphState
from docupilot.ingest.tabular import frame_to_markdown
from docupilot.models import TableResult

MAX_FIXES = 2


class SQLQuery(BaseModel):
    purpose: str = Field(description="What this query computes, in a few words")
    sql: str


class SQLPlan(BaseModel):
    queries: list[SQLQuery] = Field(default_factory=list)


class SQLFix(BaseModel):
    sql: str


async def _run(d, sql: str):
    s = d.settings
    return await asyncio.to_thread(d.workspace.tables.run_select, sql, max_rows=s.sql_max_rows,
                                   timeout_s=s.sql_timeout_s)


async def table_node(state: GraphState, config: RunnableConfig) -> GraphState:
    d = deps(config)
    ws = d.workspace
    targets = set(state.get("target_files") or [])
    tables = [t for t in ws.tables.tables.values() if not targets or t.file_name in targets] \
        or list(ws.tables.tables.values())
    schema = "\n\n".join(t.schema_card for t in tables)
    question = state["standalone"]

    plan = await d.llm.generate_json(f"Tables:\n{schema}\n\nQuestion: {question}", SQLPlan,
                                     system=prompts.TABLE_SYSTEM)
    results: list[TableResult] = []
    for q in plan.queries[:3]:
        sql, error, attempts = q.sql, None, 1
        df = None
        while True:
            try:
                df = await _run(d, sql)
                error = None
                break
            except Exception as e:  # noqa: BLE001 - any DuckDB/guard error is fed back to the model
                error = str(e).splitlines()[0][:400]
                if attempts > MAX_FIXES:
                    break
                fix = await d.llm.generate_json(
                    f"Tables:\n{schema}\n\nQuestion: {question}\nPurpose: {q.purpose}\n\n"
                    + prompts.TABLE_FIX.format(sql=sql, error=error), SQLFix, system=prompts.TABLE_SYSTEM)
                sql, attempts = fix.sql, attempts + 1
        used = [t for t in tables if re.search(rf'\b{re.escape(t.name)}\b', sql)]
        res = TableResult(question=q.purpose, sql=sql, tables=[t.name for t in used] or [t.name for t in tables],
                          file_names=sorted({t.file_name for t in used}), attempts=attempts, error=error)
        if df is not None:
            res.markdown = frame_to_markdown(df, max_rows=50) if len(df) else "(no rows)"
            res.row_count = len(df)
        results.append(res)

    detail = "\n\n".join(
        f"**{r.question}**" + (f" (fixed after {r.attempts - 1} error(s))" if r.attempts > 1 and not r.error else "")
        + f"\n```sql\n{r.sql}\n```\n" + (f"Error: {r.error}" if r.error else r.markdown)
        for r in results) or "The tables can't answer this question."
    ok = sum(1 for r in results if not r.error)
    emit("step", agent="Table", title=f"Ran {ok}/{len(results)} SQL queries", detail=detail)
    return {"table_results": results}
