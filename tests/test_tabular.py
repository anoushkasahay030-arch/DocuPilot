import pandas as pd
import pytest

from docupilot.ingest.tabular import TableStore, UnsafeSQLError, detect_header, guard_sql
from eval.corpus import _orders, table_facts


@pytest.fixture(scope="module")
def tables(corpus):
    ts = TableStore()
    ts.add_file(corpus["xlsx"], "sales_2025.xlsx")
    ts.add_file(corpus["csv"], "customers.csv")
    return ts


def test_tables_and_types(tables):
    assert set(tables.tables) == {"sales_2025__orders", "sales_2025__targets", "customers"}
    types = {c: t for c, t, _ in tables.tables["sales_2025__orders"].columns}
    assert types["revenue"] in {"BIGINT", "DOUBLE"} and types["date"].startswith("TIMESTAMP")
    assert dict((c, t) for c, t, _ in tables.tables["customers"].columns)["churned"] == "BOOLEAN"


def test_sql_matches_pandas(tables):
    facts = table_facts()
    df = tables.run_select("SELECT SUM(revenue) AS total FROM sales_2025__orders")
    assert f"{int(df.total[0]):,}" == facts["total_revenue"]
    df = tables.run_select("SELECT region, SUM(revenue) r FROM sales_2025__orders GROUP BY 1 ORDER BY 2 DESC")
    assert df.region[0] == facts["top_region"]
    expected = _orders().groupby("Region")["Revenue"].sum().sort_values(ascending=False)
    assert list(df.r.astype(int)) == list(expected.astype(int))
    df = tables.run_select("SELECT COUNT(*) n FROM customers WHERE churned")
    assert str(df.n[0]) == facts["churned_customers"]


def test_row_limit(tables):
    assert len(tables.run_select("SELECT * FROM sales_2025__orders", max_rows=10)) == 10


@pytest.mark.parametrize("sql", [
    "DROP TABLE customers",
    "SELECT 1; DROP TABLE customers",
    "INSERT INTO customers VALUES (1)",
    "SELECT * FROM read_csv('/etc/passwd')",
    "COPY customers TO '/tmp/x.csv'",
    "ATTACH '/tmp/x.db'",
])
def test_guard_rejects_unsafe_sql(sql):
    with pytest.raises(UnsafeSQLError):
        guard_sql(sql)


def test_guard_allows_cte_and_trailing_semicolon():
    assert guard_sql("WITH a AS (SELECT 1) SELECT * FROM a;").endswith("FROM a")


def test_external_access_blocked_even_if_guard_bypassed(tables):
    with pytest.raises(Exception):
        tables.con.execute("SELECT * FROM '/etc/hosts'").fetchall()


def test_detect_header_skips_title_rows():
    raw = pd.DataFrame([["My Report", None, None], [None, None, None], ["Name", "Qty", "Price"],
                        ["a", 1, 2.5], ["b", 2, 3.5]])
    df = detect_header(raw)
    assert list(df.columns) == ["Name", "Qty", "Price"] and len(df) == 2
