"""Rock rules: derived status and whole-percent progress from milestones."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.rules.timeutil import percent

RockStatus = Literal["on_track", "off_track", "complete"]
_ROCK_STATUSES = ("on_track", "off_track", "complete")


@dataclass(frozen=True)
class MilestoneFacts:
    due_date: date
    done: bool


def milestone_past_due(m: MilestoneFacts, today: date) -> bool:
    """Past due = not done and due strictly before today (a milestone due today is not past due)."""
    return not m.done and m.due_date < today


def rock_status(seeded_status: RockStatus, milestones: Sequence[MilestoneFacts], today: date) -> RockStatus:
    """Derived Rock status.

    - "complete" if the seeded status is complete (wins over everything, even unfinished milestones).
    - "off_track" if seeded off_track, or any unfinished milestone is past due.
    - otherwise "on_track". With no milestones the seeded status alone decides.
    """
    if seeded_status not in _ROCK_STATUSES:
        raise ValueError(f"unknown rock status {seeded_status!r}")
    if seeded_status == "complete":
        return "complete"
    if seeded_status == "off_track" or any(milestone_past_due(m, today) for m in milestones):
        return "off_track"
    return "on_track"


def rock_progress(seeded_status: str, milestones: Sequence[MilestoneFacts]) -> int:
    """Whole-percent progress, half up: done / total milestones (1/8 -> 13, 1/3 -> 33).

    With no milestones: 100 once the seeded status is complete, otherwise 0.
    """
    if milestones:
        return percent(sum(1 for m in milestones if m.done), len(milestones))
    return 100 if seeded_status == "complete" else 0
