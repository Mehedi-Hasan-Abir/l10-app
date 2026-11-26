"""Time, week math and rounding rules (PLAN section 2, brief 2.6 "Time" and "Round half up")."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.rules.timeutil import (
    COMPANY_TZ,
    TimeContext,
    company_date,
    percent,
    quarter_of,
    round_half_up,
    scorecard_weeks,
    week_bounds_utc,
    week_start_of,
)


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_company_timezone_is_chicago() -> None:
    assert COMPANY_TZ.key == "America/Chicago"


# --- week_start_of -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "monday"),
    [
        (date(2026, 11, 9), date(2026, 11, 9)),  # Monday maps to itself
        (date(2026, 11, 10), date(2026, 11, 9)),  # Tuesday
        (date(2026, 11, 11), date(2026, 11, 9)),  # Wednesday (today at the default clock)
        (date(2026, 11, 15), date(2026, 11, 9)),  # Sunday belongs to the week that started Monday
        (date(2026, 11, 8), date(2026, 11, 2)),  # the Sunday before belongs to the previous week
        (date(2026, 11, 2), date(2026, 11, 2)),
        (date(2026, 1, 1), date(2025, 12, 29)),  # crosses the year boundary
    ],
)
def test_week_start_of_returns_the_monday_of_the_mon_to_sun_week(day: date, monday: date) -> None:
    assert week_start_of(day) == monday
    assert week_start_of(day).weekday() == 0


# --- week_bounds_utc ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("week", "start", "end"),
    [
        # Week containing the fall-back change (Sun 2026-11-01 02:00 CDT -> 01:00 CST): ends at 06:00Z.
        (date(2026, 10, 26), "2026-10-26T05:00:00Z", "2026-11-02T06:00:00Z"),
        # The week after is entirely CST: starts and ends at 06:00Z.
        (date(2026, 11, 2), "2026-11-02T06:00:00Z", "2026-11-09T06:00:00Z"),
        (date(2026, 11, 9), "2026-11-09T06:00:00Z", "2026-11-16T06:00:00Z"),
        # A summer week is CDT (UTC-5): 05:00Z.
        (date(2026, 7, 6), "2026-07-06T05:00:00Z", "2026-07-13T05:00:00Z"),
        # Spring-forward week (Sun 2026-03-08): starts 06:00Z (CST), ends 05:00Z (CDT).
        (date(2026, 3, 2), "2026-03-02T06:00:00Z", "2026-03-09T05:00:00Z"),
    ],
)
def test_week_bounds_utc_resolve_the_offset_per_boundary_across_dst(week: date, start: str, end: str) -> None:
    assert week_bounds_utc(week) == (utc(start), utc(end))


def test_week_bounds_are_aware_utc_and_half_open_lengths_follow_dst() -> None:
    start, end = week_bounds_utc(date(2026, 10, 26))
    assert start.utcoffset() == timedelta(0) and end.utcoffset() == timedelta(0)
    assert end - start == timedelta(days=7, hours=1)  # fall-back week has 169 hours
    start, end = week_bounds_utc(date(2026, 3, 2))
    assert end - start == timedelta(days=7) - timedelta(hours=1)  # spring-forward week has 167 hours


def test_week_bounds_end_is_the_exclusive_start_of_the_next_week() -> None:
    assert week_bounds_utc(date(2026, 10, 26))[1] == week_bounds_utc(date(2026, 11, 2))[0]


def test_week_bounds_reject_a_date_that_is_not_a_monday() -> None:
    with pytest.raises(ValueError, match="Monday"):
        week_bounds_utc(date(2026, 11, 10))


# --- company_date ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("instant", "local_day"),
    [
        ("2026-11-11T15:00:00Z", date(2026, 11, 11)),  # the default clock
        ("2026-11-11T05:59:59Z", date(2026, 11, 10)),  # 23:59:59 CST the evening before
        ("2026-11-11T06:00:00Z", date(2026, 11, 11)),  # local midnight CST
        ("2026-07-01T04:59:59Z", date(2026, 6, 30)),  # CDT: local midnight is 05:00Z
        ("2026-07-01T05:00:00Z", date(2026, 7, 1)),
        ("2026-11-01T05:30:00Z", date(2026, 11, 1)),  # 00:30 CDT on the fall-back day
        ("2026-11-02T05:30:00Z", date(2026, 11, 1)),  # seed t11: 23:30 CST Sunday, not Monday
        ("2026-11-09T05:59:00Z", date(2026, 11, 8)),  # seed t28
        ("2026-11-09T06:01:00Z", date(2026, 11, 9)),  # seed t29
    ],
)
def test_company_date_is_the_chicago_calendar_date(instant: str, local_day: date) -> None:
    assert company_date(utc(instant)) == local_day


def test_company_date_accepts_any_aware_offset() -> None:
    tokyo = timezone(timedelta(hours=9))
    assert company_date(datetime(2026, 11, 11, 20, 0, tzinfo=tokyo)) == date(2026, 11, 11)  # 11:00Z


def test_company_date_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        company_date(datetime(2026, 11, 11, 15, 0))


# --- scorecard_weeks ---------------------------------------------------------------------------------


def test_scorecard_weeks_is_13_weeks_oldest_first_ending_with_the_current_week() -> None:
    weeks = scorecard_weeks(date(2026, 11, 9))
    assert len(weeks) == 13
    assert weeks[0] == date(2026, 8, 17)
    assert weeks[-1] == date(2026, 11, 9)
    assert weeks == sorted(weeks)
    assert all(w.weekday() == 0 for w in weeks)
    assert all(b - a == timedelta(days=7) for a, b in zip(weeks, weeks[1:], strict=False))


@pytest.mark.parametrize("n", [0, 1, 4, 13, 26])
def test_scorecard_weeks_honours_the_requested_length(n: int) -> None:
    weeks = scorecard_weeks(date(2026, 11, 9), n)
    assert len(weeks) == n
    if n:
        assert weeks[-1] == date(2026, 11, 9)


def test_scorecard_weeks_rejects_a_negative_length() -> None:
    with pytest.raises(ValueError):
        scorecard_weeks(date(2026, 11, 9), -1)


# --- quarter_of --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "label"),
    [
        (date(2026, 1, 1), "2026-Q1"),
        (date(2026, 3, 31), "2026-Q1"),
        (date(2026, 4, 1), "2026-Q2"),
        (date(2026, 6, 30), "2026-Q2"),
        (date(2026, 7, 1), "2026-Q3"),
        (date(2026, 9, 30), "2026-Q3"),
        (date(2026, 10, 1), "2026-Q4"),
        (date(2026, 11, 11), "2026-Q4"),
        (date(2026, 12, 31), "2026-Q4"),
        (date(2027, 1, 1), "2027-Q1"),
    ],
)
def test_quarter_of_is_the_calendar_quarter(day: date, label: str) -> None:
    assert quarter_of(day) == label


# --- TimeContext -------------------------------------------------------------------------------------


def test_time_context_at_the_default_clock() -> None:
    ctx = TimeContext.at(utc("2026-11-11T15:00:00Z"))
    assert ctx.now == utc("2026-11-11T15:00:00Z")
    assert ctx.today == date(2026, 11, 11)
    assert ctx.current_week == date(2026, 11, 9)
    assert ctx.last_closed_week == date(2026, 11, 2)
    assert ctx.quarter == "2026-Q4"


@pytest.mark.parametrize(
    ("now", "today", "current_week", "last_closed_week", "quarter"),
    [
        # Sunday 23:59:59 local is still the old week; one second later it is the new week.
        ("2026-11-09T05:59:59Z", date(2026, 11, 8), date(2026, 11, 2), date(2026, 10, 26), "2026-Q4"),
        ("2026-11-09T06:00:00Z", date(2026, 11, 9), date(2026, 11, 9), date(2026, 11, 2), "2026-Q4"),
        # Quarter rolls over at local midnight (CDT on 1 Oct), not at midnight UTC.
        ("2026-10-01T04:59:59Z", date(2026, 9, 30), date(2026, 9, 28), date(2026, 9, 21), "2026-Q3"),
        ("2026-10-01T05:00:00Z", date(2026, 10, 1), date(2026, 9, 28), date(2026, 9, 21), "2026-Q4"),
        # Late-evening UTC is still the previous local day.
        ("2026-12-02T05:59:00Z", date(2026, 12, 1), date(2026, 11, 30), date(2026, 11, 23), "2026-Q4"),
        ("2026-11-18T15:00:00Z", date(2026, 11, 18), date(2026, 11, 16), date(2026, 11, 9), "2026-Q4"),
    ],
)
def test_time_context_follows_company_local_time(
    now: str, today: date, current_week: date, last_closed_week: date, quarter: str
) -> None:
    ctx = TimeContext.at(utc(now))
    assert (ctx.today, ctx.current_week, ctx.last_closed_week, ctx.quarter) == (
        today,
        current_week,
        last_closed_week,
        quarter,
    )


def test_time_context_normalizes_now_to_utc() -> None:
    tokyo = timezone(timedelta(hours=9))
    ctx = TimeContext.at(datetime(2026, 11, 12, 0, 0, tzinfo=tokyo))  # 2026-11-11T15:00Z
    assert ctx.now.utcoffset() == timedelta(0)
    assert ctx.now == datetime(2026, 11, 11, 15, 0, tzinfo=UTC)
    assert ctx.today == date(2026, 11, 11)


def test_time_context_rejects_naive_now_and_is_immutable() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        TimeContext.at(datetime(2026, 11, 11, 15, 0))
    ctx = TimeContext.at(utc("2026-11-11T15:00:00Z"))
    with pytest.raises(FrozenInstanceError):
        ctx.today = date(2026, 1, 1)  # type: ignore[misc]


# --- round_half_up and percent -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "places", "expected"),
    [
        ("12.5", 0, "13"),
        ("12.4999", 0, "12"),
        ("66.66666666666666666666666667", 0, "67"),
        ("8.25", 1, "8.3"),
        ("8.24", 1, "8.2"),
        ("8.35", 1, "8.4"),
        ("0.5", 0, "1"),
        ("1.5", 0, "2"),
        ("2.5", 0, "3"),  # banker's rounding would give 2
        ("-2.5", 0, "-3"),  # ties go away from zero
        ("2.675", 2, "2.68"),  # float round() gives 2.67
        ("7", 1, "7.0"),
    ],
)
def test_round_half_up_rounds_ties_up_with_decimal(value: str, places: int, expected: str) -> None:
    result = round_half_up(Decimal(value), places)
    assert result == Decimal(expected)
    assert str(result) == expected


def test_round_half_up_accepts_ints_and_rejects_floats() -> None:
    assert round_half_up(5) == Decimal(5)
    with pytest.raises(TypeError):
        round_half_up(8.25)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        (1, 8, 13),  # 12.5 -> 13 (seed r2)
        (1, 3, 33),
        (2, 3, 67),  # seed t11 week
        (3, 8, 38),  # 37.5
        (5, 8, 63),  # 62.5
        (9, 10, 90),
        (0, 7, 0),
        (7, 7, 100),
        (1, 200, 1),  # 0.5
        (1, 201, 0),  # 0.4975
        (89, 100, 89),
    ],
)
def test_percent_is_half_up(numerator: int, denominator: int, expected: int) -> None:
    result = percent(numerator, denominator)
    assert result == expected
    assert isinstance(result, int)


@pytest.mark.parametrize(("numerator", "denominator"), [(1, 0), (1, -3), (-1, 5)])
def test_percent_rejects_a_non_positive_denominator_or_negative_numerator(
    numerator: int, denominator: int
) -> None:
    with pytest.raises(ValueError):
        percent(numerator, denominator)
