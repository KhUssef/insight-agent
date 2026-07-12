"""The three tools as plain functions, exercised without any LLM or protocol."""

import json
from pathlib import Path

import duckdb
import pytest

from insight_agent.sql_guard import SQLValidationError
from insight_agent.tools import create_chart, describe_schema, run_sql


def test_describe_schema_lists_the_sales_table(con: duckdb.DuckDBPyConnection) -> None:
    schema = describe_schema(con)
    assert json.dumps(schema)
    tables = {table["table"]: table for table in schema["tables"]}
    assert "sample_sales" in tables
    sample_sales = tables["sample_sales"]
    assert sample_sales["row_count"] > 0
    column_names = [column["name"] for column in sample_sales["columns"]]
    assert "region" in column_names
    region = next(column for column in sample_sales["columns"] if column["name"] == "region")
    assert set(region["distinct_values"]) == {"North", "South", "East", "West"}


def test_run_sql_returns_json_rows(con: duckdb.DuckDBPyConnection) -> None:
    result = run_sql(con, "SELECT region, sum(revenue) AS total FROM sample_sales GROUP BY region")
    assert json.dumps(result)
    assert result["columns"] == ["region", "total"]
    assert result["row_count"] == 4
    assert result["truncated"] is False


def test_run_sql_truncates_large_results(con: duckdb.DuckDBPyConnection) -> None:
    result = run_sql(con, "SELECT * FROM sample_sales", max_rows=10)
    assert result["row_count"] == 10
    assert result["truncated"] is True


def test_run_sql_rejects_writes(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(SQLValidationError):
        run_sql(con, "DELETE FROM sample_sales")
    row = con.execute("SELECT count(*) FROM sample_sales").fetchone()
    assert row is not None
    assert row[0] > 0


def test_create_chart_writes_png(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    result = create_chart(
        con,
        sql=(
            "SELECT region, sum(revenue) AS total FROM sample_sales "
            "GROUP BY region ORDER BY region"
        ),
        chart_type="bar",
        x="region",
        y="total",
        title="Revenue by region",
        filename="by_region",
        charts_dir=tmp_path,
    )
    path = Path(result["path"])
    assert path.exists()
    assert path.suffix == ".png"
    assert path.parent == tmp_path
    assert result["rows_plotted"] == 4


def test_create_chart_supports_multiple_series(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    result = create_chart(
        con,
        sql=(
            "SELECT region, "
            "sum(CASE WHEN month(order_date) IN (4,5,6) THEN revenue ELSE 0 END) AS q2, "
            "sum(CASE WHEN month(order_date) IN (7,8,9) THEN revenue ELSE 0 END) AS q3 "
            "FROM sample_sales GROUP BY region ORDER BY region"
        ),
        chart_type="bar",
        x="region",
        y=["q2", "q3"],
        title="Q2 vs Q3 revenue by region",
        filename="q2_vs_q3.png",
        charts_dir=tmp_path,
    )
    assert Path(result["path"]).exists()


@pytest.mark.parametrize(
    "filename",
    ["../escape", "..\\escape", "a/b.png", "a\\b.png", "", ".hidden", "bad name!.png"],
)
def test_create_chart_rejects_unsafe_filenames(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, filename: str
) -> None:
    with pytest.raises(ValueError):
        create_chart(
            con,
            sql="SELECT region, sum(revenue) AS total FROM sample_sales GROUP BY region",
            chart_type="bar",
            x="region",
            y="total",
            title="t",
            filename=filename,
            charts_dir=tmp_path,
        )


def test_create_chart_rejects_unknown_columns(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    with pytest.raises(ValueError):
        create_chart(
            con,
            sql="SELECT region FROM sample_sales LIMIT 5",
            chart_type="bar",
            x="region",
            y="missing",
            title="t",
            filename="c.png",
            charts_dir=tmp_path,
        )


def test_create_chart_rejects_unknown_chart_type(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    with pytest.raises(ValueError):
        create_chart(
            con,
            sql="SELECT region, sum(revenue) AS total FROM sample_sales GROUP BY region",
            chart_type="pie",
            x="region",
            y="total",
            title="t",
            filename="c.png",
            charts_dir=tmp_path,
        )
