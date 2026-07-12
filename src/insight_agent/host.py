"""The agent host: an MCP client driving the plan-call-observe loop.

The host is the only component that talks to the LLM, and it reaches the data
tools exclusively over the Model Context Protocol: it spawns the MCP server as
a subprocess on stdio, discovers its tools with tools/list, converts them to
the model's function-calling schema, and dispatches every tool call the model
makes back through tools/call. It never imports a tool function and never
touches the database.

The loop is written from scratch against the raw tool-calling API; there is no
agent framework anywhere in it.
"""

import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from insight_agent.config import Settings, get_settings
from insight_agent.llm import LLMClient
from insight_agent.prompts import SYSTEM_PROMPT

_ROUND_LIMIT_NOTICE = (
    "Reached the tool round limit before finishing. Partial findings follow."
)


@dataclass(frozen=True)
class AgentEvent:
    """One structured step of the agent's progress, for an interface to render.

    kind is one of "usage" (one LLM round completed, with effort and token
    accounting), "plan" (a goal the model stated in the message content
    accompanying a round of tool calls), "tool_call" (a dispatch about to
    happen), "tool_result" (that dispatch's outcome), or "answer" (the final
    response). text is a human-readable one-liner for the given kind; detail
    is a JSON-friendly payload carrying the same information structured for
    programmatic use:

    - usage: {"round": the 1-based round just completed, "max_rounds": the
      configured cap, "model": the model name}, plus "prompt_tokens",
      "completion_tokens", and "total_tokens" (cumulative across the run)
      when the provider reports token usage
    - plan: {"goal": the stated goal}
    - tool_call: {"tool": name, "arguments": the parsed call arguments}
    - tool_result: {"tool": name, "summary": the one-line summary}, plus
      "row_count" and "truncated" when the result is a parseable run_sql
      payload, "path" when it is a create_chart payload, or "error" when the
      tool returned an error payload
    - answer: {"answer": the final answer text, "charts": the chart paths
      collected during the run, "usage": the same run summary stored in
      AgentResult.usage}
    """

    kind: Literal["usage", "plan", "tool_call", "tool_result", "answer"]
    text: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResult:
    """The outcome of one agent run: the answer plus its supporting trail.

    usage summarizes the effort the run took: "rounds" (LLM rounds used),
    "max_rounds" (the configured cap), "tool_calls" (dispatches made),
    "duration_seconds", and "model", plus "prompt_tokens",
    "completion_tokens", and "total_tokens" when the provider reports them.
    """

    answer: str
    charts: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    events: list[AgentEvent] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


def _server_parameters(settings: Settings) -> StdioServerParameters:
    """Build the stdio launch spec for the MCP server subprocess.

    The host's data-related settings are propagated to the server through
    environment variables so both processes agree on the data directory and
    chart directory.
    """
    env = {
        **os.environ,
        "DATA_DIR": str(settings.data_dir),
        "CHARTS_DIR": str(settings.charts_dir),
        "GENERATE_SAMPLE_DATA": "1" if settings.generate_sample_data else "0",
    }
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "insight_agent.mcp_server"],
        env=env,
    )


