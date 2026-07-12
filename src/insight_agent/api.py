"""HTTP interface.

A FastAPI layer over the agent host: POST /ask runs one agent turn and
returns the answer with any chart paths, GET /ask/stream streams the same
kind of run as Server-Sent Events for a live-progress UI, GET /meta reports
the configured LLM and the models a caller may select, GET /dataset reports
a data folder's tables for display and converts its supported files, GET
/stats reports the process's accumulated usage totals, GET /charts serves
the written chart images, and GET / serves the web frontend. The ask routes
and /dataset accept an optional folder argument that scopes the run or the
description to one local data directory. All reasoning lives in the host;
this module only translates HTTP to ask() and back.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from insight_agent.config import Settings, get_settings
from insight_agent.data import connect
from insight_agent.host import AgentEvent, AgentResult, ask
from insight_agent.ingest import scan_tables
from insight_agent.llm import MissingAPIKeyError
from insight_agent.tools import describe_schema

_STATIC_DIR = Path(__file__).parent / "static"


class AskRequest(BaseModel):
    """A single natural-language question about the dataset.

    model optionally names which of the configured models should run the
    turn; when omitted the default model is used. folder optionally names a
    local data directory to scope the run to; when omitted the run uses the
    configured default data directory.
    """

    question: str
    model: str | None = None
    folder: str | None = None

    @field_validator("question")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class AskResponse(BaseModel):
    """The agent's answer, the chart files it produced, and the run's effort.

    usage carries the host's run summary: rounds used, the round cap, tool
    calls made, duration, the model name, and token counts when the provider
    reports them.
    """

    answer: str
    charts: list[str]
    usage: dict[str, Any]


class MetaResponse(BaseModel):
    """The LLM configuration a frontend needs to offer model selection."""

    model: str
    models: list[str]
    base_url: str
    max_tool_rounds: int
    api_key_configured: bool


@dataclass
class SessionStats:
    """In-memory usage totals for one server process.

    Accumulated across every run served by this app instance and reset when
    the process restarts. questions counts runs started, answers counts runs
    that returned an answer, and errors counts runs that failed.
    """

    questions: int = 0
    answers: int = 0
    errors: int = 0
    rounds: int = 0
    tool_calls: int = 0
    charts: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    duration_seconds: float = 0.0

    def record(self, result: AgentResult) -> None:
        """Fold one completed run's usage summary into the totals."""
        usage = result.usage
        self.answers += 1
        self.rounds += int(usage.get("rounds", 0))
        self.tool_calls += int(usage.get("tool_calls", 0))
        self.charts += len(result.charts)
        self.prompt_tokens += int(usage.get("prompt_tokens", 0))
        self.completion_tokens += int(usage.get("completion_tokens", 0))
        self.total_tokens += int(usage.get("total_tokens", 0))
        self.duration_seconds = round(
            self.duration_seconds + float(usage.get("duration_seconds", 0.0)), 3
        )


def _sse_message(event: AgentEvent) -> str:
    """Render one AgentEvent as a named Server-Sent Events message."""
    data = json.dumps({"text": event.text, "detail": event.detail})
    return f"event: {event.kind}\ndata: {data}\n\n"


def _sse_error(message: str) -> str:
    """Render a clean, stack-trace-free error message as an "error" SSE message."""
    data = json.dumps({"text": message, "detail": {"error": message}})
    return f"event: error\ndata: {data}\n\n"


