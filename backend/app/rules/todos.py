"""To-Do rules: weekly completion rate (target 90%) and overdue flag."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo

from app.rules.timeutil import COMPANY_TZ, percent, require_aware, week_bounds_utc

TARGET_PERCENT = 90


@dataclass(frozen=True)
class TodoFacts:
    id: str
    due_date: date
    done_at: datetime | None


@dataclass(frozen=True)
class Completion:
    due: int
    done: int
    rate_percent: int
    meets_target: bool


def todo_completion(
    todos: Iterable[TodoFacts], week_start: date, tz: tzinfo = COMPANY_TZ
) -> Completion | None:
    """Completion of the To-Dos due in the week starting `week_start` (Monday).

    - Due that week: `week_start <= due_date <= week_start + 6 days` (company calendar dates).
    - Done: `done_at` is before the week's exclusive end instant (UTC, per-week DST offset). A To-Do done
      after its due week counts as not done; one done early counts as done.
    - `rate_percent` is half-up. `meets_target` compares the exact ratio (`done * 10 >= due * 9`), so
      exactly 90% meets the target while 89.9% does not, whatever the rounding says.
    - Zero due means no rate: returns None.
    """
    _, week_end = week_bounds_utc(week_start, tz)
    last_day = week_start + timedelta(days=6)
    due = 0
    done = 0
    for todo in todos:
        if not week_start <= todo.due_date <= last_day:
            continue
        due += 1
        if todo.done_at is not None and require_aware(todo.done_at, "done_at") < week_end:
            done += 1
    if due == 0:
        return None
    return Completion(
        due=due,
        done=done,
        rate_percent=percent(done, due),
        meets_target=done * 100 >= due * TARGET_PERCENT,
    )


def is_overdue(due_date: date, done_at: datetime | None, today: date) -> bool:
    """Overdue = not done and the due date is strictly before today (due today is not overdue)."""
    return done_at is None and due_date < today
