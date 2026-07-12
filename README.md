# Insight Agent - an agentic AI data analyst built on MCP

> Ask a question about a dataset in plain English. The agent plans, then calls
> tools over the **Model Context Protocol (MCP)** (inspect schema, run SQL, draw a
> chart) in a loop until it can answer with evidence. Not a single prompt, but a
> real tool-using agent talking to a real MCP server.

**Example**

```
$ insight-agent "Which region had the biggest drop in revenue in Q3, and why?"

[plan]  compare Q2 vs Q3 revenue by region, then break the worst one down by category
tools/call run_sql      SELECT region, ... GROUP BY region
tools/call run_sql      SELECT category, ... WHERE region = 'West' ...
tools/call create_chart bar chart of category revenue, Q2 vs Q3  -> charts/west_q3.png

The West region fell the hardest: revenue dropped 23 percent ($412k -> $317k) from
Q2 to Q3. The decline is concentrated in the "Outdoor" category (-58 percent),
which alone accounts for about 80 percent of the regional drop. Every other
category was flat or up. See charts/west_q3.png.
```

---

## Why this project exists

A portfolio piece for a mid/senior **Data / ML / AI** role, built to demonstrate
the things that actually matter in 2026, not a tutorial notebook:

- **MCP, end to end.** A standalone **MCP server** exposes the data-analysis tools,
  and a separate **MCP client host** (the agent) discovers and calls them over the
  protocol. The server works in any MCP client (Claude Desktop, Cursor), not only
  this agent.
- **Agentic AI done properly.** An LLM that plans and drives a multi-step tool-use
  loop (function calling), rather than a single request and response. The loop is
  written from scratch, with no agent framework.
- **A real, safe tool surface.** SQL over a real database, deterministic chart
  generation, schema inspection. Tools are typed and validated, and SQL is
  strictly read-only (enforced by parsing, not by trusting the model).
- **Evaluation, not vibes.** A rubric-based eval harness scores the agent's answers
  on a fixed set of questions, so quality is measurable and regressions are caught.
- **Engineering maturity.** API, CLI, typed config, tests, linting, and CI. The
  difference between a script and a system.

## Architecture

MCP is the boundary. The agent host never imports a tool function directly, it
reaches every tool over the protocol. That is exactly how Claude Desktop and Cursor
consume tools, so the same server plugs straight into them.

```
   DeepSeek (LLM)                    +-- Claude Desktop / Cursor --+  <- also connect here
        ^                            |                             |
        | tool calls (function API)  +--------------+--------------+
   +----+---------------+   MCP (stdio / JSON-RPC)  |
   |  Agent = MCP host  | <------------------------>|  Insight MCP server
   |  (client)          |   tools/list, tools/call  |  describe_schema
   +--------------------+                           |  run_sql (read-only)
                                                    |  create_chart
                                                    +------------+----------
                                                                 v
                                                           DuckDB (dataset)
```

The LLM itself does not speak MCP. The host lists the server's MCP tools, converts
them into the model's function-calling schema, runs the plan-call-observe loop, and
dispatches each of the model's tool calls back through the MCP client. This is the
standard MCP host design.

## What it does

1. Loads every dataset in a data directory (`DATA_DIR`, default `data/`) into
   DuckDB inside the MCP server - one table per file. CSV loads directly;
   Excel (one table per sheet), TXT/TSV (delimiter sniffed), JSON, and Parquet
   are converted to cached CSVs first. Two joinable synthetic tables ship with
   the repo: `sample_sales` and `region_targets`.
2. Exposes the data to any MCP client through three tools:
   `describe_schema`, `run_sql`, `create_chart`. Cross-table joins just work.
3. The agent host reasons about the question, calls tools in a loop, and returns a
   grounded answer plus any charts it generated.
4. A built-in web UI streams the agent's work live: the current goal, each SQL
   command as it executes, per-step results, and the final answer with charts.

## Web UI

```bash
uvicorn insight_agent.api:app
# open http://127.0.0.1:8000
```

A React single-page app streams the agent's work live over Server-Sent Events
(`GET /ask/stream`): each run renders as an execution trace grouped by LLM
round, with the model's stated intent, every SQL command highlighted as it
executes, per-step results and timings, a live "thinking" indicator between
tool calls, and charts the moment they are written. When a run finishes, the
trace folds into a one-line receipt (rounds, tools, tokens, time) and the
markdown answer becomes the card's face; the receipt re-expands the full
trace on click. Around the trace:

- a landing page built from the actual dataset: table cards with column chips
  and suggested questions derived from the schema
- multiple chats in a left sidebar: each chat is bound to one local data
  folder and keeps its own runs, model, and folder; chats persist in
  localStorage across reloads (the last 20 runs per chat)
- a data folder panel: the active chat's folder, the known folders to pick
  from, an add-folder input (the server is local, so a pasted path is
  enough), and a load-and-convert action backed by `GET /dataset?folder=...`
  that lists the resulting tables plus any skipped files with the reason