async def _stream_answer(
    question: str, settings: Settings, stats: SessionStats
) -> AsyncIterator[str]:
    """Run one agent turn and yield each of its events as an SSE message.

    The host's on_event callback is synchronous, so it is bridged to this
    async generator with a queue: ask() runs as a background task on the
    current event loop, and on_event pushes each AgentEvent onto the queue
    from that same loop as it is emitted. The "answer" event is held back
    until the task signals completion, so the run is already folded into the
    session stats when the client receives it and a stats fetch triggered by
    the answer can never observe the pre-run totals; the stream ends right
    after delivering it. If the task raises before ever emitting one - a
    missing API key, or any other failure - the exception is caught inside
    the task and turned into a final "error" message instead, so a failure
    can never leave the connection open. Every run is folded into the given
    session stats, as a completed answer or as an error.
    """
    queue: asyncio.Queue[AgentEvent | Exception | None] = asyncio.Queue()

    def on_event(event: AgentEvent) -> None:
        queue.put_nowait(event)

    async def run() -> None:
        try:
            result = await ask(question, settings=settings, on_event=on_event)
        except Exception as exc:
            stats.errors += 1
            queue.put_nowait(exc)
        else:
            stats.record(result)
            queue.put_nowait(None)

    stats.questions += 1
    task = asyncio.create_task(run())
    pending_answer: AgentEvent | None = None
    try:
        while True:
            item = await queue.get()
            if item is None:
                if pending_answer is not None:
                    yield _sse_message(pending_answer)
                break
            if isinstance(item, Exception):
                if isinstance(item, MissingAPIKeyError):
                    message = str(item)
                else:
                    message = f"internal error: {item}"
                yield _sse_error(message)
                break
            if item.kind == "answer":
                pending_answer = item
                continue
            yield _sse_message(item)
    finally:
        if not task.done():
            await task


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application, wired to the given settings.

    Each call returns an independent app: its own chart mount directory
    (created if missing, so the mount never fails on a fresh checkout), its
    own session stats, and its own settings passed through to every agent
    run. This lets tests construct an isolated app without touching the
    process-wide configuration; `insight_agent.api.app` is one instance built
    from the process's own settings, and that is what
    `uvicorn insight_agent.api:app` serves.
    """
    if settings is None:
        settings = get_settings()
    app_settings = settings

    app = FastAPI(
        title="Insight Agent",
        description="Agentic AI data analyst over the Model Context Protocol.",
    )
    stats = SessionStats()

    app_settings.charts_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/charts", StaticFiles(directory=str(app_settings.charts_dir)), name="charts")

    assets_dir = _STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    def _resolve_folder(folder: str | None) -> Path | None:
        """Validate an optional user-supplied data folder path.

        Returns None for an absent folder (the default data directory
        applies) and the path otherwise. A path that does not exist or is
        not a directory is rejected with a 422.
        """
        if folder is None or not folder.strip():
            return None
        path = Path(folder)
        if not path.is_dir():
            raise HTTPException(
                status_code=422,
                detail=f"folder does not exist or is not a directory: {folder}",
            )
        return path

    def _is_default_data_dir(path: Path) -> bool:
        """Report whether a folder is the configured default data directory."""
        try:
            return path.resolve() == app_settings.data_dir.resolve()
        except OSError:
            return False

    def _settings_for(model: str | None, folder: str | None = None) -> Settings:
        """Resolve the per-run settings for optional model and folder overrides.

        A model that is not in the configured list is rejected with a 422 so
        an arbitrary model name can never reach the provider. A folder that
        is not an existing directory is rejected with a 422; a valid one
        scopes the run's data directory, with sample-data generation disabled
        so a user's empty folder is never populated with generated files.
        """
        updates: dict[str, Any] = {}
        if model is not None and model != app_settings.llm_model:
            available = app_settings.available_models()
            if model not in available:
                raise HTTPException(
                    status_code=422,
                    detail=f"unknown model {model!r}; available: {', '.join(available)}",
                )
            updates["llm_model"] = model
        path = _resolve_folder(folder)
        if path is not None and not _is_default_data_dir(path):
            updates["data_dir"] = path
            updates["generate_sample_data"] = False
        if not updates:
            return app_settings
        return app_settings.model_copy(update=updates)

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """Serve the web frontend page."""
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/meta", response_model=MetaResponse)
    def meta() -> MetaResponse:
        """Report the LLM configuration: active model, selectable models, limits."""
        return MetaResponse(
            model=app_settings.llm_model,
            models=app_settings.available_models(),
            base_url=app_settings.llm_base_url,
            max_tool_rounds=app_settings.max_tool_rounds,
            api_key_configured=bool(app_settings.deepseek_api_key),
        )

    @app.get("/dataset")
    def dataset(folder: str | None = Query(None)) -> dict[str, Any]:
        """Describe a data folder's tables: columns, types, row counts, hints.

        With no folder argument this describes the configured default data
        directory, generating the sample datasets first when it is missing or
        empty. A folder argument scopes the description to that directory
        (422 when it is not an existing directory) and never generates sample
        files into it. Scanning converts any supported non-CSV files into
        cached CSVs, so this endpoint doubles as the "load and convert"
        action; the response carries the resolved folder, the same
        deterministic schema description the MCP server exposes as the
        describe_schema tool, and a skipped list naming every file the scan
        could not load with the reason. The agent's own data access still
        happens exclusively over MCP tool calls.
        """
        path = _resolve_folder(folder)
        target = app_settings.data_dir if path is None else path
        ensure_samples = path is None or _is_default_data_dir(path)
        con = connect(target, ensure_samples=ensure_samples)
        try:
            payload = describe_schema(con)
        finally:
            con.close()
        payload["folder"] = str(target)
        payload["skipped"] = [asdict(item) for item in scan_tables(target).skipped]
        return payload

    @app.get("/stats")
    def usage_stats() -> dict[str, Any]:
        """Report the usage totals accumulated by this server process."""
        return asdict(stats)

    @app.post("/ask", response_model=AskResponse)
    async def ask_question(request: AskRequest) -> AskResponse:
        """Run one agent turn and return the grounded answer."""
        run_settings = _settings_for(request.model, request.folder)
        stats.questions += 1
        try:
            result = await ask(request.question, settings=run_settings)
        except MissingAPIKeyError as exc:
            stats.errors += 1
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception:
            stats.errors += 1
            raise
        stats.record(result)
        return AskResponse(answer=result.answer, charts=result.charts, usage=result.usage)

    @app.get("/ask/stream", include_in_schema=False)
    async def ask_stream(
        question: str = Query(...),
        model: str | None = Query(None),
        folder: str | None = Query(None),
    ) -> StreamingResponse:
        """Stream one agent run's progress as Server-Sent Events.

        Each AgentEvent becomes one SSE message: `event: <kind>` with
        `data: {"text": ..., "detail": ...}`. A blank question, an unknown
        model, or an invalid folder is rejected with 422 before the stream
        opens; any failure during the run - a missing API key or anything
        else - ends the stream with a final `event: error` message instead
        of raising.
        """
        if not question.strip():
            raise HTTPException(status_code=422, detail="question must not be blank")
        run_settings = _settings_for(model, folder)
        return StreamingResponse(
            _stream_answer(question, run_settings, stats), media_type="text/event-stream"
        )

    return app


app = create_app(get_settings())
