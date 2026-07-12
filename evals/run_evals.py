"""Run the agent over the fixed question set and print a pass rate.

Requires a configured LLM key: the harness exercises the real host, which
plans and calls tools over MCP. Exit codes: 0 all cases passed, 1 at least
one case failed, 2 no API key configured.
"""

import asyncio

from evals.questions import QUESTIONS
from evals.scoring import score
from insight_agent.config import get_settings
from insight_agent.host import ask


async def main() -> int:
    """Run every evaluation case sequentially and report the pass rate."""
    settings = get_settings()
    if not settings.deepseek_api_key:
        print("DEEPSEEK_API_KEY is not set; the eval harness needs a real LLM. Exiting.")
        return 2

    passed = 0
    for case in QUESTIONS:
        result = await ask(case.question, settings=settings)
        ok, failures = score(result.answer, result.charts, case.rubric)
        if ok:
            passed += 1
            print(f"{case.id}: PASS")
        else:
            print(f"{case.id}: FAIL ({'; '.join(failures)})")

    total = len(QUESTIONS)
    rate = 100.0 * passed / total
    print(f"passed {passed}/{total} ({rate:.1f} percent)")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
