"""The CLI as a thin layer over the host, tested with a fake ask()."""

from collections.abc import Callable
from typing import Any

import pytest

from insight_agent.cli import main
from insight_agent.host import AgentEvent, AgentResult
from insight_agent.llm import MissingAPIKeyError


def _fake_ask(result: AgentResult) -> Callable[..., Any]:
    async def fake(
        question: str,
        settings: Any = None,
        llm: Any = None,
        on_event: Callable[[AgentEvent], None] | None = None,
    ) -> AgentResult:
        if on_event is not None:
            on_event(
                AgentEvent(
                    kind="plan",
                    text="Checking the schema first.",
                    detail={"goal": "Checking the schema first."},
                )
            )
            on_event(
                AgentEvent(
                    kind="tool_call",
                    text='tools/call run_sql {"sql": "SELECT 1"}',
                    detail={"tool": "run_sql", "arguments": {"sql": "SELECT 1"}},
                )
            )
            on_event(
                AgentEvent(
                    kind="tool_result",
                    text="1 row(s)",
                    detail={"tool": "run_sql", "summary": "1 row(s)", "row_count": 1},
                )
            )
        return result

    return fake


def test_prints_answer_and_charts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = AgentResult(answer="The West region dropped most.", charts=["charts/west.png"])
    monkeypatch.setattr("insight_agent.cli.ask", _fake_ask(result))
    monkeypatch.setattr("sys.argv", ["insight-agent", "which region dropped most in Q3"])
    main()
    captured = capsys.readouterr()
    assert "The West region dropped most." in captured.out
    assert "charts: charts/west.png" in captured.out
    assert "goal: Checking the schema first." in captured.err
    assert "tools/call run_sql" in captured.err
    assert "-> 1 row(s)" in captured.err


def test_no_charts_line_when_no_charts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = AgentResult(answer="42")
    monkeypatch.setattr("insight_agent.cli.ask", _fake_ask(result))
    monkeypatch.setattr("sys.argv", ["insight-agent", "how many"])
    main()
    assert "charts:" not in capsys.readouterr().out


def test_quiet_suppresses_progress(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = AgentResult(answer="ok")
    monkeypatch.setattr("insight_agent.cli.ask", _fake_ask(result))
    monkeypatch.setattr("sys.argv", ["insight-agent", "--quiet", "q"])
    main()
    assert capsys.readouterr().err == ""


def test_missing_api_key_exits_with_code_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake(question: str, **kwargs: Any) -> AgentResult:
        raise MissingAPIKeyError("no API key configured")

    monkeypatch.setattr("insight_agent.cli.ask", fake)
    monkeypatch.setattr("sys.argv", ["insight-agent", "q"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2
    assert "DEEPSEEK_API_KEY" in capsys.readouterr().err
