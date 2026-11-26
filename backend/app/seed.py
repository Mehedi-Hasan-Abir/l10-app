"""Seed loader: `python -m app.seed [--reset]` (PLAN section 4).

* One transaction; every row is inserted with `ON CONFLICT DO NOTHING`, so rerunning never clobbers data the
  app created and never fails.
* `--reset` truncates every table first (including `app_clock`), then loads.
* `load_seed` does not commit; the CLI does. (The rules report calls it inside a transaction it rolls back.)
"""

import argparse
import asyncio
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.models import (
    Base,
    Company,
    Issue,
    Measurable,
    Meeting,
    MeetingAttendee,
    MeetingRating,
    Membership,
    Milestone,
    Rock,
    Score,
    Team,
    Todo,
    User,
)
from app.rules import scorecard_week_for
from app.services.meeting_summary import build_meeting_summary


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _opt_ts(value: str | None) -> datetime | None:
    return None if value is None else _ts(value)


def load_seed_file(seed_path: Path) -> dict[str, Any]:
    """Parse the seed JSON with floats as `Decimal` (no binary float ever touches a score or goal)."""
    return json.loads(Path(seed_path).read_text(encoding="utf-8"), parse_float=Decimal)


async def _insert(session: AsyncSession, model: type[Base], rows: list[dict[str, Any]]) -> int:
    """Insert rows, skipping conflicts. Returns how many were actually inserted."""
    if not rows:
        return 0
    result = await session.execute(pg_insert(model).values(rows).on_conflict_do_nothing())
    return result.rowcount or 0


def _issue_source(source: dict[str, Any] | None) -> dict[str, Any]:
    columns: dict[str, Any] = {
        "source_type": None,
        "source_measurable_id": None,
        "source_week_start": None,
        "source_rock_id": None,
        "source_todo_id": None,
    }
    if source is None:
        return columns
    if "measurable_id" in source:
        columns.update(
            source_type="measurable",
            source_measurable_id=source["measurable_id"],
            source_week_start=date.fromisoformat(source["week_start"]),
        )
    elif "rock_id" in source:
        columns.update(source_type="rock", source_rock_id=source["rock_id"])
    elif "todo_id" in source:
        columns.update(source_type="todo", source_todo_id=source["todo_id"])
    else:
        raise ValueError(f"unrecognized issue source: {source!r}")
    return columns


async def _truncate_all(session: AsyncSession) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    await session.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    session.expire_all()


