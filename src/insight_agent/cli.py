"""Command-line interface.

One-shot usage: insight-agent "which region dropped most in Q3". Progress
lines stream to stderr while the agent works; the final answer, plus a
charts line when any were produced, prints to stdout.
"""

import argparse
import asyncio
import sys

from insight_agent.host import AgentEvent, ask
from insight_agent.llm import MissingAPIKeyError


def _progress(event: AgentEvent) -> None:
    """Print one progress event to stderr, keeping stdout clean.

    A usage event prints as a bracketed "[round n/m ...]" effort line, a plan
    event prints as a "goal: ..." line, a tool_call event prints as its
    "tools/call ..." line, and a tool_result event prints as an indented
    "  -> ..." summary line. The answer event is not printed here; the final
    answer and charts are printed separately once the run completes.
    """
    if event.kind == "usage":
        print(f"[{event.text}]", file=sys.stderr)
    elif event.kind == "plan":
        print(f"goal: {event.text}", file=sys.stderr)
    elif event.kind == "tool_call":
        print(event.text, file=sys.stderr)
    elif event.kind == "tool_result":
        print(f"  -> {event.text}", file=sys.stderr)


def _force_utf8_output() -> None:
    """Reconfigure stdout and stderr to UTF-8 with replacement.

    Windows consoles default to a legacy code page, and model answers may
    contain characters outside it; without this, printing the answer can
    raise UnicodeEncodeError.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    """Entry point for the insight-agent console script."""
    _force_utf8_output()
    parser = argparse.ArgumentParser(
        prog="insight-agent",
        description="Ask a natural-language question about the dataset.",
    )
    parser.add_argument("question", help="the question to answer")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress tool-call progress lines on stderr",
    )
    args = parser.parse_args()

    on_event = None if args.quiet else _progress
    try:
        result = asyncio.run(ask(args.question, on_event=on_event))
    except MissingAPIKeyError:
        print(
            "no API key configured: set DEEPSEEK_API_KEY in .env or the environment",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None

    print(result.answer)
    if result.charts:
        print("charts: " + " ".join(result.charts))


if __name__ == "__main__":
    main()
