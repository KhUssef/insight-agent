"""The HTTP API as a thin layer over the host, tested with a fake ask()."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from insight_agent.api import app, create_app
from insight_agent.config import Settings
from insight_agent.host import AgentEvent, AgentResult
from insight_agent.llm import MissingAPIKeyError


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse raw Server-Sent Events text into a list of (event, payload) pairs."""
    events: list[tuple[str, dict[str, Any]]] = []
    event_name: str | None = None
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
        elif line == "" and event_name is not None:
            payload = json.loads("".join(data_lines)) if data_lines else {}
            events.append((event_name, payload))
            event_name = None
            data_lines = []
    return events


_CANNED_EVENTS = [
    AgentEvent(kind="plan", text="Find the biggest drop", detail={"goal": "Find the biggest drop"}),
    AgentEvent(
        kind="tool_call",
        text="tools/call run_sql {'sql': 'SELECT 1'}",
        detail={"tool": "run_sql", "arguments": {"sql": "SELECT 1"}},
    ),
    AgentEvent(
        kind="tool_result",
        text="1 row(s)",
        detail={"tool": "run_sql", "summary": "1 row(s)", "row_count": 1},
    ),
    AgentEvent(
        kind="answer",
        text="West.",
        detail={"answer": "West.", "charts": ["charts/west.png"]},
    ),
]


