"""Rules report: `python -m app.report [--now ISO] [--live]` (PLAN section 8, format in the starter README).

Prints every value of brief section 2.6 as JSON (`indent=2`):

* default (fresh): ONE transaction that truncates, loads `seed/seed.json`, computes, prints and ROLLS BACK, so
  the output always describes a freshly seeded database and live data is never touched;
* `--live`: reports the current database as it is;
* `--now ISO` overrides `APP_NOW` (the clock is always a `FrozenClock`; the system clock is never read).

Values come from the same services the API serves (`scorecard`, `todos`, `rocks`, `issues`) and from
`build_meeting_summary` (the function that writes the snapshot when a meeting ends), so the report cannot
drift from the app.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import FrozenClock
from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.models import Meeting, Team
from app.rules import TimeContext
from app.seed import load_seed
from app.services import issues, rocks, scorecard, todos
from app.services.meeting_summary import build_meeting_summary


def parse_now(value: str) -> datetime:
    """ISO-8601 instant (a trailing `Z` is fine); a naive value is taken as UTC."""
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


async def _team_report(session: AsyncSession, tc: TimeContext, team_id: str) -> dict[str, Any]:
    grid = await scorecard.get_scorecard(session, tc, team_id)
    completion = await todos.completion_window(session, tc, team_id)
    todo_list = await todos.list_todos(session, tc, team_id)
    rock_list = await rocks.list_rocks(session, tc, team_id)
    unsolved = await issues.list_issues(session, team_id, "unsolved")

    meetings: dict[str, Any] = {}
    ended = await session.scalars(
        select(Meeting).where(Meeting.team_id == team_id, Meeting.ended_at.is_not(None))
    )
    for meeting in sorted(ended, key=lambda m: m.id):
        summary = await build_meeting_summary(session, meeting)
        meetings[meeting.id] = {
            "scorecard_week": summary["scorecard_week"],
            "measurable_status": {
                e["measurable_id"]: e["status"]
                for e in sorted(summary["measurable_status"], key=lambda e: e["measurable_id"])
            },
            "issues_solved": sorted(i["id"] for i in summary["issues_solved"]),
            "todos_created": sorted(t["id"] for t in summary["todos_created"]),
            "attendee_ids": sorted(a["id"] for a in summary["attendees"]),
            "rating": summary["rating"],
        }

    return {
        "measurable_status": {
            m.id: {cell.week_start.isoformat(): cell.status for cell in m.cells}
            for m in sorted(grid.measurables, key=lambda m: m.id)
        },
        "todo_completion": {
            week.isoformat(): None
            if rate is None
            else {
                "due": rate.due,
                "done": rate.done,
                "rate_percent": rate.rate_percent,
                "meets_target": rate.meets_target,
            }
            for week, rate in completion.items()
        },
        "overdue_todos": sorted(t.id for t in todo_list.todos if t.overdue),
        "rocks": {
            r.id: {"status": r.status, "progress_percent": r.progress_percent}
            for r in sorted(rock_list.rocks, key=lambda r: r.id)
        },
        "issue_order": [i.id for i in unsolved],
        "meetings": meetings,
    }


async def build_report(session: AsyncSession, now: datetime) -> dict[str, Any]:
    """The report for the database visible to `session`, at the business time `now`."""
    clock = FrozenClock(now)
    tc = TimeContext.at(clock.now())
    team_ids = sorted(await session.scalars(select(Team.id)))
    return {
        "now": tc.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "teams": {team_id: await _team_report(session, tc, team_id) for team_id in team_ids},
    }


async def generate_report(
    session_factory: async_sessionmaker[AsyncSession], seed_path: Path, now: datetime, *, live: bool = False
) -> dict[str, Any]:
    """Fresh mode seeds inside a transaction that is always rolled back; live mode only reads."""
    async with session_factory() as session:
        try:
            if not live:
                await load_seed(session, seed_path, reset=True)
            return await build_report(session, now)
        finally:
            await session.rollback()  # never commit: fresh mode must leave the live data untouched


async def _run(now: datetime | None, live: bool) -> dict[str, Any]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        return await generate_report(
            create_session_factory(engine), settings.seed_path, now or settings.app_now, live=live
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.report", description="Print the rules report (brief section 2.6) as JSON."
    )
    parser.add_argument(
        "--now", type=parse_now, default=None, help="business time ISO-8601 (default APP_NOW)"
    )
    parser.add_argument(
        "--live", action="store_true", help="report the current database instead of a freshly seeded one"
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args.now, args.live)), indent=2))


if __name__ == "__main__":
    main()
