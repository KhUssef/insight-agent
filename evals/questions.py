"""The fixed evaluation question set.

Every expectation is derived from the shipped deterministic dataset, verified
by direct DuckDB queries: West leads total revenue and has the largest Q2 to
Q3 drop (driven by the Outdoor category), South trails, Electronics is the
top category, and the data covers all twelve months of 2025 in 3600 orders.

The sample data directory ships two joinable tables: `sample_sales` (the
original per-order sales rows) and `region_targets` (one revenue target per
region). Joining them on `region` and comparing 2025 actual revenue to target
gives, verified by direct DuckDB queries: East 2,863,361.86 vs target
2,720,194 (exceeds), North 2,651,345.47 vs target 2,518,778 (exceeds), South
2,237,297.56 vs target 2,125,433 (exceeds), West 3,081,809.21 vs target
3,389,990 (misses by 308,180.79). Three regions meet or exceed their target;
West is the only one that misses.

A rubric is a list of checks applied to the agent's answer and chart list:
- {"kind": "contains_any", "values": [...]}: at least one value appears
- {"kind": "contains_all", "values": [...]}: every value appears
- {"kind": "not_contains", "values": [...]}: none of the values appears
- {"kind": "regex", "pattern": ...}: the pattern matches somewhere
- {"kind": "chart_created"}: at least one chart file was produced
Text matching is case-insensitive.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    """One evaluation question with its scoring rubric."""

    id: str
    question: str
    rubric: list[dict[str, Any]]


QUESTIONS: list[EvalCase] = [
    EvalCase(
        id="q3-drop-region",
        question=(
            "Which region had the biggest drop in revenue from Q2 to Q3, "
            "and which category drove the decline?"
        ),
        rubric=[
            {"kind": "contains_any", "values": ["West"]},
            {"kind": "contains_any", "values": ["Outdoor"]},
        ],
    ),
    EvalCase(
        id="top-region-revenue",
        question="Which region generated the most total revenue over the whole dataset?",
        rubric=[{"kind": "contains_any", "values": ["West"]}],
    ),
    EvalCase(
        id="lowest-region-revenue",
        question="Which region generated the least total revenue overall?",
        rubric=[{"kind": "contains_any", "values": ["South"]}],
    ),
    EvalCase(
        id="top-category-revenue",
        question="Which product category has the highest total revenue?",
        rubric=[{"kind": "contains_any", "values": ["Electronics"]}],
    ),
    EvalCase(
        id="date-range",
        question="What time period does the sales data cover?",
        rubric=[
            {"kind": "contains_all", "values": ["2025"]},
            {"kind": "contains_any", "values": ["January", "Jan", "2025-01"]},
            {"kind": "contains_any", "values": ["December", "Dec", "2025-12"]},
        ],
    ),
    EvalCase(
        id="order-count",
        question="How many orders are in the dataset?",
        rubric=[{"kind": "regex", "pattern": r"3,?600"}],
    ),
    EvalCase(
        id="chart-region-revenue",
        question="Create a bar chart of total revenue by region and tell me what it shows.",
        rubric=[
            {"kind": "chart_created"},
            {"kind": "contains_any", "values": ["West"]},
        ],
    ),
    EvalCase(
        id="target-miss",
        question="Which regions missed their revenue target in 2025, and by how much?",
        rubric=[
            {"kind": "contains_all", "values": ["West"]},
            {
                "kind": "contains_any",
                "values": ["missed", "below", "under", "short", "did not meet", "didn't meet"],
            },
            {"kind": "regex", "pattern": r"30[78][,.]?\d{0,3}"},
        ],
    ),
    EvalCase(
        id="target-hit-count",
        question="How many regions met or exceeded their revenue target?",
        rubric=[
            {"kind": "regex", "pattern": r"\b(3|three)\b"},
            {"kind": "not_contains", "values": ["four regions", "all four", "all 4"]},
        ],
    ),
    EvalCase(
        id="multi-table-awareness",
        question="What tables are available and how do they relate?",
        rubric=[
            {"kind": "contains_all", "values": ["sample_sales", "region_targets"]},
        ],
    ),
]
