"""MCP server exposing the data-analysis tools over the Model Context Protocol.

This package is a standalone protocol server: it owns the DuckDB connection
and exposes describe_schema, run_sql, and create_chart to any MCP client over
stdio. It contains no LLM calls.
"""

from insight_agent.mcp_server.server import build_server

__all__ = ["build_server"]