- a dataset explorer (tables, row counts, column types, value hints); on
  narrow screens the sidebar becomes a horizontal strip with a chat picker
  chip and table chips that open a bottom sheet
- a model picker backed by `GET /meta` (configured via `LLM_MODELS`), with the
  chosen model passed per run
- an effort gauge per run plus a progress filament under the header, updated
  live from the host's usage events, and a stop control that abandons a run
- a stats modal behind a header button: usage tables by chat and by model
  (runs, tool calls, tokens, agent time), computed client-side from the
  persisted runs; `GET /stats` still reports process totals for API users
- a system/light/dark theme toggle, a chart lightbox, and charts rendered by
  the MCP server in a matching house style

`POST /ask` remains for programmatic use and returns the run's usage summary
alongside the answer and chart paths; like the stream, it accepts an optional
`folder` to scope the run to one data directory.

The built app is committed under `src/insight_agent/static/`, so serving it
needs no Node. To work on the frontend itself:

```bash
cd web
npm install
npm run dev     # Vite dev server, proxies API calls to uvicorn on :8000
npm run build   # typecheck and rebuild src/insight_agent/static/
```

## Bring your own data

Point `DATA_DIR` at any folder of CSV, Excel, TXT/TSV, JSON, or Parquet files
and ask questions about them, or add a folder from the web UI and bind a chat
to it - every run in that chat sees only that folder's tables. Sample data is
only generated into the configured default directory, and only when it is
missing or empty - never into your own folders.

## Tech stack

| Area       | Choice                                                     |
| ---------- | ---------------------------------------------------------- |
| Language   | Python 3.10+                                                |
| Protocol   | MCP, official `mcp` Python SDK, stdio transport            |
| LLM API    | OpenAI SDK -> DeepSeek (`deepseek-chat`), provider-agnostic |
| Data       | DuckDB + pandas                                             |
| Charts     | matplotlib                                                  |
| Serving    | FastAPI + Uvicorn                                           |
| Web UI     | React + TypeScript + Vite (prebuilt, no Node at runtime)   |
| Config     | pydantic-settings (env-driven)                             |
| Quality    | pytest, ruff, GitHub Actions                                |

## Using the MCP server on its own

The server is a first-class artifact. Point any MCP client at it:

```jsonc
// Claude Desktop: claude_desktop_config.json
{
  "mcpServers": {
    "insight": {
      "command": "python",
      "args": ["-m", "insight_agent.mcp_server"]
    }
  }
}
```

Then ask the client questions about the data directly, with no agent host involved.

## Quick start

```bash
pip install -e ".[dev]"
cp .env.example .env            # add DEEPSEEK_API_KEY
insight-agent "which region dropped most in Q3, and why?"
```

## Development

```bash
pip install -e ".[dev]"      # install with dev dependencies
pytest                        # full suite - passes with no API key set
ruff check .                  # lint
mypy src                      # type check
python -m evals.run_evals     # score the agent against the fixed question set
python -m insight_agent.mcp_server        # run the MCP server standalone (stdio)
uvicorn insight_agent.api:app --reload    # run the HTTP API
```

The test suite never needs a network or an API key: the tools, the read-only
SQL guard, the MCP server (over in-process protocol sessions), and the agent
loop (driven by a scripted fake LLM against the real server subprocess) are
all tested directly. Only the eval harness calls the live LLM.

## Evaluation

`python -m evals.run_evals` runs the agent over a fixed set of questions with
known answers derived from the sample dataset (the synthetic data plants a
verifiable story: the West region's Outdoor category collapses in Q3). Each
answer is scored against a rubric and the run prints a per-question PASS/FAIL
plus an overall pass rate. A change that lowers the pass rate is a regression.

## Roadmap

- [x] Config, DuckDB data layer, synthetic sample dataset
- [x] Three tools with a fully tested read-only SQL guard
- [x] MCP server (stdio) wrapping the tools
- [x] LLM client wrapper (DeepSeek via the openai SDK)
- [x] Agent host: MCP client plus the plan-call-observe loop
- [x] CLI and FastAPI interfaces
- [x] Evaluation harness with a fixed question set
- [x] Tests and GitHub Actions CI
- [x] Multi-dataset data directory with format ingestion (Excel, TXT/TSV, JSON, Parquet)
- [x] Structured agent events and a live-progress web UI (SSE)
- [x] React frontend: run trace, dataset explorer, model picker, effort and usage stats
- [x] Six-table sample dataset with a joinable story world (margins, marketing, returns)
- [x] Multi-chat workspace: folder-bound chats, add-and-convert folders, per-chat and per-model usage stats
- [ ] Docs GIF/screenshot for the CV

## Status

Implemented end to end: 148 tests, lint and type checks clean, CI configured,
multi-table ingestion, folder-bound multi-chat workspace, and a streaming
React web UI with per-run effort and token accounting.
