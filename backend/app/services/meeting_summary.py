"""The `MeetingSummary` snapshot (PLAN section 6), written when a meeting ends and for seeded meetings.

One function builds it for both, so the report, the API and the seed can never disagree. All business math is
delegated to `app.rules`.
"""

from datetime import UTC, date, datetime
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Issue, Measurable, Meeting, MeetingAttendee, MeetingRating, Score, Todo, User
from app.rules import Goal, GoalType, company_date, measurable_status, meeting_rating, week_start_of


def iso_z(ts: datetime) -> str:
    """ISO-8601 UTC with a trailing `Z`."""
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _user_ref(user: User) -> dict[str, str]:
    return {"id": user.id, "name": user.name, "role": user.role}


def _goal_of(measurable: Measurable) -> Goal:
    return Goal(
        type=cast(GoalType, measurable.goal_type),
        value=measurable.goal_value,
        min=measurable.goal_min,
        max=measurable.goal_max,
    )


async def build_meeting_summary(
    session: AsyncSession, meeting: Meeting, *, until: datetime | None = None
) -> dict[str, Any]:
    """Summary of `meeting`, as JSON-ready primitives.

    * measurable status: each team measurable for `meeting.scorecard_week`, evaluated with
      `current_week` = the week the meeting started in (so the reviewed week is always closed);
    * issues solved / to-dos created by the team within `[started_at, until]` inclusive, where `until`
      defaults to `meeting.ended_at` (pass it to summarize a meeting that has not been closed yet);
    * attendees and the rating (average of submitted ratings, one decimal, half up; null if nobody rated).

    Id lists are sorted as plain strings (`t12` before `t3`); measurables follow scorecard order.
    """
    window_end = until or meeting.ended_at
    if window_end is None:
        raise ValueError("meeting has no ended_at; pass `until` to summarize an active meeting")
    window_start = meeting.started_at
    team_id = meeting.team_id
    current_week: date = week_start_of(company_date(window_start))

    measurables = list(
        await session.scalars(
            select(Measurable)
            .where(Measurable.team_id == team_id)
            .order_by(Measurable.sort_order, Measurable.id)
        )
    )
    values = {
        row.measurable_id: row.value
        for row in await session.execute(
            select(Score.measurable_id, Score.value).where(
                Score.week_start == meeting.scorecard_week,
                Score.measurable_id.in_([m.id for m in measurables]),
            )
        )
    }
    measurable_status_rows = []
    for m in measurables:
        value = values.get(m.id)
        measurable_status_rows.append(
            {
                "measurable_id": m.id,
                "name": m.name,
                "value": None if value is None else float(value),
                "status": measurable_status(_goal_of(m), value, meeting.scorecard_week, current_week),
            }
        )

    solved = await session.execute(
        select(Issue.id, Issue.title).where(
            Issue.team_id == team_id,
            Issue.solved_at.is_not(None),
            Issue.solved_at >= window_start,
            Issue.solved_at <= window_end,
        )
    )
    issues_solved = [{"id": r.id, "title": r.title} for r in sorted(solved, key=lambda r: r.id)]

    created = await session.execute(
        select(Todo, User)
        .join(User, User.id == Todo.owner_id)
        .where(Todo.team_id == team_id, Todo.created_at >= window_start, Todo.created_at <= window_end)
    )
    todos_created = [
        {"id": todo.id, "title": todo.title, "owner": _user_ref(owner), "due_date": todo.due_date.isoformat()}
        for todo, owner in sorted(created.all(), key=lambda pair: pair[0].id)
    ]

    attendee_users = await session.scalars(
        select(User)
        .join(MeetingAttendee, MeetingAttendee.user_id == User.id)
        .where(MeetingAttendee.meeting_id == meeting.id)
    )
    attendees = [_user_ref(u) for u in sorted(attendee_users, key=lambda u: u.id)]

    ratings = list(
        await session.scalars(select(MeetingRating.rating).where(MeetingRating.meeting_id == meeting.id))
    )
    average = meeting_rating(ratings)

    return {
        "meeting_id": meeting.id,
        "team_id": team_id,
        "scorecard_week": meeting.scorecard_week.isoformat(),
        "started_at": iso_z(window_start),
        "ended_at": iso_z(window_end),
        "measurable_status": measurable_status_rows,
        "issues_solved": issues_solved,
        "todos_created": todos_created,
        "attendees": attendees,
        "rating": None if average is None else float(average),
    }
