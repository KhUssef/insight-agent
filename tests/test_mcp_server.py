"""The MCP server, exercised over the real protocol with an in-memory transport.

No LLM and no API key are involved: a ClientSession is connected to the built
server through in-process memory streams, and every assertion goes through
tools/list and tools/call exactly as an external MCP client would.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import TextContent

from insight_agent.config import Settings
from insight_agent.mcp_server.server import build_server


@pytest.fixture()
def settings(data_dir: Path, tmp_path: Path) -> Settings:
    return Settings(data_dir=data_dir, charts_dir=tmp_path / "charts")


@asynccontextmanager
async def connected(settings: Settings) -> AsyncIterator[ClientSession]:
    """Connect an MCP ClientSession to a freshly built server in-process.

    The connection is entered and exited inside the test's own task; anyio
    cancel scopes must open and close in the same task.
    """
    server = build_server(settings)
    async with create_connected_server_and_client_session(server) as client_session:
        yield client_session


def _payload(result: Any) -> dict[str, Any]:
    """Decode the JSON text content of a tools/call result."""
    content = result.content[0]
    assert isinstance(content, TextContent)
    decoded = json.loads(content.text)
    assert isinstance(decoded, dict)
    return decoded


async def test_tools_list_exposes_exactly_three_tools(settings: Settings) -> None:
    async with connected(settings) as session:
        listed = await session.list_tools()
    names = sorted(tool.name for tool in listed.tools)
    assert names == ["create_chart", "describe_schema", "run_sql"]


async def test_describe_schema_over_protocol(settings: Settings) -> None:
    async with connected(settings) as session:
        result = await session.call_tool("describe_schema", {})
    payload = _payload(result)
    tables = {table["table"] for table in payload["tables"]}
    assert "sample_sales" in tables


async def test_run_sql_select_over_protocol(settings: Settings) -> None:
    async with connected(settings) as session:
        result = await session.call_tool(
            "run_sql",
            {"sql": "SELECT region, sum(revenue) AS total FROM sample_sales GROUP BY region"},
        )
    payload = _payload(result)
    assert payload["row_count"] == 4
    assert payload["columns"] == ["region", "total"]


async def test_run_sql_rejects_writes_over_protocol(settings: Settings) -> None:
    async with connected(settings) as session:
        result = await session.call_tool("run_sql", {"sql": "DELETE FROM sample_sales"})
        payload = _payload(result)
        assert "error" in payload
        check = await session.call_tool(
            "run_sql", {"sql": "SELECT count(*) AS n FROM sample_sales"}
        )
        assert _payload(check)["rows"][0][0] > 0


async def test_create_chart_over_protocol(settings: Settings) -> None:
    async with connected(settings) as session:
        result = await session.call_tool(
            "create_chart",
            {
                "sql": (
                    "SELECT region, sum(revenue) AS total FROM sample_sales "
                    "GROUP BY region ORDER BY region"
                ),
                "chart_type": "bar",
                "x": "region",
                "y": "total",
                "title": "Revenue by region",
                "filename": "protocol_chart.png",
            },
        )
    payload = _payload(result)
    path = Path(payload["path"])
    assert path.exists()
    assert path.parent == settings.charts_dir


async def test_create_chart_bad_filename_returns_error(settings: Settings) -> None:
    async with connected(settings) as session:
        result = await session.call_tool(
            "create_chart",
            {
                "sql": "SELECT region, sum(revenue) AS total FROM sample_sales GROUP BY region",
                "chart_type": "bar",
                "x": "region",
                "y": "total",
                "title": "t",
                "filename": "../escape.png",
            },
        )
    assert "error" in _payload(result)
