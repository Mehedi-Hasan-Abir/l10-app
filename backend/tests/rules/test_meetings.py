"""Meeting rating, scorecard week, agenda and section timing (brief 2.6 "Meeting rating", 2.5 Meeting)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.rules.meetings import (
    AGENDA,
    meeting_rating,
    next_section,
    scorecard_week_for,
    section_duration_s,
    section_timing,
)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


# --- meeting_rating ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ratings", "expected"),
    [
        ([8, 9, 7], "8.0"),  # seed mt1: 24/3
        ([8, 9, 8, 8], "8.3"),  # seed mt2: 33/4 = 8.25 -> 8.3 (round() would give 8.2)
        ([7], "7.0"),
        ([1, 2], "1.5"),
        ([9, 10], "9.5"),
        ([10, 10, 9], "9.7"),  # 9.666...
        ([8, 8, 9], "8.3"),  # 8.333...
        ([1, 1, 1, 2], "1.3"),  # 1.25 -> 1.3 (banker's rounding would give 1.2)
        ([5, 5, 5, 6, 6, 6, 6, 6], "5.6"),  # 45/8 = 5.625
        ([7, 8, 8, 8], "7.8"),  # 7.75 -> 7.8
        ([10, 10, 10], "10.0"),
        ([1], "1.0"),
    ],
)
def test_rating_is_the_average_to_one_decimal_half_up(ratings: list[int], expected: str) -> None:
    result = meeting_rating(ratings)
    assert result is not None
    assert str(result) == expected


def test_rating_of_no_ratings_is_none_not_zero() -> None:
    assert meeting_rating([]) is None


def test_rating_ignores_nobody_it_only_averages_what_it_is_given() -> None:
    # Attendees who did not rate are simply absent from the list; 5 attendees, 3 ratings -> 8.0
    assert str(meeting_rating([8, 9, 7])) == "8.0"


def test_rating_returns_a_decimal() -> None:
    assert isinstance(meeting_rating([8, 9]), Decimal)
    assert meeting_rating([8, 9]) == Decimal("8.5")


# --- scorecard_week_for ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("started_at", "week"),
    [
        ("2026-10-28T15:00:00Z", date(2026, 10, 19)),  # seed mt1 (Wednesday)
        ("2026-11-04T15:00:00Z", date(2026, 10, 26)),  # seed mt2 (Wednesday; the DST week)
        ("2026-11-11T15:00:00Z", date(2026, 11, 2)),  # a meeting at the default clock
        # Monday 00:00 local starts the next week: one second earlier is still the old week.
        ("2026-11-09T05:59:59Z", date(2026, 10, 26)),
        ("2026-11-09T06:00:00Z", date(2026, 11, 2)),
        # Sunday late evening local is the previous week even though UTC says Monday.
        ("2026-11-02T05:30:00Z", date(2026, 10, 19)),
    ],
)
def test_scorecard_week_is_the_week_before_the_meeting_week(started_at: str, week: date) -> None:
    assert scorecard_week_for(utc(started_at)) == week
    assert week.weekday() == 0


def test_scorecard_week_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        scorecard_week_for(datetime(2026, 10, 28, 15, 0))


# --- agenda ------------------------------------------------------------------------------------------


def test_agenda_is_scorecard_5_ids_60_conclude_10_minutes() -> None:
    assert AGENDA == (
        ("scorecard", "Scorecard", 5 * 60),
        ("ids", "IDS", 60 * 60),
        ("conclude", "Conclude", 10 * 60),
    )


@pytest.mark.parametrize(
    ("section", "following"),
    [("scorecard", "ids"), ("ids", "conclude"), ("conclude", None)],
)
def test_next_section_walks_the_agenda_and_stops_after_conclude(section: str, following: str | None) -> None:
    assert next_section(section) == following


def test_next_section_rejects_an_unknown_section() -> None:
    with pytest.raises(ValueError):
        next_section("lunch")


@pytest.mark.parametrize(("section", "seconds"), [("scorecard", 300), ("ids", 3600), ("conclude", 600)])
def test_section_duration_matches_the_agenda(section: str, seconds: int) -> None:
    assert section_duration_s(section) == seconds


def test_section_duration_rejects_an_unknown_section() -> None:
    with pytest.raises(ValueError):
        section_duration_s("lunch")


# --- section_timing ----------------------------------------------------------------------------------

STARTED = utc("2026-11-11T15:00:00Z")


@pytest.mark.parametrize(
    ("offset_s", "elapsed", "over"),
    [
        (0, 0, False),
        (1, 1, False),
        (299, 299, False),
        (300, 300, False),  # exactly the planned duration is not over time
        (301, 301, True),  # one second more is
        (3600, 3600, True),
    ],
)
def test_over_time_means_elapsed_is_strictly_greater_than_the_duration(
    offset_s: int, elapsed: int, over: bool
) -> None:
    assert section_timing(STARTED, STARTED + timedelta(seconds=offset_s), 300) == (elapsed, over)


def test_elapsed_never_goes_negative() -> None:
    assert section_timing(STARTED, STARTED - timedelta(seconds=30), 300) == (0, False)


def test_elapsed_is_whole_seconds_floored() -> None:
    assert section_timing(STARTED, STARTED + timedelta(seconds=300, milliseconds=999), 300) == (300, False)
    assert section_timing(STARTED, STARTED + timedelta(seconds=301, milliseconds=1), 300) == (301, True)


def test_timing_works_across_offsets_and_the_ids_section_boundary() -> None:
    assert section_timing(STARTED, STARTED + timedelta(seconds=3600), 3600) == (3600, False)
    assert section_timing(STARTED, STARTED + timedelta(seconds=3601), 3600) == (3601, True)


def test_timing_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        section_timing(datetime(2026, 11, 11, 15, 0), STARTED, 300)
    with pytest.raises(ValueError, match="timezone-aware"):
        section_timing(STARTED, datetime(2026, 11, 11, 15, 0), 300)