async def load_seed(session: AsyncSession, seed_path: Path, *, reset: bool) -> dict[str, int]:
    """Load the seed into the current transaction (never commits).

    Returns the number of rows inserted per table (rows that already existed are not counted).
    """
    seed = load_seed_file(seed_path)
    if reset:
        await _truncate_all(session)

    seeded_at = _ts(seed["meta"]["now"])
    company = seed["company"]
    counts: dict[str, int] = {}

    counts["companies"] = await _insert(session, Company, [company])
    counts["teams"] = await _insert(
        session, Team, [{**t, "company_id": company["id"]} for t in seed["teams"]]
    )
    counts["users"] = await _insert(
        session, User, [{**u, "company_id": company["id"]} for u in seed["users"]]
    )
    counts["memberships"] = await _insert(session, Membership, seed["memberships"])

    counts["measurables"] = await _insert(
        session,
        Measurable,
        [
            {
                "id": m["id"],
                "team_id": m["team_id"],
                "name": m["name"],
                "owner_id": m["owner_id"],
                "unit": m["unit"],
                "goal_type": m["goal"]["type"],
                "goal_value": m["goal"].get("value"),
                "goal_min": m["goal"].get("min"),
                "goal_max": m["goal"].get("max"),
                "sort_order": i,
            }
            for i, m in enumerate(seed["measurables"])
        ],
    )
    counts["scores"] = await _insert(
        session,
        Score,
        [
            {
                "measurable_id": s["measurable_id"],
                "week_start": date.fromisoformat(s["week_start"]),
                "value": s["value"],
                "version": 1,
                "updated_at": seeded_at,
                "updated_by": None,
            }
            for s in seed["scores"]
        ],
    )
    counts["rocks"] = await _insert(
        session,
        Rock,
        [
            {
                "id": r["id"],
                "team_id": r["team_id"],
                "title": r["title"],
                "owner_id": r["owner_id"],
                "quarter": r["quarter"],
                "seeded_status": r["status"],
                "sort_order": i,
            }
            for i, r in enumerate(seed["rocks"])
        ],
    )
    counts["milestones"] = await _insert(
        session,
        Milestone,
        [
            {
                "id": ms["id"],
                "rock_id": ms["rock_id"],
                "title": ms["title"],
                "due_date": date.fromisoformat(ms["due_date"]),
                "done": ms["done"],
                "sort_order": i,
            }
            for i, ms in enumerate(seed["milestones"])
        ],
    )
    counts["issues"] = await _insert(
        session,
        Issue,
        [
            {
                "id": i["id"],
                "team_id": i["team_id"],
                "title": i["title"],
                "owner_id": i["owner_id"],
                "priority": i["priority"],
                "version": 1,
                "created_at": _ts(i["created_at"]),
                "solved_at": _opt_ts(i["solved_at"]),
                "resolution_note": i["resolution_note"],
                **_issue_source(i.get("source")),
            }
            for i in seed["issues"]
        ],
    )
    counts["todos"] = await _insert(
        session,
        Todo,
        [
            {
                "id": t["id"],
                "team_id": t["team_id"],
                "title": t["title"],
                "owner_id": t["owner_id"],
                "due_date": date.fromisoformat(t["due_date"]),
                "done_at": _opt_ts(t["done_at"]),
                "source_issue_id": t["source_issue_id"],
                "created_at": _ts(t["created_at"]),
            }
            for t in seed["todos"]
        ],
    )

    # Meetings. A finished meeting must carry a summary (CHECK: summary IS NULL = ended_at IS NULL), but the
    # summary needs the attendees/ratings, which reference the meeting row. So: insert the meeting with an
    # empty-object placeholder, add attendees and ratings, then replace the placeholder with the real summary
    # (same transaction, same function the app uses when a meeting ends). Only newly inserted meetings are
    # touched, so a rerun never rewrites a snapshot.
    meetings = seed["meetings"]
    result = await session.execute(
        pg_insert(Meeting)
        .values(
            [
                {
                    "id": m["id"],
                    "team_id": m["team_id"],
                    "facilitator_id": m["facilitator_id"],
                    "started_at": _ts(m["started_at"]),
                    "ended_at": _opt_ts(m["ended_at"]),
                    "current_section": "conclude",
                    "section_started_at": _ts(m["started_at"]),
                    "scorecard_week": scorecard_week_for(_ts(m["started_at"])),
                    "summary": None if m["ended_at"] is None else {},
                }
                for m in meetings
            ]
        )
        .on_conflict_do_nothing()
        .returning(Meeting.id)
    )
    new_meeting_ids = set(result.scalars())
    counts["meetings"] = len(new_meeting_ids)
    counts["meeting_attendees"] = await _insert(
        session,
        MeetingAttendee,
        [
            {"meeting_id": m["id"], "user_id": user_id, "joined_at": _ts(m["started_at"])}
            for m in meetings
            for user_id in m["attendee_ids"]
        ],
    )
    counts["meeting_ratings"] = await _insert(
        session,
        MeetingRating,
        [
            {
                "meeting_id": m["id"],
                "user_id": r["user_id"],
                "rating": r["rating"],
                "updated_at": _ts(m["ended_at"] or m["started_at"]),
            }
            for m in meetings
            for r in m["ratings"]
        ],
    )

    session.expire_all()
    for m in meetings:
        if m["id"] in new_meeting_ids and m["ended_at"] is not None:
            meeting = await session.get(Meeting, m["id"], populate_existing=True)
            assert meeting is not None
            summary = await build_meeting_summary(session, meeting)
            await session.execute(update(Meeting).where(Meeting.id == m["id"]).values(summary=summary))
    session.expire_all()

    return counts


async def _run(reset: bool) -> dict[str, int]:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        async with create_session_factory(engine)() as session, session.begin():
            return await load_seed(session, settings.seed_path, reset=reset)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.seed", description="Load seed/seed.json.")
    parser.add_argument(
        "--reset", action="store_true", help="truncate every table (including app_clock) before loading"
    )
    args = parser.parse_args()
    counts = asyncio.run(_run(args.reset))
    mode = "reset + loaded" if args.reset else "loaded (existing rows kept)"
    print(f"seed {mode}: " + ", ".join(f"{table}={n}" for table, n in counts.items()))


if __name__ == "__main__":
    main()
