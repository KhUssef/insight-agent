"""Scoring logic and question-set integrity, tested without any LLM."""

from evals.questions import QUESTIONS
from evals.scoring import score

KNOWN_KINDS = {"contains_any", "contains_all", "not_contains", "regex", "chart_created"}


def test_contains_any_passes_case_insensitively() -> None:
    ok, failures = score("The WEST region fell hardest.", [], [
        {"kind": "contains_any", "values": ["west", "east"]},
    ])
    assert ok
    assert failures == []


def test_contains_any_fails_with_reason() -> None:
    ok, failures = score("No relevant region here.", [], [
        {"kind": "contains_any", "values": ["West"]},
    ])
    assert not ok
    assert "West" in failures[0]


def test_contains_all_requires_every_value() -> None:
    rubric = [{"kind": "contains_all", "values": ["2025", "January"]}]
    ok, _ = score("From January to December 2025.", [], rubric)
    assert ok
    ok, failures = score("Covers 2025.", [], rubric)
    assert not ok
    assert "January" in failures[0]


def test_not_contains_passes_when_value_absent() -> None:
    rubric = [{"kind": "not_contains", "values": ["four"]}]
    ok, failures = score("Three regions exceeded their target.", [], rubric)
    assert ok
    assert failures == []


def test_not_contains_fails_case_insensitively_when_value_present() -> None:
    rubric = [{"kind": "not_contains", "values": ["four"]}]
    ok, failures = score("FOUR regions exceeded their target.", [], rubric)
    assert not ok
    assert "four" in failures[0]


def test_regex_check() -> None:
    rubric = [{"kind": "regex", "pattern": r"3,?600"}]
    assert score("There are 3600 orders.", [], rubric)[0]
    assert score("There are 3,600 orders.", [], rubric)[0]
    assert not score("There are many orders.", [], rubric)[0]


def test_chart_created_check() -> None:
    rubric = [{"kind": "chart_created"}]
    assert score("See the chart.", ["charts/by_region.png"], rubric)[0]
    ok, failures = score("See the chart.", [], rubric)
    assert not ok
    assert "chart" in failures[0]


def test_unknown_kind_fails_closed() -> None:
    ok, failures = score("anything", [], [{"kind": "mystery"}])
    assert not ok
    assert "unknown check kind" in failures[0]


def test_question_set_is_well_formed() -> None:
    assert QUESTIONS
    ids = [case.id for case in QUESTIONS]
    assert len(ids) == len(set(ids))
    for case in QUESTIONS:
        assert case.question.strip()
        assert case.rubric
        for check in case.rubric:
            assert check["kind"] in KNOWN_KINDS
            if check["kind"] in ("contains_any", "contains_all", "not_contains"):
                assert check["values"]
                assert all(isinstance(value, str) and value for value in check["values"])
            if check["kind"] == "regex":
                assert isinstance(check["pattern"], str) and check["pattern"]