def test_ask_returns_answer_and_charts(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    async def fake(question: str, **kwargs: Any) -> AgentResult:
        assert question == "which region dropped most in Q3"
        return AgentResult(answer="West.", charts=["charts/west.png"])

    monkeypatch.setattr("insight_agent.api.ask", fake)
    response = client.post("/ask", json={"question": "which region dropped most in Q3"})
    assert response.status_code == 200
    assert response.json() == {"answer": "West.", "charts": ["charts/west.png"], "usage": {}}


@pytest.mark.parametrize("payload", [{}, {"question": ""}, {"question": "   "}])
def test_ask_rejects_missing_or_blank_question(
    client: TestClient, payload: dict[str, Any]
) -> None:
    response = client.post("/ask", json=payload)
    assert response.status_code == 422


def test_missing_api_key_maps_to_503(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    async def fake(question: str, **kwargs: Any) -> AgentResult:
        raise MissingAPIKeyError(
            "no API key configured: set DEEPSEEK_API_KEY in the environment or .env"
        )

    monkeypatch.setattr("insight_agent.api.ask", fake)
    response = client.post("/ask", json={"question": "q"})
    assert response.status_code == 503
    assert "DEEPSEEK_API_KEY" in response.json()["detail"]


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_serves_frontend(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Insight Agent" in response.text


def test_ask_stream_emits_events_in_order(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    async def fake(question: str, **kwargs: Any) -> AgentResult:
        on_event = kwargs.get("on_event")
        for event in _CANNED_EVENTS:
            if on_event is not None:
                on_event(event)
        return AgentResult(
            answer="West.",
            charts=["charts/west.png"],
            tool_calls=[{"tool": "run_sql", "arguments": {"sql": "SELECT 1"}}],
            events=_CANNED_EVENTS,
        )

    monkeypatch.setattr("insight_agent.api.ask", fake)
    with client.stream(
        "GET", "/ask/stream", params={"question": "which region dropped most in Q3"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        text = "".join(response.iter_text())

    events = _parse_sse(text)
    assert [kind for kind, _ in events] == ["plan", "tool_call", "tool_result", "answer"]
    assert events[0][1]["detail"]["goal"] == "Find the biggest drop"
    assert events[1][1]["detail"]["tool"] == "run_sql"
    assert events[2][1]["detail"]["row_count"] == 1
    assert events[3][1]["detail"]["answer"] == "West."
    assert events[3][1]["detail"]["charts"] == ["charts/west.png"]


def test_ask_stream_rejects_blank_question(client: TestClient) -> None:
    response = client.get("/ask/stream", params={"question": "   "})
    assert response.status_code == 422


def test_ask_stream_emits_error_event_for_missing_key(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    async def fake(question: str, **kwargs: Any) -> AgentResult:
        raise MissingAPIKeyError(
            "no API key configured: set DEEPSEEK_API_KEY in the environment or .env"
        )

    monkeypatch.setattr("insight_agent.api.ask", fake)
    with client.stream("GET", "/ask/stream", params={"question": "q"}) as response:
        assert response.status_code == 200
        text = "".join(response.iter_text())

    events = _parse_sse(text)
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "error"
    assert "DEEPSEEK_API_KEY" in payload["text"]


def _isolated_settings(tmp_path: Any, **overrides: Any) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        charts_dir=tmp_path / "charts",
        _env_file=None,
        **overrides,
    )


def test_meta_reports_models_and_limits(tmp_path: Any) -> None:
    settings = _isolated_settings(
        tmp_path,
        deepseek_api_key="",
        llm_model="deepseek-chat",
        llm_models="deepseek-chat,deepseek-reasoner",
        max_tool_rounds=7,
    )
    isolated_client = TestClient(create_app(settings), raise_server_exceptions=False)

    response = isolated_client.get("/meta")

    assert response.status_code == 200
    payload = response.json()
    assert payload["model"] == "deepseek-chat"
    assert payload["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert payload["max_tool_rounds"] == 7
    assert payload["api_key_configured"] is False
    assert payload["base_url"]


def test_dataset_describes_loaded_tables(tmp_path: Any) -> None:
    settings = _isolated_settings(tmp_path)
    isolated_client = TestClient(create_app(settings), raise_server_exceptions=False)

    response = isolated_client.get("/dataset")

    assert response.status_code == 200
    tables = {table["table"]: table for table in response.json()["tables"]}
    assert set(tables) == {
        "sample_sales",
        "region_targets",
        "products",
        "customers",
        "marketing_spend",
        "returns",
    }
    assert tables["sample_sales"]["row_count"] > 0
    column_names = [column["name"] for column in tables["sample_sales"]["columns"]]
    assert "region" in column_names


def test_stats_accumulate_across_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    async def fake(question: str, **kwargs: Any) -> AgentResult:
        return AgentResult(
            answer="West.",
            charts=["charts/west.png"],
            usage={
                "rounds": 3,
                "max_rounds": 12,
                "tool_calls": 2,
                "duration_seconds": 1.5,
                "model": "deepseek-chat",
                "prompt_tokens": 400,
                "completion_tokens": 50,
                "total_tokens": 450,
            },
        )

    monkeypatch.setattr("insight_agent.api.ask", fake)
    isolated_client = TestClient(
        create_app(_isolated_settings(tmp_path)), raise_server_exceptions=False
    )

    assert isolated_client.get("/stats").json()["questions"] == 0
    for _ in range(2):
        assert isolated_client.post("/ask", json={"question": "q"}).status_code == 200

    payload = isolated_client.get("/stats").json()
    assert payload["questions"] == 2
    assert payload["answers"] == 2
    assert payload["errors"] == 0
    assert payload["rounds"] == 6
    assert payload["tool_calls"] == 4
    assert payload["charts"] == 2
    assert payload["total_tokens"] == 900
    assert payload["duration_seconds"] == 3.0


def test_stats_count_failed_runs_as_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    async def fake(question: str, **kwargs: Any) -> AgentResult:
        raise MissingAPIKeyError("no API key configured")

    monkeypatch.setattr("insight_agent.api.ask", fake)
    isolated_client = TestClient(
        create_app(_isolated_settings(tmp_path)), raise_server_exceptions=False
    )

    assert isolated_client.post("/ask", json={"question": "q"}).status_code == 503
    payload = isolated_client.get("/stats").json()
    assert payload["questions"] == 1
    assert payload["errors"] == 1
    assert payload["answers"] == 0


def test_ask_accepts_configured_model_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    seen: dict[str, Any] = {}

    async def fake(question: str, **kwargs: Any) -> AgentResult:
        seen["model"] = kwargs["settings"].llm_model
        return AgentResult(answer="West.")

    monkeypatch.setattr("insight_agent.api.ask", fake)
    settings = _isolated_settings(
        tmp_path, llm_model="deepseek-chat", llm_models="deepseek-chat,deepseek-reasoner"
    )
    isolated_client = TestClient(create_app(settings), raise_server_exceptions=False)

    response = isolated_client.post(
        "/ask", json={"question": "q", "model": "deepseek-reasoner"}
    )

    assert response.status_code == 200
    assert seen["model"] == "deepseek-reasoner"


def test_ask_rejects_unknown_model(tmp_path: Any) -> None:
    settings = _isolated_settings(
        tmp_path, llm_model="deepseek-chat", llm_models="deepseek-chat,deepseek-reasoner"
    )
    isolated_client = TestClient(create_app(settings), raise_server_exceptions=False)

    response = isolated_client.post("/ask", json={"question": "q", "model": "gpt-4o"})
    assert response.status_code == 422
    assert "deepseek-reasoner" in response.json()["detail"]

    response = isolated_client.get(
        "/ask/stream", params={"question": "q", "model": "gpt-4o"}
    )
    assert response.status_code == 422


def test_charts_are_served_as_static_files(tmp_path: Any) -> None:
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    (charts_dir / "demo.png").write_bytes(b"fake-png-bytes")
    settings = Settings(charts_dir=charts_dir, data_dir=tmp_path / "data")

    isolated_client = TestClient(create_app(settings), raise_server_exceptions=False)
    response = isolated_client.get("/charts/demo.png")

    assert response.status_code == 200
    assert response.content == b"fake-png-bytes"


def test_charts_mount_creates_missing_directory(tmp_path: Any) -> None:
    charts_dir = tmp_path / "does-not-exist-yet"
    settings = Settings(charts_dir=charts_dir, data_dir=tmp_path / "data")

    create_app(settings)

    assert charts_dir.is_dir()


def test_dataset_scoped_to_folder_shows_only_its_tables(tmp_path: Any) -> None:
    folder = tmp_path / "user_folder"
    folder.mkdir()
    (folder / "inventory.csv").write_text("item,stock\nwidget,4\n", encoding="utf-8")
    isolated_client = TestClient(
        create_app(_isolated_settings(tmp_path)), raise_server_exceptions=False
    )

    response = isolated_client.get("/dataset", params={"folder": str(folder)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["folder"] == str(folder)
    assert {table["table"] for table in payload["tables"]} == {"inventory"}
    assert payload["skipped"] == []


def test_dataset_rejects_missing_folder(tmp_path: Any) -> None:
    isolated_client = TestClient(
        create_app(_isolated_settings(tmp_path)), raise_server_exceptions=False
    )

    response = isolated_client.get(
        "/dataset", params={"folder": str(tmp_path / "nowhere")}
    )

    assert response.status_code == 422
    assert "not a directory" in response.json()["detail"]


def test_ask_routes_reject_missing_folder(tmp_path: Any) -> None:
    isolated_client = TestClient(
        create_app(_isolated_settings(tmp_path)), raise_server_exceptions=False
    )
    missing = str(tmp_path / "nowhere")

    response = isolated_client.post("/ask", json={"question": "q", "folder": missing})
    assert response.status_code == 422

    response = isolated_client.get(
        "/ask/stream", params={"question": "q", "folder": missing}
    )
    assert response.status_code == 422


def test_ask_scopes_run_settings_to_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    seen: dict[str, Any] = {}

    async def fake(question: str, **kwargs: Any) -> AgentResult:
        seen["data_dir"] = kwargs["settings"].data_dir
        seen["generate_sample_data"] = kwargs["settings"].generate_sample_data
        return AgentResult(answer="Scoped.")

    monkeypatch.setattr("insight_agent.api.ask", fake)
    folder = tmp_path / "user_folder"
    folder.mkdir()
    (folder / "inventory.csv").write_text("item,stock\nwidget,4\n", encoding="utf-8")
    isolated_client = TestClient(
        create_app(_isolated_settings(tmp_path)), raise_server_exceptions=False
    )

    response = isolated_client.post("/ask", json={"question": "q", "folder": str(folder)})

    assert response.status_code == 200
    assert seen["data_dir"] == folder
    assert seen["generate_sample_data"] is False


def test_dataset_converts_txt_and_reports_skipped_files(tmp_path: Any) -> None:
    folder = tmp_path / "user_folder"
    folder.mkdir()
    (folder / "readings.txt").write_text("a;b\n1;2\n3;4\n", encoding="utf-8")
    (folder / "run.bat").write_text("echo hello\n", encoding="utf-8")
    (folder / "broken.xlsx").write_text("not a real workbook", encoding="utf-8")
    isolated_client = TestClient(
        create_app(_isolated_settings(tmp_path)), raise_server_exceptions=False
    )

    response = isolated_client.get("/dataset", params={"folder": str(folder)})

    assert response.status_code == 200
    payload = response.json()
    assert {table["table"] for table in payload["tables"]} == {"readings"}
    assert (folder / ".converted" / "readings.csv").exists()
    skipped = {entry["file"]: entry["reason"] for entry in payload["skipped"]}
    assert set(skipped) == {"run.bat", "broken.xlsx"}
    assert "unsupported file type" in skipped["run.bat"]
    assert "conversion failed" in skipped["broken.xlsx"]


def test_dataset_never_generates_samples_into_user_folder(tmp_path: Any) -> None:
    folder = tmp_path / "empty_user_folder"
    folder.mkdir()
    isolated_client = TestClient(
        create_app(_isolated_settings(tmp_path)), raise_server_exceptions=False
    )

    response = isolated_client.get("/dataset", params={"folder": str(folder)})

    assert response.status_code == 200
    assert response.json()["tables"] == []
    assert list(folder.iterdir()) == []