def _to_function_schema(
    name: str, description: str, input_schema: dict[str, Any]
) -> dict[str, Any]:
    """Convert one MCP tool listing into the OpenAI function-calling schema."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": input_schema,
        },
    }


def _result_text(result: Any) -> str:
    """Extract the text payload from an MCP tools/call result."""
    parts = [
        item.text
        for item in getattr(result, "content", [])
        if getattr(item, "text", None) is not None
    ]
    return "\n".join(parts)


def _assistant_message(message: Any) -> dict[str, Any]:
    """Render a model response message as an OpenAI-format assistant dict."""
    return {
        "role": "assistant",
        "content": message.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in message.tool_calls
        ],
    }


def _compact(arguments: dict[str, Any], limit: int = 120) -> str:
    """One-line rendering of tool arguments for progress events."""
    text = json.dumps(arguments)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _tool_result_event(name: str, text: str) -> AgentEvent:
    """Build the tool_result event for one dispatched call's raw JSON text.

    Parses the tool's JSON text payload to produce a compact summary: row
    count and truncation for run_sql, the written file path for create_chart,
    the table list for describe_schema, or the error message when the tool
    returned an error payload. A payload that does not match any of these
    shapes falls back to a truncated text preview.
    """
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        payload = None

    detail: dict[str, Any] = {"tool": name}

    if isinstance(payload, dict) and "error" in payload:
        detail["error"] = payload["error"]
        summary = f"error: {payload['error']}"
    elif name == "run_sql" and isinstance(payload, dict) and "row_count" in payload:
        detail["row_count"] = payload["row_count"]
        summary = f"{payload['row_count']} row(s)"
        if payload.get("truncated"):
            detail["truncated"] = True
            summary += " (truncated)"
    elif name == "create_chart" and isinstance(payload, dict) and "path" in payload:
        detail["path"] = payload["path"]
        summary = f"chart written to {payload['path']}"
    elif isinstance(payload, dict) and "tables" in payload:
        table_names = ", ".join(table["table"] for table in payload["tables"])
        summary = f"tables: {table_names}" if table_names else "no tables found"
    else:
        summary = text if len(text) <= 120 else text[:117] + "..."

    detail["summary"] = summary
    return AgentEvent(kind="tool_result", text=summary, detail=detail)


async def _run_loop(
    session: ClientSession,
    question: str,
    settings: Settings,
    llm: LLMClient,
    tool_schemas: list[dict[str, Any]],
    on_event: Callable[[AgentEvent], None] | None,
) -> AgentResult:
    """Drive the plan-call-observe loop until a final answer or the round cap."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    charts: list[str] = []
    calls_made: list[dict[str, Any]] = []
    events: list[AgentEvent] = []
    last_content = ""
    model_name = str(getattr(llm, "model", ""))
    started = time.monotonic()
    rounds_used = 0
    tokens: dict[str, int] = {}

    def emit(event: AgentEvent) -> None:
        events.append(event)
        if on_event is not None:
            on_event(event)

    def emit_usage() -> None:
        detail: dict[str, Any] = {
            "round": rounds_used,
            "max_rounds": settings.max_tool_rounds,
            "model": model_name,
        }
        text = f"round {rounds_used}/{settings.max_tool_rounds}"
        if tokens:
            detail.update(tokens)
            text += f", {tokens['total_tokens']} tokens"
        emit(AgentEvent(kind="usage", text=text, detail=detail))

    def finish(answer: str) -> AgentResult:
        usage: dict[str, Any] = {
            "rounds": rounds_used,
            "max_rounds": settings.max_tool_rounds,
            "tool_calls": len(calls_made),
            "duration_seconds": round(time.monotonic() - started, 3),
            "model": model_name,
            **tokens,
        }
        emit(
            AgentEvent(
                kind="answer",
                text=answer,
                detail={"answer": answer, "charts": charts, "usage": usage},
            )
        )
        return AgentResult(
            answer=answer, charts=charts, tool_calls=calls_made, events=events, usage=usage
        )

    for _ in range(settings.max_tool_rounds):
        message = await anyio.to_thread.run_sync(partial(llm.chat, messages, tool_schemas))
        rounds_used += 1
        round_usage = getattr(llm, "last_usage", None)
        if round_usage:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                tokens[key] = tokens.get(key, 0) + int(round_usage.get(key, 0))
        emit_usage()
        if not getattr(message, "tool_calls", None):
            return finish(message.content or "")

        content = (message.content or "").strip()
        if content:
            emit(AgentEvent(kind="plan", text=content, detail={"goal": content}))
        last_content = message.content or last_content
        messages.append(_assistant_message(message))
        for call in message.tool_calls:
            name = call.function.name
            try:
                arguments: dict[str, Any] = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                emit(
                    AgentEvent(
                        kind="tool_call",
                        text=f"tools/call {name} <malformed arguments>",
                        detail={"tool": name, "arguments": {}},
                    )
                )
                error_text = json.dumps({"error": f"invalid tool arguments JSON: {exc}"})
                emit(_tool_result_event(name, error_text))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": error_text,
                    }
                )
                continue

            emit(
                AgentEvent(
                    kind="tool_call",
                    text=f"tools/call {name} {_compact(arguments)}",
                    detail={"tool": name, "arguments": arguments},
                )
            )
            calls_made.append({"tool": name, "arguments": arguments})
            result = await session.call_tool(name, arguments)
            text = _result_text(result)
            emit(_tool_result_event(name, text))
            if name == "create_chart":
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    payload = {}
                if isinstance(payload, dict) and "path" in payload:
                    charts.append(str(payload["path"]))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": text})

    answer = _ROUND_LIMIT_NOTICE if not last_content else f"{_ROUND_LIMIT_NOTICE}\n{last_content}"
    return finish(answer)


async def ask(
    question: str,
    settings: Settings | None = None,
    llm: LLMClient | None = None,
    on_event: Callable[[AgentEvent], None] | None = None,
) -> AgentResult:
    """Answer a natural-language question about the dataset.

    Spawns the MCP server, discovers its tools over the protocol, and runs the
    tool-use loop against the configured LLM. on_event, when given, receives
    one AgentEvent per step - a completed LLM round with its token usage, a
    stated goal, a tool dispatch, a tool result, or the final answer - so an
    interface can show live progress; every emitted event is also collected in
    the returned AgentResult.events regardless of whether on_event is given.
    """
    if settings is None:
        settings = get_settings()
    if llm is None:
        llm = LLMClient(settings)

    async with stdio_client(_server_parameters(settings)) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            tool_schemas = [
                _to_function_schema(tool.name, tool.description or "", tool.inputSchema)
                for tool in listed.tools
            ]
            return await _run_loop(session, question, settings, llm, tool_schemas, on_event)
