"""Aggregation of the fault-injection scorecard across baseline traces.

`benchmark-analyzer` used to score one baseline trace — whichever the store
returned first — so the printed accuracy was a property of the corpus rather
than of the analyzer, and moved between 71.4% and 85.7% with no code change.
It now scores every eligible trace and aggregates per fault; these tests pin the
aggregation itself, without needing traces, a database, or a model.
"""

from __future__ import annotations

from typing import Any

import pytest

from forensics.cli import _aggregate_fault_scorecards


def _result(
    fault: str,
    *,
    step: str = "extraction",
    category: str = "extraction_hallucination",
    step_correct: bool,
    category_correct: bool,
    scoreable: bool = True,
) -> dict[str, Any]:
    return {
        "fault": fault,
        "scoreable": scoreable,
        "expected_step": step,
        "expected_category": category,
        "step_correct": step_correct,
        "category_correct": category_correct,
    }


def _card(*results: dict[str, Any]) -> dict[str, Any]:
    return {"total": len(results), "results": list(results)}


def test_aggregation_weights_faults_by_how_often_they_were_scored() -> None:
    correct = _card(
        _result("hallucinated_entity", step_correct=True, category_correct=True),
        _result("context_loss", step="summarization", step_correct=False, category_correct=False),
    )
    mixed = _card(
        _result("hallucinated_entity", step_correct=True, category_correct=True),
        _result("context_loss", step="summarization", step_correct=True, category_correct=True),
    )

    aggregate = _aggregate_fault_scorecards([correct, mixed])

    assert aggregate["total"] == 4
    assert aggregate["step_accuracy"] == pytest.approx(3 / 4)
    rows = {row["fault"]: row for row in aggregate["results"]}
    assert rows["hallucinated_entity"]["traces"] == 2
    assert rows["hallucinated_entity"]["step_correct"] == 2
    assert rows["context_loss"]["traces"] == 2
    assert rows["context_loss"]["step_correct"] == 1


def test_category_accuracy_requires_the_step_too() -> None:
    """Category credit is joint, matching `score_all_faults`."""
    aggregate = _aggregate_fault_scorecards(
        [_card(_result("wrong_label", step_correct=False, category_correct=True))]
    )

    assert aggregate["step_accuracy"] == 0.0
    assert aggregate["category_accuracy"] == 0.0


def test_order_follows_first_appearance() -> None:
    aggregate = _aggregate_fault_scorecards(
        [
            _card(
                _result("b_fault", step_correct=True, category_correct=True),
                _result("a_fault", step_correct=True, category_correct=True),
            )
        ]
    )

    assert [row["fault"] for row in aggregate["results"]] == ["b_fault", "a_fault"]


def test_empty_scorecards_do_not_divide_by_zero() -> None:
    aggregate = _aggregate_fault_scorecards([])

    assert aggregate == {
        "total": 0,
        "unscoreable": 0,
        "step_accuracy": 0.0,
        "category_accuracy": 0.0,
        "results": [],
    }


def test_unscoreable_results_never_enter_the_denominator() -> None:
    """A corruption the baseline cannot host must not count as a miss.

    The offline fixture extracts no entities for most documents, so the
    `context_loss` fault is a no-op there: the analyzer correctly reports no
    failure. Folding that in as 0% reported an analyzer defect that does not
    exist and dragged the headline accuracy down with it.
    """
    scoreable = _card(
        _result("hallucinated_entity", step_correct=True, category_correct=True),
    )
    unscoreable = _card(
        _result(
            "context_loss",
            step="summarization",
            category="context_loss",
            step_correct=False,
            category_correct=False,
            scoreable=False,
        )
    )

    aggregate = _aggregate_fault_scorecards([scoreable, unscoreable])

    assert aggregate["total"] == 1
    assert aggregate["unscoreable"] == 1
    assert aggregate["step_accuracy"] == 1.0
    assert aggregate["category_accuracy"] == 1.0

    rows = {row["fault"]: row for row in aggregate["results"]}
    assert rows["context_loss"]["traces"] == 0
    assert rows["context_loss"]["unscoreable"] == 1


def test_a_fault_with_no_scoreable_baselines_is_reported_as_such() -> None:
    aggregate = _aggregate_fault_scorecards(
        [
            _card(
                _result(
                    "context_loss",
                    step="summarization",
                    category="context_loss",
                    step_correct=False,
                    category_correct=False,
                    scoreable=False,
                )
            )
        ]
    )

    assert aggregate["total"] == 0
    assert aggregate["step_accuracy"] == 0.0
    assert [row["traces"] for row in aggregate["results"]] == [0]
