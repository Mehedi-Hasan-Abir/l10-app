"""Scorecard rules: measurable status against a goal, and the human goal label."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from app.rules.timeutil import to_decimal

GoalType = Literal["at_least", "at_most", "between", "exactly"]
MeasurableStatus = Literal["on_track", "off_track", "pending"]


@dataclass(frozen=True)
class Goal:
    """A measurable's target. `value` for at_least/at_most/exactly, `min`/`max` for between.

    int bounds (seed JSON has `"value": 10`) are normalized to Decimal; floats are rejected.
    """

    type: GoalType
    value: Decimal | None = None
    min: Decimal | None = None
    max: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("value", "min", "max"):
            raw = getattr(self, name)
            if raw is not None:
                object.__setattr__(self, name, to_decimal(raw))
        if self.type == "between":
            if self.min is None or self.max is None:
                raise ValueError("a 'between' goal needs both min and max")
            if self.min > self.max:
                raise ValueError("a 'between' goal needs min <= max")
        elif self.type in ("at_least", "at_most", "exactly"):
            if self.value is None:
                raise ValueError(f"a '{self.type}' goal needs a value")
        else:
            raise ValueError(f"unknown goal type {self.type!r}")


def _meets_goal(goal: Goal, value: Decimal) -> bool:
    """Boundaries count as met: >= for at_least, <= for at_most, min <= v <= max, == for exactly."""
    match goal.type:
        case "at_least":
            return goal.value is not None and value >= goal.value
        case "at_most":
            return goal.value is not None and value <= goal.value
        case "exactly":
            return goal.value is not None and value == goal.value
        case "between":
            return goal.min is not None and goal.max is not None and goal.min <= value <= goal.max
    raise ValueError(f"unknown goal type {goal.type!r}")


def measurable_status(
    goal: Goal, value: Decimal | int | None, week_start: date, current_week: date
) -> MeasurableStatus:
    """Status of one scorecard cell.

    - No score: "pending" while the week is the current (or a future) week, "off_track" once it is closed.
    - A score is compared with the goal; hitting the boundary exactly is on track. The current week is
      evaluated like any other week once it has a score.
    """
    if value is None:
        return "pending" if week_start >= current_week else "off_track"
    return "on_track" if _meets_goal(goal, to_decimal(value)) else "off_track"


def _number(d: Decimal) -> str:
    """Plain decimal text without trailing zeros: 3.0 -> "3", 2.50 -> "2.5", 1E+2 -> "100"."""
    if d == 0:
        return "0"
    return format(d.normalize(), "f")


def goal_label(goal: Goal, unit: str) -> str:
    """Human label for a goal, e.g. "≥ 10", "≤ 3 %", "2–4 hours", "= 1", "≥ $500k".

    Numbers drop trailing zeros. Unit rendering: "count" (or empty) is omitted; a unit starting with "$"
    is a currency prefix plus suffix applied to every number ("$k" -> "$500k", "$2k–$4k"); any other unit
    follows after a space ("%" -> "3 %", "hours" -> "2–4 hours").
    """

    def fmt(d: Decimal | None) -> str:
        if d is None:
            raise ValueError(f"goal {goal.type!r} is missing a bound")
        text = _number(d)
        return f"${text}{unit[1:]}" if unit.startswith("$") else text

    suffix = "" if unit in ("", "count") or unit.startswith("$") else f" {unit}"
    match goal.type:
        case "at_least":
            return f"≥ {fmt(goal.value)}{suffix}"
        case "at_most":
            return f"≤ {fmt(goal.value)}{suffix}"
        case "exactly":
            return f"= {fmt(goal.value)}{suffix}"
        case "between":
            return f"{fmt(goal.min)}–{fmt(goal.max)}{suffix}"
    raise ValueError(f"unknown goal type {goal.type!r}")
