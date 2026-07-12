"""The three data-analysis tools as plain, deterministic functions.

These functions contain no LLM calls and no protocol code. They take an open
DuckDB connection plus validated arguments and return JSON-serializable
dictionaries. The MCP server wraps them for protocol exposure; tests exercise
them directly without a network or an API key.

Charts are rendered in the application's house style, applied once at import
time through matplotlib rcParams: a soft paper background, a teal-first color
cycle, open spines, and a dotted horizontal grid, so the output sits naturally
inside the web frontend.
"""

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from insight_agent.sql_guard import validate_read_only

_CHART_COLORS = ["#0e7c7b", "#c98a1b", "#5c6b68", "#3b7fb8", "#a34a4a", "#6a5a9e"]

plt.rcParams.update(
    {
        "figure.facecolor": "#fbfcfc",
        "axes.facecolor": "#fbfcfc",
        "savefig.facecolor": "#fbfcfc",
        "figure.dpi": 144,
        "savefig.dpi": 144,
        "font.family": "sans-serif",
        "font.sans-serif": ["Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"],
        "text.color": "#182422",
        "axes.edgecolor": "#c9d4d2",
        "axes.linewidth": 1.0,
        "axes.labelcolor": "#5c6b68",
        "axes.titlecolor": "#182422",
        "axes.titlelocation": "left",
        "axes.titleweight": "bold",
        "axes.titlesize": 12,
        "axes.titlepad": 12,
        "axes.labelsize": 9.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.grid.axis": "y",
        "grid.color": "#c9d4d2",
        "grid.linestyle": ":",
        "grid.linewidth": 0.8,
        "axes.axisbelow": True,
        "xtick.color": "#5c6b68",
        "ytick.color": "#5c6b68",
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
        "legend.frameon": False,
        "legend.fontsize": 9,
        "lines.linewidth": 2.0,
        "axes.prop_cycle": plt.cycler(color=_CHART_COLORS),
    }
)

_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CHART_TYPES = ("bar", "line", "scatter")

_MAX_DISTINCT_SAMPLES = 12


def _jsonable(value: Any) -> Any:
    """Convert a single cell to a JSON-serializable value."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def describe_schema(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Describe every table in the database: columns, types, row counts.

    For low-cardinality text columns the distinct values are included so a
    caller can write correct filters without guessing at spellings.
    """
    tables: list[dict[str, Any]] = []
    table_names = [
        row[0]
        for row in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
    ]
    for table_name in table_names:
        columns_info = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
            [table_name],
        ).fetchall()
        row_count = con.execute(f'SELECT count(*) FROM "{table_name}"').fetchone()
        columns: list[dict[str, Any]] = []
        for column_name, data_type in columns_info:
            column: dict[str, Any] = {"name": column_name, "type": data_type}
            kind = data_type.upper()
            if kind in ("VARCHAR", "TEXT", "STRING"):
                distinct = con.execute(
                    f'SELECT DISTINCT "{column_name}" FROM "{table_name}" '
                    f'ORDER BY "{column_name}" LIMIT {_MAX_DISTINCT_SAMPLES + 1}'
                ).fetchall()
                if len(distinct) <= _MAX_DISTINCT_SAMPLES:
                    column["distinct_values"] = [_jsonable(row[0]) for row in distinct]
            elif kind in ("DATE", "TIMESTAMP", "DOUBLE", "FLOAT") or any(
                marker in kind for marker in ("INT", "DECIMAL")
            ):
                bounds = con.execute(
                    f'SELECT min("{column_name}"), max("{column_name}") FROM "{table_name}"'
                ).fetchone()
                if bounds is not None:
                    column["min"] = _jsonable(bounds[0])
                    column["max"] = _jsonable(bounds[1])
            columns.append(column)
        tables.append(
            {
                "table": table_name,
                "row_count": row_count[0] if row_count is not None else 0,
                "columns": columns,
            }
        )
    return {"tables": tables}


def run_sql(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    max_rows: int = 200,
) -> dict[str, Any]:
    """Execute a validated read-only SELECT and return the result as JSON data.

    Raises SQLValidationError if the statement is not a single read-only
    SELECT. Results are truncated to max_rows, with a flag saying so.
    """
    validate_read_only(sql)
    cursor = con.execute(sql)
    column_names = [desc[0] for desc in cursor.description or []]
    fetched = cursor.fetchmany(max_rows + 1)
    truncated = len(fetched) > max_rows
    rows = [[_jsonable(value) for value in row] for row in fetched[:max_rows]]
    return {
        "columns": column_names,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }


def _validate_filename(filename: str) -> str:
    """Sanitize a model-supplied filename component into a safe .png basename."""
    name = Path(filename).name
    if name != filename or not _FILENAME_RE.match(name):
        raise ValueError(
            "filename must be a plain basename using letters, digits, dot, dash, underscore"
        )
    if not name.lower().endswith(".png"):
        name = f"{name}.png"
    return name


def create_chart(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    chart_type: str,
    x: str,
    y: str | list[str],
    title: str,
    filename: str,
    charts_dir: Path,
) -> dict[str, Any]:
    """Run a read-only query and render its result as a PNG chart.

    The x and y arguments must name columns of the query result; chart_type
    must be one of bar, line, or scatter. A legend appears only when more
    than one series is plotted, and short category labels on the x axis stay
    horizontal. Returns the path of the written file and the number of rows
    plotted.
    """
    if chart_type not in _CHART_TYPES:
        raise ValueError(f"chart_type must be one of {', '.join(_CHART_TYPES)}")
    name = _validate_filename(filename)

    validate_read_only(sql)
    df = con.execute(sql).fetch_df()
    if df.empty:
        raise ValueError("query returned no rows; nothing to chart")

    y_columns = [y] if isinstance(y, str) else list(y)
    if not y_columns:
        raise ValueError("y must name at least one result column")
    for column in [x, *y_columns]:
        if column not in df.columns:
            raise ValueError(
                f"column {column!r} is not in the query result; "
                f"available columns: {', '.join(df.columns)}"
            )

    charts_dir.mkdir(parents=True, exist_ok=True)
    path = charts_dir / name

    fig, ax = plt.subplots(figsize=(8, 5))
    try:
        if chart_type == "scatter":
            for column in y_columns:
                ax.scatter(df[x], df[column], label=column)
            if len(y_columns) > 1:
                ax.legend()
        else:
            df.plot(kind=chart_type, x=x, y=y_columns, ax=ax)
        legend = ax.get_legend()
        if legend is not None and len(y_columns) == 1:
            legend.remove()
        tick_labels = [tick.get_text() for tick in ax.get_xticklabels()]
        if tick_labels and max(len(label) for label in tick_labels) <= 12:
            ax.tick_params(axis="x", rotation=0)
        ax.set_title(title)
        ax.set_xlabel(x)
        fig.tight_layout()
        fig.savefig(path)
    finally:
        plt.close(fig)

    return {"path": str(path), "rows_plotted": int(len(df))}
