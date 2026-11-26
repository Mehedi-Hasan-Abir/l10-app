"""Measurable status (brief 2.6 "Measurable status") and goal labels."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.rules.measurables import Goal, goal_label, measurable_status

CURRENT_WEEK = date(2026, 11, 9)
CLOSED_WEEK = date(2026, 11, 2)

# The five goals of team_lead in the seed.
M1 = Goal("at_least", value=Decimal("10"))  # New deals closed, count
M2 = Goal("at_most", value=Decimal("3.0"))  # Customer churn, %
M3 = Goal("between", min=Decimal("2"), max=Decimal("4"))  # Avg first response, hours
M4 = Goal("exactly", value=Decimal("1"))  # Payroll runs, count
M5 = Goal("at_least", value=Decimal("500"))  # Cash on hand, $k


def status_of(goal: Goal, value: str | int | None, week: date = CLOSED_WEEK) -> str:
    score = Decimal(value) if isinstance(value, str) else value
    return measurable_status(goal, score, week, CURRENT_WEEK)


@pytest.mark.parametrize(
    ("goal", "value", "expected"),
    [
        # at_least 10: the boundary is on track
        (M1, "10", "on_track"),
        (M1, "10.0", "on_track"),
        (M1, "11", "on_track"),
        (M1, "9", "off_track"),
        (M1, "9.99", "off_track"),
        # at_most 3.0: the boundary is on track
        (M2, "3.0", "on_track"),
        (M2, "3", "on_track"),
        (M2, "2.99", "on_track"),
        (M2, "0", "on_track"),
        (M2, "3.01", "off_track"),
        (M2, "4.5", "off_track"),
        # between 2 and 4: both boundaries are on track
        (M3, "2", "on_track"),
        (M3, "4", "on_track"),
        (M3, "3", "on_track"),
        (M3, "1.8", "off_track"),
        (M3, "1.99", "off_track"),
        (M3, "4.1", "off_track"),
        (M3, "4.01", "off_track"),
        # exactly 1: neither neighbour is on track
        (M4, "1", "on_track"),
        (M4, "1.0", "on_track"),
        (M4, "0", "off_track"),
        (M4, "2", "off_track"),
        (M4, "1.01", "off_track"),
        (M4, "0.99", "off_track"),
        # at_least 500
        (M5, "500", "on_track"),
        (M5, "499.9", "off_track"),
    ],
)
def test_a_score_is_compared_with_the_goal_and_boundaries_are_on_track(
    goal: Goal, value: str, expected: str
) -> None:
    assert status_of(goal, value) == expected


def test_int_scores_are_accepted_and_normalized_to_decimal() -> None:
    assert status_of(M1, 10) == "on_track"
    assert status_of(M1, 9) == "off_track"
    assert status_of(M4, 1) == "on_track"


def test_exact_decimal_comparison_has_no_float_noise() -> None:
    # 0.1 + 0.2 != 0.3 in floats; Decimals compare exactly.
    assert status_of(Goal("exactly", value=Decimal("0.3")), "0.30") == "on_track"
    assert status_of(Goal("at_most", value=Decimal("0.3")), "0.3000000000000000000001") == "off_track"


def test_float_scores_are_rejected() -> None:
    with pytest.raises(TypeError):
        measurable_status(M1, 10.0, CLOSED_WEEK, CURRENT_WEEK)  # type: ignore[arg-type]


@pytest.mark.parametrize("goal", [M1, M2, M3, M4, M5])
def test_a_closed_week_without_a_score_is_off_track(goal: Goal) -> None:
    assert status_of(goal, None, CLOSED_WEEK) == "off_track"  # last closed week
    assert status_of(goal, None, date(2026, 10, 19)) == "off_track"  # seed m5 gap
    assert status_of(goal, None, date(2026, 8, 17)) == "off_track"  # oldest week of the window


@pytest.mark.parametrize("goal", [M1, M2, M3, M4, M5])
def test_the_current_week_without_a_score_is_pending(goal: Goal) -> None:
    assert status_of(goal, None, CURRENT_WEEK) == "pending"


def test_a_future_week_without_a_score_is_pending() -> None:
    assert status_of(M1, None, date(2026, 11, 16)) == "pending"


def test_week_before_the_current_week_flips_to_off_track_when_empty() -> None:
    assert measurable_status(M1, None, date(2026, 11, 2), date(2026, 11, 9)) == "off_track"
    assert measurable_status(M1, None, date(2026, 11, 2), date(2026, 11, 2)) == "pending"


def test_a_score_in_the_current_week_is_evaluated_not_pending() -> None:
    # seed m5: 520 against >= 500 in the week of 2026-11-09
    assert status_of(M5, "520", CURRENT_WEEK) == "on_track"
    assert status_of(M5, "499", CURRENT_WEEK) == "off_track"
    assert status_of(M5, "500", CURRENT_WEEK) == "on_track"


def test_a_zero_score_is_a_score_not_a_missing_one() -> None:
    assert status_of(M2, "0", CLOSED_WEEK) == "on_track"  # at_most 3.0
    assert status_of(M1, "0", CLOSED_WEEK) == "off_track"  # a real 0 is not "no score"
    assert status_of(M1, 0, CURRENT_WEEK) == "off_track"


# --- Goal validation ---------------------------------------------------------------------------------


def test_goal_normalizes_int_bounds_to_decimal() -> None:
    goal = Goal("between", min=2, max=4)  # type: ignore[arg-type]
    assert goal.min == Decimal(2) and isinstance(goal.min, Decimal)
    assert isinstance(Goal("at_least", value=10).value, Decimal)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"type": "at_least"},
        {"type": "at_most"},
        {"type": "exactly"},
        {"type": "between", "min": Decimal(1)},
        {"type": "between", "max": Decimal(1)},
        {"type": "between", "min": Decimal(5), "max": Decimal(1)},
        {"type": "sometimes", "value": Decimal(1)},
        {"type": "at_least", "value": 1.5},
    ],
)
def test_malformed_goals_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises((ValueError, TypeError)):
        Goal(**kwargs)  # type: ignore[arg-type]


def test_between_with_equal_bounds_is_a_single_point() -> None:
    point = Goal("between", min=Decimal(3), max=Decimal(3))
    assert status_of(point, "3") == "on_track"
    assert status_of(point, "3.01") == "off_track"


# --- goal_label --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("goal", "unit", "label"),
    [
        (M1, "count", "≥ 10"),
        (M2, "%", "≤ 3 %"),  # 3.0 renders without the trailing zero
        (M3, "hours", "2–4 hours"),
        (M4, "count", "= 1"),
        (M5, "$k", "≥ $500k"),
        (Goal("at_most", value=Decimal("2")), "count", "≤ 2"),  # seed m7
        (Goal("at_least", value=Decimal("50")), "count", "≥ 50"),  # seed m6
        (Goal("at_least", value=Decimal("2.50")), "hours", "≥ 2.5 hours"),
        (Goal("at_least", value=Decimal("1E+2")), "count", "≥ 100"),
        (Goal("at_least", value=Decimal("0.0")), "%", "≥ 0 %"),
        (Goal("between", min=Decimal("2.5"), max=Decimal("4.0")), "$k", "$2.5k–$4k"),
        (Goal("between", min=Decimal("0.5"), max=Decimal("1.5")), "%", "0.5–1.5 %"),
        (Goal("exactly", value=Decimal("1")), "", "= 1"),
        (Goal("at_most", value=Decimal("100")), "$", "≤ $100"),
    ],
)
def test_goal_label_is_human_readable_without_trailing_zeros(goal: Goal, unit: str, label: str) -> None:
    assert goal_label(goal, unit) == label
