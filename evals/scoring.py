"""Rubric scoring for evaluation answers."""

import re
from typing import Any


def _check(answer_lower: str, charts: list[str], check: dict[str, Any]) -> str | None:
    """Apply one rubric check; return a failure reason or None on pass."""
    kind = check.get("kind")
    if kind == "contains_any":
        values: list[str] = check["values"]
        if any(value.lower() in answer_lower for value in values):
            return None
        return f"answer contains none of: {', '.join(values)}"
    if kind == "contains_all":
        missing = [value for value in check["values"] if value.lower() not in answer_lower]
        if not missing:
            return None
        return f"answer is missing: {', '.join(missing)}"
    if kind == "not_contains":
        present = [value for value in check["values"] if value.lower() in answer_lower]
        if not present:
            return None
        return f"answer wrongly contains: {', '.join(present)}"
    if kind == "regex":
        pattern: str = check["pattern"]
        if re.search(pattern, answer_lower, flags=re.IGNORECASE):
            return None
        return f"answer does not match pattern: {pattern}"
    if kind == "chart_created":
        if charts:
            return None
        return "no chart file was created"
    return f"unknown check kind: {kind!r}"


def score(
    result_answer: str,
    charts: list[str],
    rubric: list[dict[str, Any]],
) -> tuple[bool, list[str]]:
    """Score one answer against a rubric.

    Returns (passed, failure_reasons); passed is True only when every check
    in the rubric passes. Text matching is case-insensitive.
    """
    answer_lower = result_answer.lower()
    failures = [
        reason
        for check in rubric
        if (reason := _check(answer_lower, charts, check)) is not None
    ]
    return (not failures, failures)
