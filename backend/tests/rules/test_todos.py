"""To-Do completion rate and overdue flag (brief 2.6 "To-Do completion rate" and "overdue")."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.rules.todos import Completion, TodoFacts, is_overdue, todo_completion

WEEK = date(2026, 11, 2)  # Mon 2026-11-02 .. Sun 2026-11-08, ends 2026-11-09T06:00:00Z (CST)
DST_WEEK = date(2026, 10, 26)  # contains the fall-back change, ends 2026-11-02T06:00:00Z


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def todo(todo_id: str, due: str, done_at: str | None = None) -> TodoFacts:
    return TodoFacts(todo_id, date.fromisoformat(due), utc(done_at) if done_at else None)


def batch(due_day: str, done: int, total: int, done_at: str = "2026-11-03T15:00:00Z") -> list[TodoFacts]:
    return [todo(f"x{n}", due_day, done_at if n < done else None) for n in range(total)]


# --- target: exactly 90% meets, 89.x% does not --------------------------------------------------------


def test_exactly_ninety_percent_meets_the_target() -> None:
    result = todo_completion(batch("2026-11-04", done=9, total=10), WEEK)
    assert result == Completion(due=10, done=9, rate_percent=90, meets_target=True)


@pytest.mark.parametrize(
    ("done", "total", "rate", "meets"),
    [
        (8, 10, 80, False),
        (8, 9, 89, False),  # 88.9%
        (17, 19, 89, False),  # 89.47%
        (10, 10, 100, True),
        (10, 11, 91, True),  # 90.9%
        (18, 20, 90, True),  # exactly 90% again with a different denominator
        (0, 4, 0, False),
        (1, 8, 13, False),  # 12.5 rounds up to 13
        (2, 3, 67, False),
    ],
)
def test_rate_is_half_up_and_target_compares_the_exact_ratio(
    done: int, total: int, rate: int, meets: bool
) -> None:
    result = todo_completion(batch("2026-11-04", done=done, total=total), WEEK)
    assert result == Completion(due=total, done=done, rate_percent=rate, meets_target=meets)


def test_899_of_1000_displays_90_but_does_not_meet_the_target() -> None:
    """The trap: rounding 89.9% to a whole 90 must not make the target look met."""
    result = todo_completion(batch("2026-11-04", done=899, total=1000), WEEK)
    assert result is not None
    assert result.rate_percent == 90
    assert result.meets_target is False
    result = todo_completion(batch("2026-11-04", done=900, total=1000), WEEK)
    assert result is not None and result.meets_target is True


# --- done after its due week counts as not done ------------------------------------------------------


def test_done_just_after_the_due_week_ends_does_not_count() -> None:
    # seed t29: due Sunday 2026-11-08, done 2026-11-09T06:01Z = Monday 00:01 local
    result = todo_completion([todo("t29", "2026-11-08", "2026-11-09T06:01:00Z")], WEEK)
    assert result == Completion(due=1, done=0, rate_percent=0, meets_target=False)


def test_done_in_the_last_minute_of_the_week_counts() -> None:
    # seed t28: due 2026-11-06, done 2026-11-09T05:59Z = Sunday 23:59 local
    result = todo_completion([todo("t28", "2026-11-06", "2026-11-09T05:59:00Z")], WEEK)
    assert result == Completion(due=1, done=1, rate_percent=100, meets_target=True)


@pytest.mark.parametrize(
    ("done_at", "counts"),
    [
        ("2026-11-09T05:59:59Z", True),  # last second of Sunday local time
        ("2026-11-09T06:00:00Z", False),  # the end is exclusive: Monday 00:00:00 local
        ("2026-11-09T06:00:01Z", False),
    ],
)
def test_week_end_is_an_exclusive_instant_in_utc(done_at: str, counts: bool) -> None:
    result = todo_completion([todo("a", "2026-11-08", done_at)], WEEK)
    assert result is not None
    assert result.done == (1 if counts else 0)


def test_seed_week_2026_11_02_is_exactly_ninety_percent_with_t28_in_and_t29_out() -> None:
    todos = [todo(f"ok{n}", "2026-11-04", "2026-11-04T18:00:00Z") for n in range(7)]
    todos += [
        todo("t28", "2026-11-06", "2026-11-09T05:59:00Z"),
        todo("t11b", "2026-11-03", "2026-11-03T16:00:00Z"),
        todo("t29", "2026-11-08", "2026-11-09T06:01:00Z"),
    ]
    assert todo_completion(todos, WEEK) == Completion(due=10, done=9, rate_percent=90, meets_target=True)


# --- DST ---------------------------------------------------------------------------------------------


def test_dst_week_counts_a_todo_done_at_0530z_monday_because_that_is_sunday_evening_local() -> None:
    # seed t11: due 2026-10-30, done 2026-11-02T05:30Z = Sunday 23:30 CST. The week ends at 06:00Z
    # (not 05:00Z) because the clocks fell back inside that week.
    todos = [
        todo("t11", "2026-10-30", "2026-11-02T05:30:00Z"),
        todo("on_time", "2026-10-28", "2026-10-28T20:00:00Z"),
        todo("never", "2026-10-27"),
    ]
    result = todo_completion(todos, DST_WEEK)
    assert result == Completion(due=3, done=2, rate_percent=67, meets_target=False)


def test_dst_week_still_excludes_done_at_or_after_0600z() -> None:
    assert todo_completion([todo("a", "2026-10-30", "2026-11-02T06:00:00Z")], DST_WEEK) == Completion(
        due=1, done=0, rate_percent=0, meets_target=False
    )


# --- which todos are due that week -------------------------------------------------------------------


def test_due_range_is_monday_to_sunday_inclusive() -> None:
    todos = [
        todo("sun_before", "2026-11-01", "2026-11-01T20:00:00Z"),
        todo("mon", "2026-11-02", "2026-11-02T20:00:00Z"),
        todo("sun", "2026-11-08", "2026-11-08T20:00:00Z"),
        todo("mon_after", "2026-11-09", "2026-11-09T20:00:00Z"),
    ]
    assert todo_completion(todos, WEEK) == Completion(due=2, done=2, rate_percent=100, meets_target=True)


def test_done_late_but_within_the_same_week_counts() -> None:
    # due Monday, finished Friday of the same week
    result = todo_completion([todo("a", "2026-11-02", "2026-11-06T21:00:00Z")], WEEK)
    assert result == Completion(due=1, done=1, rate_percent=100, meets_target=True)


def test_done_before_the_week_started_still_counts_as_done() -> None:
    result = todo_completion([todo("a", "2026-11-05", "2026-10-20T15:00:00Z")], WEEK)
    assert result is not None and result.done == 1


def test_undone_todo_counts_as_due_but_not_done() -> None:
    result = todo_completion([todo("a", "2026-11-04"), todo("b", "2026-11-04", "2026-11-04T15:00:00Z")], WEEK)
    assert result == Completion(due=2, done=1, rate_percent=50, meets_target=False)


@pytest.mark.parametrize("todos", [[], [todo("a", "2026-11-01")], [todo("a", "2026-11-09")]])
def test_zero_due_means_no_rate(todos: list[TodoFacts]) -> None:
    assert todo_completion(todos, WEEK) is None


def test_accepts_any_iterable() -> None:
    gen = (todo(f"g{n}", "2026-11-04", "2026-11-04T15:00:00Z") for n in range(3))
    assert todo_completion(gen, WEEK) == Completion(due=3, done=3, rate_percent=100, meets_target=True)


def test_naive_done_at_is_rejected() -> None:
    naive = TodoFacts("a", date(2026, 11, 4), datetime(2026, 11, 4, 15, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        todo_completion([naive], WEEK)


# --- is_overdue --------------------------------------------------------------------------------------

TODAY = date(2026, 11, 11)


@pytest.mark.parametrize(
    ("due", "done_at", "expected"),
    [
        (date(2026, 11, 10), None, True),  # due yesterday, not done (seed t31)
        (date(2026, 11, 1), None, True),  # seed t12
        (date(2026, 11, 11), None, False),  # due today is not overdue
        (date(2026, 11, 12), None, False),  # due tomorrow
        (date(2026, 11, 10), utc("2026-11-10T15:00:00Z"), False),
        (date(2026, 10, 1), utc("2026-11-11T14:00:00Z"), False),
    ],
)
def test_overdue_means_not_done_and_due_before_today(
    due: date, done_at: datetime | None, expected: bool
) -> None:
    assert is_overdue(due, done_at, TODAY) is expected
