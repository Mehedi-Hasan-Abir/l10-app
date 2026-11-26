"""Meeting rules: rating average, scorecard week under review, agenda and section timing."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta, tzinfo
from decimal import Decimal

from app.rules.timeutil import COMPANY_TZ, company_date, require_aware, round_half_up, week_start_of

# (key, title, planned duration in seconds): Scorecard 5 min, IDS 60 min, Conclude 10 min.
AGENDA: tuple[tuple[str, str, int], ...] = (
    ("scorecard", "Scorecard", 300),
    ("ids", "IDS", 3600),
    ("conclude", "Conclude", 600),
)


def meeting_rating(ratings: Sequence[int]) -> Decimal | None:
    """Average of the submitted ratings to one decimal, half up ([8, 9, 8, 8] -> 8.3). Empty -> None."""
    if not ratings:
        return None
    return round_half_up(Decimal(sum(ratings)) / Decimal(len(ratings)), 1)


def scorecard_week_for(started_at: datetime, tz: tzinfo = COMPANY_TZ) -> date:
    """The scorecard week a meeting reviews: the last closed week as of its start (Monday of the
    company-local start date, minus 7 days)."""
    return week_start_of(company_date(started_at, tz)) - timedelta(days=7)


def next_section(section: str) -> str | None:
    """Key of the section after `section`; None after the last one. Unknown key -> ValueError."""
    keys = [key for key, _, _ in AGENDA]
    if section not in keys:
        raise ValueError(f"unknown meeting section {section!r}")
    index = keys.index(section) + 1
    return keys[index] if index < len(keys) else None


def section_duration_s(section: str) -> int:
    """Planned duration of an agenda section in seconds. Unknown key -> ValueError."""
    for key, _, duration in AGENDA:
        if key == section:
            return duration
    raise ValueError(f"unknown meeting section {section!r}")


def section_timing(section_started_at: datetime, now: datetime, duration_s: int) -> tuple[int, bool]:
    """`(elapsed_seconds, over_time)` for the current section.

    Elapsed is whole seconds since the section started, floored and never negative. Over time means
    elapsed > duration, so exactly at the planned duration is not over; one second later is. Overrun never
    advances the meeting; this only reports it.
    """
    delta = require_aware(now, "now") - require_aware(section_started_at, "section_started_at")
    elapsed = max(0, int(delta.total_seconds()))
    return elapsed, elapsed > duration_s
