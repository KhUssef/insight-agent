"""FastMCP server wrapping the three data-analysis tools.

The server is deterministic: every tool call maps to a pure data operation in
insight_agent.tools. Results are returned as strict JSON strings. Validation
failures (unsafe SQL, bad chart arguments) are returned as JSON error payloads
so that a calling model can read the reason and self-correct instead of
receiving a protocol-level failure.
"""

import json

import duckdb
from mcp.server.fastmcp import FastMCP

from insight_agent import tools
from insight_agent.config import Settings, get_settings
from insight_agent.data import connect
from insight_agent.sql_guard import SQLValidationError

SERVER_NAME = "insight"


def build_server(settings: Settings | None = None) -> FastMCP:
    """Create the MCP server with the dataset loaded and all tools registered.

    The DuckDB connection is created here and captured by the tool closures;
    it never leaves the server process.
    """
    if settings is None:
        settings = get_settings()
    con = connect(settings.data_dir, ensure_samples=settings.generate_sample_data)
    server = FastMCP(SERVER_NAME, log_level="WARNING")

    @server.tool()
    def describe_schema() -> str:
        """Describe the loaded dataset: tables, columns with types, row counts,
        distinct values for low-cardinality text columns, and min/max bounds
        for numeric and date columns. Call this before writing any SQL."""
        return json.dumps(tools.describe_schema(con))

    @server.tool()
    def run_sql(sql: str) -> str:
        """Execute a single read-only SQL SELECT (or WITH ... SELECT) against
        the dataset and return the result as JSON with columns, rows, row_count,
        and a truncated flag. Any write, DDL, or database-management statement
        is rejected."""
        try:
            return json.dumps(tools.run_sql(con, sql, max_rows=settings.max_result_rows))
        except (SQLValidationError, duckdb.Error) as exc:
            return json.dumps({"error": str(exc)})

    @server.tool()
    def create_chart(
        sql: str,
        chart_type: str,
        x: str,
        y: str | list[str],
        title: str,
        filename: str,
    ) -> str:
        """Run a read-only SQL query and render its result as a PNG chart.
        chart_type is one of bar, line, or scatter. x names the column for the
        horizontal axis and y names one or more result columns to plot.
        filename is a plain basename (no directories); the .png suffix is added
        if missing. Returns JSON with the written file path and rows plotted."""
        try:
            return json.dumps(
                tools.create_chart(
                    con,
                    sql=sql,
                    chart_type=chart_type,
                    x=x,
                    y=y,
                    title=title,
                    filename=filename,
                    charts_dir=settings.charts_dir,
                )
            )
        except (SQLValidationError, ValueError, duckdb.Error) as exc:
            return json.dumps({"error": str(exc)})

    return server


def main() -> None:
    """Run the server on stdio transport."""
    build_server().run(transport="stdio")
