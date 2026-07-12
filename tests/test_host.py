"""The agent host loop, tested with a scripted fake LLM and a real MCP server.

Only the LLM is faked: the host spawns the actual MCP server subprocess over
stdio, so these tests exercise tool discovery, schema conversion, dispatch,
and result handling end to end without a network or an API key. Host settings
reach the server subprocess through environment variables (DATA_DIR,
CHARTS_DIR), which is also asserted here via the chart test writing into a
pytest tmp_path.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from insight_agent.config import Settings
from insight_agent.host import AgentEvent, ask


class FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.type = "function"
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content: str | None = None, tool_calls: list[FakeToolCall] | None = None):
        self.content = content
        self.tool_calls = tool_calls


class ScriptedLLM:
    """Replays a fixed script of messages; repeats the last one if exhausted.

    When per-call usage dicts are given, each chat call exposes the matching
    one through last_usage, the way LLMClient reports provider token usage.
    """

    model = "scripted"

    def __init__(
        self, script: list[FakeMessage], usages: list[dict[str, int]] | None = None
    ) -> None:
        self.script = script
        self.usages = usages
        self.calls: list[dict[str, Any]] = []
        self.last_usage: dict[str, int] | None = None

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> FakeMessage:
        self.calls.append({"messages": list(messages), "tools": tools})
        index = min(len(self.calls) - 1, len(self.script) - 1)
        if self.usages is not None:
            self.last_usage = self.usages[min(len(self.calls) - 1, len(self.usages) - 1)]
        return self.script[index]


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        charts_dir=tmp_path / "charts",
        deepseek_api_key="",
        **overrides,
    )


async def test_full_loop_answers_with_real_tool_results(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    llm = ScriptedLLM(
        [
            FakeMessage(tool_calls=[FakeToolCall("c1", "describe_schema", "{}")]),
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        "c2",
                        "run_sql",
                        json.dumps(
                            {
                                "sql": (
                                    "SELECT region, sum(revenue) AS total "
                                    "FROM sample_sales GROUP BY region"
                                )
                            }
                        ),
                    )
                ]
            ),
            FakeMessage(content="The West region dropped the most."),
        ]
    )
    result = await ask("which region dropped most in Q3", settings=settings, llm=llm)

    assert result.answer == "The West region dropped the most."
    assert [call["tool"] for call in result.tool_calls] == ["describe_schema", "run_sql"]
    assert result.charts == []

    final_messages = llm.calls[-1]["messages"]
    tool_messages = [m for m in final_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 2
    schema_payload = json.loads(tool_messages[0]["content"])
    table_names = {table["table"] for table in schema_payload["tables"]}
    assert table_names == {
        "sample_sales",
        "region_targets",
        "products",
        "customers",
        "marketing_spend",
        "returns",
    }
    sql_payload = json.loads(tool_messages[1]["content"])
    assert sql_payload["columns"] == ["region", "total"]
    assert sql_payload["row_count"] == 4

    tools = llm.calls[0]["tools"]
    assert tools is not None
    assert sorted(t["function"]["name"] for t in tools) == [
        "create_chart",
        "describe_schema",
        "run_sql",
    ]


async def test_chart_paths_are_collected(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    llm = ScriptedLLM(
        [
            FakeMessage(
                tool_calls=[
                    FakeToolCall(
                        "c1",
                        "create_chart",
                        json.dumps(
                            {
                                "sql": (
                                    "SELECT region, sum(revenue) AS total "
                                    "FROM sample_sales GROUP BY region ORDER BY region"
                                ),
                                "chart_type": "bar",
                                "x": "region",
                                "y": "total",
                                "title": "Revenue by region",
                                "filename": "by_region.png",
                            }
                        ),
                    )
                ]
            ),
            FakeMessage(content="See the chart."),
        ]
    )
    events: list[AgentEvent] = []
    result = await ask(
        "chart revenue by region", settings=settings, llm=llm, on_event=events.append
    )

    assert len(result.charts) == 1
    chart_path = Path(result.charts[0])
    assert chart_path.exists()
    assert chart_path.parent == settings.charts_dir

    tool_result_events = [event for event in events if event.kind == "tool_result"]
    assert tool_result_events[0].detail["path"] == result.charts[0]


async def test_malformed_arguments_feed_error_back(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    llm = ScriptedLLM(
        [
            FakeMessage(tool_calls=[FakeToolCall("c1", "run_sql", '{"sql": ')]),
            FakeMessage(content="Recovered."),
        ]
    )
    events: list[AgentEvent] = []
    result = await ask("broken call", settings=settings, llm=llm, on_event=events.append)

    assert result.answer == "Recovered."
    assert result.tool_calls == []
    tool_messages = [m for m in llm.calls[-1]["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert "error" in json.loads(tool_messages[0]["content"])

    tool_result_events = [event for event in events if event.kind == "tool_result"]
    assert len(tool_result_events) == 1
    assert "error" in tool_result_events[0].detail


async def test_round_cap_terminates_the_loop(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_tool_rounds=2)
    llm = ScriptedLLM([FakeMessage(tool_calls=[FakeToolCall("c1", "describe_schema", "{}")])])
    result = await ask("never finishes", settings=settings, llm=llm)

    assert len(llm.calls) == 2
    assert "round limit" in result.answer.lower()
    assert len(result.tool_calls) == 2
    assert result.events[-1].kind == "answer"


async def test_events_capture_plan_tool_call_and_result(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    llm = ScriptedLLM(
        [
            FakeMessage(
                content="Checking the schema first.",
                tool_calls=[FakeToolCall("c1", "describe_schema", "{}")],
            ),
            FakeMessage(
                content="Now summing revenue by region.",
                tool_calls=[
                    FakeToolCall(
                        "c2",
                        "run_sql",
                        json.dumps(
                            {
                                "sql": (
                                    "SELECT region, sum(revenue) AS total "
                                    "FROM sample_sales GROUP BY region"
                                )
                            }
                        ),
                    )
                ],
            ),
            FakeMessage(content="The West region dropped the most."),
        ]
    )
    events: list[AgentEvent] = []
    result = await ask(
        "which region dropped most in Q3", settings=settings, llm=llm, on_event=events.append
    )

    kinds = [event.kind for event in events]
    assert kinds == [
        "usage",
        "plan",
        "tool_call",
        "tool_result",
        "usage",
        "plan",
        "tool_call",
        "tool_result",
        "usage",
        "answer",
    ]

    usage_events = [event for event in events if event.kind == "usage"]
    assert [event.detail["round"] for event in usage_events] == [1, 2, 3]
    assert all(event.detail["max_rounds"] == 12 for event in usage_events)
    assert all(event.detail["model"] == "scripted" for event in usage_events)

    plan_events = [event for event in events if event.kind == "plan"]
    assert plan_events[0].detail == {"goal": "Checking the schema first."}
    assert plan_events[1].detail == {"goal": "Now summing revenue by region."}

    schema_result, sql_result = (event for event in events if event.kind == "tool_result")
    assert schema_result.detail["tool"] == "describe_schema"
    assert sql_result.detail["tool"] == "run_sql"
    assert sql_result.detail["row_count"] == 4
    assert "truncated" not in sql_result.detail

    answer_event = events[-1]
    assert answer_event.kind == "answer"
    assert answer_event.detail["answer"] == "The West region dropped the most."
    assert answer_event.detail["charts"] == []
    assert answer_event.detail["usage"] == result.usage
    assert result.usage["rounds"] == 3
    assert result.usage["tool_calls"] == 2
    assert result.usage["model"] == "scripted"
    assert result.usage["duration_seconds"] >= 0
    assert "total_tokens" not in result.usage

    assert result.events == events


async def test_token_usage_accumulates_across_rounds(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    llm = ScriptedLLM(
        [
            FakeMessage(tool_calls=[FakeToolCall("c1", "describe_schema", "{}")]),
            FakeMessage(content="Done."),
        ],
        usages=[
            {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            {"prompt_tokens": 300, "completion_tokens": 30, "total_tokens": 330},
        ],
    )
    events: list[AgentEvent] = []
    result = await ask("count tokens", settings=settings, llm=llm, on_event=events.append)

    usage_events = [event for event in events if event.kind == "usage"]
    assert usage_events[0].detail["total_tokens"] == 120
    assert usage_events[1].detail["total_tokens"] == 450
    assert result.usage["prompt_tokens"] == 400
    assert result.usage["completion_tokens"] == 50
    assert result.usage["total_tokens"] == 450
    assert result.usage["rounds"] == 2


async def test_folder_scoped_run_sees_only_that_folders_tables(tmp_path: Path) -> None:
    """A run scoped to a user folder reaches only that folder's tables.

    The data directory travels to the MCP server subprocess through the
    environment, so pointing the run's settings at a folder confines
    describe_schema to its contents; with sample generation disabled the
    folder is also never populated with generated files.
    """
    folder = tmp_path / "user_folder"
    folder.mkdir()
    (folder / "inventory.csv").write_text(
        "item,stock\nwidget,4\ngadget,9\n", encoding="utf-8"
    )
    settings = make_settings(tmp_path).model_copy(
        update={"data_dir": folder, "generate_sample_data": False}
    )
    llm = ScriptedLLM(
        [
            FakeMessage(tool_calls=[FakeToolCall("c1", "describe_schema", "{}")]),
            FakeMessage(content="Only inventory here."),
        ]
    )
    result = await ask("what tables exist", settings=settings, llm=llm)

    assert result.answer == "Only inventory here."
    tool_messages = [m for m in llm.calls[-1]["messages"] if m.get("role") == "tool"]
    schema_payload = json.loads(tool_messages[0]["content"])
    assert {table["table"] for table in schema_payload["tables"]} == {"inventory"}
    assert {path.name for path in folder.iterdir()} == {"inventory.csv"}


def test_missing_key_raises_before_any_subprocess(tmp_path: Path) -> None:
    from insight_agent.llm import MissingAPIKeyError

    settings = make_settings(tmp_path)
    with pytest.raises(MissingAPIKeyError):
        import anyio

        anyio.run(lambda: ask("q", settings=settings))
