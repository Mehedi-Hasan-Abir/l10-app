"""Seed loading (idempotent, resettable) and the database-level constraints (PLAN section 4).

The constraint tests talk to Postgres directly: the point is that the DB rejects bad rows even if the app
code were wrong.
"""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import FrozenClock, ManualClock, RunningClock, build_running_clock
from app.config import Settings
from app.seed import load_seed, load_seed_file

CHECK = "23514"
UNIQUE = "23505"
FOREIGN_KEY = "23503"

TABLES = (
    "companies",
    "teams",
    "users",
    "memberships",
    "measurables",
    "scores",
    "rocks",
    "milestones",
    "issues",
    "todos",
    "meetings",
    "meeting_attendees",
    "meeting_ratings",
    "app_clock",
)

SEED_COUNTS = {
    "companies": 1,
    "teams": 3,
    "users": 9,
    "memberships": 11,
    "measurables": 7,
    "scores": 83,
    "rocks": 7,
    "milestones": 12,
    "issues": 9,
    "todos": 20,
    "meetings": 2,
    "meeting_attendees": 10,
    "meeting_ratings": 7,
    "app_clock": 0,
}

T0 = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)


async def counts(session: AsyncSession) -> dict[str, int]:
    return {t: (await session.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one() for t in TABLES}


async def scalar(session: AsyncSession, sql: str, **params: Any) -> Any:
    return (await session.execute(text(sql), params)).scalar_one()


async def rejected_with(
    session: AsyncSession, sql: str, sqlstate: str, *, message: str | None = None, **params: Any
) -> str | None:
    """Run `sql` in a savepoint and assert Postgres rejects it with `sqlstate`.

    Returns the violated constraint's name (None for trigger-raised errors). The savepoint keeps the
    surrounding transaction usable, so one test can try several bad rows.
    """
    with pytest.raises(IntegrityError) as excinfo:
        async with session.begin_nested():
            await session.execute(text(sql), params)
    orig = excinfo.value.orig
    assert orig is not None
    assert getattr(orig, "sqlstate", None) == sqlstate, orig
    if message is not None:
        assert message in str(orig)
    return getattr(orig.__cause__, "constraint_name", None)


# --- seed ---------------------------------------------------------------------------------------------------


async def test_seed_loads_expected_row_counts(db_session: AsyncSession) -> None:
    assert await counts(db_session) == SEED_COUNTS


async def test_seed_values_are_exact_decimals(db_session: AsyncSession) -> None:
    # parse_float=Decimal: 3.0 / 2.4 / 3.3 never pass through binary floats.
    assert await scalar(db_session, "SELECT goal_value FROM measurables WHERE id = 'm2'") == Decimal("3.0")
    value = await scalar(
        db_session, "SELECT value FROM scores WHERE measurable_id = 'm2' AND week_start = '2026-10-19'"
    )
    assert value == Decimal("2.4")


async def test_rerunning_seed_without_reset_is_a_noop(
    db_session_factory: async_sessionmaker[AsyncSession], test_settings: Settings
) -> None:
    async with db_session_factory() as session, session.begin():
        inserted = await load_seed(session, test_settings.seed_path, reset=False)
    assert set(inserted.values()) == {0}

    async with db_session_factory() as session:
        assert await counts(session) == SEED_COUNTS


async def test_app_created_rows_survive_a_non_reset_reseed(
    db_session_factory: async_sessionmaker[AsyncSession], test_settings: Settings
) -> None:
    async with db_session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO issues (id, team_id, title, owner_id, created_at) "
                "VALUES ('i_app1', 'team_lead', 'Created by the app', 'u2', :ts)"
            ),
            {"ts": T0},
        )
        # Edits to seeded rows are not clobbered either.
        await session.execute(text("UPDATE issues SET priority = 1, version = 2 WHERE id = 'i1'"))
        await session.execute(
            text("UPDATE scores SET value = 99 WHERE measurable_id = 'm1' AND week_start = '2026-11-02'")
        )

    async with db_session_factory() as session, session.begin():
        inserted = await load_seed(session, test_settings.seed_path, reset=False)
    assert set(inserted.values()) == {0}

    async with db_session_factory() as session:
        assert await scalar(session, "SELECT count(*) FROM issues") == SEED_COUNTS["issues"] + 1
        assert await scalar(session, "SELECT title FROM issues WHERE id = 'i_app1'") == "Created by the app"
        assert await scalar(session, "SELECT priority FROM issues WHERE id = 'i1'") == 1
        score = await scalar(
            session, "SELECT value FROM scores WHERE measurable_id = 'm1' AND week_start = '2026-11-02'"
        )
        assert score == 99


async def test_reseed_fills_in_rows_that_were_removed_but_keeps_summaries(
    db_session_factory: async_sessionmaker[AsyncSession], test_settings: Settings
) -> None:
    async with db_session_factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM scores WHERE measurable_id = 'm1' AND week_start = '2026-11-02'")
        )
    async with db_session_factory() as session, session.begin():
        inserted = await load_seed(session, test_settings.seed_path, reset=False)
    assert inserted["scores"] == 1
    assert sum(inserted.values()) == 1


async def test_reset_restores_the_seed_and_clears_app_data(
    db_session_factory: async_sessionmaker[AsyncSession], test_settings: Settings
) -> None:
    async with db_session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO todos (id, team_id, title, owner_id, due_date, created_at) "
                "VALUES ('t_app1', 'team_lead', 'Created by the app', 'u2', '2026-11-18', :ts)"
            ),
            {"ts": T0},
        )
        await session.execute(text("DELETE FROM meeting_ratings WHERE meeting_id = 'mt2' AND user_id = 'u4'"))
        await session.execute(text("UPDATE issues SET priority = 1 WHERE id = 'i1'"))
        await session.execute(
            text("INSERT INTO app_clock (app_now, real_anchor) VALUES (:a, now())"), {"a": T0}
        )

    async with db_session_factory() as session, session.begin():
        inserted = await load_seed(session, test_settings.seed_path, reset=True)
    assert inserted == {k: v for k, v in SEED_COUNTS.items() if k != "app_clock"}

    async with db_session_factory() as session:
        assert await counts(session) == SEED_COUNTS
        assert await scalar(session, "SELECT count(*) FROM todos WHERE id = 't_app1'") == 0
        assert await scalar(session, "SELECT priority FROM issues WHERE id = 'i1'") == 5
        assert await scalar(session, "SELECT summary->>'rating' FROM meetings WHERE id = 'mt2'") == "8.3"


async def test_seed_json_is_loaded_with_decimals(test_settings: Settings) -> None:
    seed = load_seed_file(test_settings.seed_path)
    assert isinstance(seed["measurables"][1]["goal"]["value"], Decimal)
    raw = json.loads(test_settings.seed_path.read_text(encoding="utf-8"))
    assert len(seed["scores"]) == len(raw["scores"]) == SEED_COUNTS["scores"]


async def test_seeded_meetings_carry_their_frozen_summaries(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(text("SELECT id, summary, scorecard_week, current_section FROM meetings"))
    ).all()
    by_id = {r.id: r for r in rows}
    assert set(by_id) == {"mt1", "mt2"}
    assert all(r.summary is not None and r.current_section == "conclude" for r in rows)

    mt1, mt2 = by_id["mt1"].summary, by_id["mt2"].summary
    assert str(by_id["mt1"].scorecard_week) == "2026-10-19"
    assert str(by_id["mt2"].scorecard_week) == "2026-10-26"
    assert mt1["rating"] == 8.0
    assert mt2["rating"] == 8.3  # 33 / 4 = 8.25 -> 8.3, half up (Python round() would say 8.2)

    assert [i["id"] for i in mt1["issues_solved"]] == ["i7"]
    assert [t["id"] for t in mt1["todos_created"]] == ["t20"]
    assert [i["id"] for i in mt2["issues_solved"]] == ["i6"]
    assert [t["id"] for t in mt2["todos_created"]] == ["t30"]
    assert [a["id"] for a in mt2["attendees"]] == ["u1", "u2", "u3", "u4", "u5"]
    assert mt2["todos_created"][0]["owner"] == {"id": "u3", "name": "Casey Cho", "role": "team_member"}
    assert mt2["todos_created"][0]["due_date"] == "2026-11-11"

    statuses1 = {m["measurable_id"]: m["status"] for m in mt1["measurable_status"]}
    assert statuses1 == {
        "m1": "on_track",
        "m2": "on_track",
        "m3": "on_track",
        "m4": "on_track",
        "m5": "off_track",  # closed week without a score
    }
    statuses2 = {m["measurable_id"]: m["status"] for m in mt2["measurable_status"]}
    assert statuses2 == {
        "m1": "off_track",
        "m2": "on_track",
        "m3": "on_track",
        "m4": "off_track",
        "m5": "on_track",
    }
    assert mt2["started_at"] == "2026-11-04T15:00:00Z"
    assert mt2["ended_at"] == "2026-11-04T16:20:00Z"


async def test_running_clock_anchor_persists_across_restarts(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = await build_running_clock(db_session_factory, T0)
    async with db_session_factory() as session:
        anchor = await scalar(session, "SELECT real_anchor FROM app_clock WHERE app_now = :a", a=T0)

    second = await build_running_clock(db_session_factory, T0)  # "restart": same APP_NOW
    async with db_session_factory() as session:
        assert await scalar(session, "SELECT count(*) FROM app_clock") == 1
        assert await scalar(session, "SELECT real_anchor FROM app_clock WHERE app_now = :a", a=T0) == anchor

    assert T0 <= first.now() < T0 + timedelta(minutes=1)
    assert abs(first.now() - second.now()) < timedelta(seconds=1)  # same timeline, not a fresh one

    other = await build_running_clock(db_session_factory, T0 + timedelta(days=7))  # new APP_NOW, new anchor
    async with db_session_factory() as session:
        assert await scalar(session, "SELECT count(*) FROM app_clock") == 2
    assert other.now() >= T0 + timedelta(days=7)


# --- constraints: roles -------------------------------------------------------------------------------------

OBSERVER = "u5"  # role = observer, member of team_lead


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param(
            "INSERT INTO issues (id, team_id, title, owner_id, created_at) "
            "VALUES ('i_x', 'team_lead', 'Observer owns it', :obs, :ts)",
            id="issue-owner",
        ),
        pytest.param(
            "INSERT INTO todos (id, team_id, title, owner_id, due_date, created_at) "
            "VALUES ('t_x', 'team_lead', 'Observer owns it', :obs, '2026-11-18', :ts)",
            id="todo-owner",
        ),
        pytest.param(
            "INSERT INTO measurables (id, team_id, name, owner_id, unit, goal_type, goal_value, sort_order) "
            "VALUES ('m_x', 'team_lead', 'X', :obs, 'count', 'at_least', 1, 99)",
            id="measurable-owner",
        ),
        pytest.param(
            "INSERT INTO rocks (id, team_id, title, owner_id, quarter, seeded_status, sort_order) "
            "VALUES ('r_x', 'team_lead', 'X', :obs, '2026-Q4', 'on_track', 99)",
            id="rock-owner",
        ),
        pytest.param(
            "INSERT INTO meetings (id, team_id, facilitator_id, started_at, current_section, "
            "section_started_at, scorecard_week) "
            "VALUES ('mt_x', 'team_lead', :obs, :ts, 'scorecard', :ts, '2026-11-02')",
            id="meeting-facilitator",
        ),
        pytest.param(
            "INSERT INTO meeting_ratings (meeting_id, user_id, rating, updated_at) "
            "VALUES ('mt1', :obs, 9, :ts)",  # u5 attended mt1, so the FK passes: only the trigger can stop it
            id="meeting-rating",
        ),
        pytest.param("UPDATE issues SET owner_id = :obs WHERE id = 'i1'", id="reassign-issue-to-observer"),
        pytest.param("UPDATE todos SET owner_id = :obs WHERE id = 't12'", id="reassign-todo-to-observer"),
    ],
)
async def test_db_rejects_observer_as_owner_facilitator_or_rater(db_session: AsyncSession, sql: str) -> None:
    await rejected_with(db_session, sql, CHECK, message="observer", obs=OBSERVER, ts=T0)


async def test_db_accepts_non_observer_owner(db_session: AsyncSession) -> None:
    await db_session.execute(
        text(
            "INSERT INTO issues (id, team_id, title, owner_id, created_at) "
            "VALUES ('i_ok', 'team_lead', 'A member owns it', 'u2', :ts)"
        ),
        {"ts": T0},
    )
    assert await scalar(db_session, "SELECT owner_id FROM issues WHERE id = 'i_ok'") == "u2"


# --- constraints: value ranges ----------------------------------------------------------------------------


@pytest.mark.parametrize("priority", [0, 6])
async def test_db_rejects_priority_outside_1_to_5(db_session: AsyncSession, priority: int) -> None:
    constraint = await rejected_with(
        db_session,
        "INSERT INTO issues (id, team_id, title, owner_id, priority, created_at) "
        "VALUES ('i_x', 'team_lead', 'Bad priority', 'u2', :p, :ts)",
        CHECK,
        p=priority,
        ts=T0,
    )
    assert constraint == "ck_issues_priority"
    await rejected_with(db_session, "UPDATE issues SET priority = :p WHERE id = 'i1'", CHECK, p=priority)


@pytest.mark.parametrize("rating", [0, 11])
async def test_db_rejects_rating_outside_1_to_10(db_session: AsyncSession, rating: int) -> None:
    constraint = await rejected_with(
        db_session,
        "UPDATE meeting_ratings SET rating = :r WHERE meeting_id = 'mt1' AND user_id = 'u1'",
        CHECK,
        r=rating,
    )
    assert constraint == "ck_meeting_ratings_rating"


async def test_db_accepts_boundary_ratings_and_priorities(db_session: AsyncSession) -> None:
    for rating in (1, 10):
        await db_session.execute(
            text("UPDATE meeting_ratings SET rating = :r WHERE meeting_id = 'mt1' AND user_id = 'u1'"),
            {"r": rating},
        )
    for priority in (1, 5, None):
        await db_session.execute(text("UPDATE issues SET priority = :p WHERE id = 'i1'"), {"p": priority})


async def test_db_rejects_blank_titles_and_notes(db_session: AsyncSession) -> None:
    sql = (
        "INSERT INTO issues (id, team_id, title, owner_id, created_at) "
        "VALUES ('i_x', 'team_lead', :t, 'u2', :ts)"
    )
    await rejected_with(db_session, sql, CHECK, t="   ", ts=T0)
    await rejected_with(db_session, sql, CHECK, t="x" * 201, ts=T0)
    # A solved issue needs a non-blank note, and an unsolved one must not have one.
    await rejected_with(
        db_session, "UPDATE issues SET solved_at = :ts, resolution_note = '  ' WHERE id = 'i1'", CHECK, ts=T0
    )
    await rejected_with(db_session, "UPDATE issues SET solved_at = :ts WHERE id = 'i1'", CHECK, ts=T0)
    await rejected_with(
        db_session, "UPDATE issues SET resolution_note = 'orphan note' WHERE id = 'i1'", CHECK
    )


# --- constraints: weeks -------------------------------------------------------------------------------------


@pytest.mark.parametrize("week", ["2026-11-10", "2026-11-08", "2026-11-15"])  # Tue, Sun, Sun
async def test_db_rejects_non_monday_score_week(db_session: AsyncSession, week: str) -> None:
    constraint = await rejected_with(
        db_session,
        "INSERT INTO scores (measurable_id, week_start, value, updated_at) VALUES ('m1', :w, 5, :ts)",
        CHECK,
        w=date.fromisoformat(week),
        ts=T0,
    )
    assert constraint == "ck_scores_week_is_monday"


async def test_db_rejects_non_monday_issue_source_week_and_meeting_week(db_session: AsyncSession) -> None:
    await rejected_with(
        db_session,
        "INSERT INTO issues (id, team_id, title, owner_id, created_at, source_type, source_measurable_id, "
        "source_week_start) VALUES ('i_x', 'team_lead', 'T', 'u2', :ts, 'measurable', 'm2', '2026-11-04')",
        CHECK,
        ts=T0,
    )
    await rejected_with(
        db_session, "UPDATE meetings SET scorecard_week = '2026-10-21' WHERE id = 'mt1'", CHECK
    )


async def test_db_rejects_duplicate_score_for_a_week(db_session: AsyncSession) -> None:
    await rejected_with(
        db_session,
        "INSERT INTO scores (measurable_id, week_start, value, updated_at) "
        "VALUES ('m1', '2026-11-02', 5, :ts)",
        UNIQUE,
        ts=T0,
    )


# --- constraints: duplicate open issues (the Make it an Issue guard) ----------------------------------------

INSERT_MEASURABLE_ISSUE = (
    "INSERT INTO issues (id, team_id, title, owner_id, created_at, solved_at, resolution_note, source_type, "
    "source_measurable_id, source_week_start) "
    "VALUES (:id, 'team_lead', 'Payroll runs, week of Oct 26', 'u3', :ts, :solved_at, :note, 'measurable', "
    "'m4', '2026-10-26')"
)


async def test_db_rejects_two_unsolved_issues_for_the_same_measurable_and_week(
    db_session: AsyncSession,
) -> None:
    # Seed: i5 is the unsolved issue for (m1, 2026-11-02).
    constraint = await rejected_with(
        db_session,
        "INSERT INTO issues (id, team_id, title, owner_id, created_at, source_type, source_measurable_id, "
        "source_week_start) "
        "VALUES ('i_dup', 'team_lead', 'Dup', 'u2', :ts, 'measurable', 'm1', '2026-11-02')",
        UNIQUE,
        ts=T0,
    )
    assert constraint == "uq_issue_open_measurable"


async def test_db_allows_one_solved_plus_one_unsolved_but_not_two_unsolved(db_session: AsyncSession) -> None:
    # Seed: i6 is SOLVED for (m4, 2026-10-26). A new unsolved one is fine...
    await db_session.execute(
        text(INSERT_MEASURABLE_ISSUE), {"id": "i_new", "ts": T0, "solved_at": None, "note": None}
    )
    # ...so is another solved one (history can hold many)...
    await db_session.execute(
        text(INSERT_MEASURABLE_ISSUE), {"id": "i_old", "ts": T0, "solved_at": T0, "note": "Done before"}
    )
    # ...but a second UNSOLVED one is not.
    constraint = await rejected_with(
        db_session, INSERT_MEASURABLE_ISSUE, UNIQUE, id="i_new2", ts=T0, solved_at=None, note=None
    )
    assert constraint == "uq_issue_open_measurable"

    # Solving the unsolved one frees the slot again.
    await db_session.execute(
        text("UPDATE issues SET solved_at = :ts, resolution_note = 'ok' WHERE id = 'i_new'"), {"ts": T0}
    )
    await db_session.execute(
        text(INSERT_MEASURABLE_ISSUE), {"id": "i_new2", "ts": T0, "solved_at": None, "note": None}
    )


async def test_db_rejects_two_unsolved_issues_for_the_same_rock_or_todo(db_session: AsyncSession) -> None:
    rock_issue = (
        "INSERT INTO issues (id, team_id, title, owner_id, created_at, source_type, source_rock_id) "
        "VALUES (:id, 'team_lead', 'Rock is off track', 'u2', :ts, 'rock', 'r4')"
    )
    await db_session.execute(text(rock_issue), {"id": "i_r1", "ts": T0})
    assert await rejected_with(db_session, rock_issue, UNIQUE, id="i_r2", ts=T0) == "uq_issue_open_rock"

    todo_issue = (
        "INSERT INTO issues (id, team_id, title, owner_id, created_at, source_type, source_todo_id) "
        "VALUES (:id, 'team_lead', 'To-do is overdue', 'u2', :ts, 'todo', 't12')"
    )
    await db_session.execute(text(todo_issue), {"id": "i_t1", "ts": T0})
    assert await rejected_with(db_session, todo_issue, UNIQUE, id="i_t2", ts=T0) == "uq_issue_open_todo"


async def test_db_enforces_the_issue_source_shape(db_session: AsyncSession) -> None:
    # source_type without its columns, and columns of the wrong kind, are rejected.
    await rejected_with(
        db_session,
        "INSERT INTO issues (id, team_id, title, owner_id, created_at, source_type) "
        "VALUES ('i_x', 'team_lead', 'T', 'u2', :ts, 'measurable')",
        CHECK,
        ts=T0,
    )
    await rejected_with(
        db_session,
        "INSERT INTO issues (id, team_id, title, owner_id, created_at, source_type, source_rock_id, "
        "source_week_start) VALUES ('i_x', 'team_lead', 'T', 'u2', :ts, 'rock', 'r4', '2026-11-02')",
        CHECK,
        ts=T0,
    )


# --- constraints: team isolation (composite FKs) ------------------------------------------------------------


async def test_db_rejects_todo_whose_source_issue_is_in_another_team(db_session: AsyncSession) -> None:
    constraint = await rejected_with(
        db_session,
        "INSERT INTO todos (id, team_id, title, owner_id, due_date, source_issue_id, created_at) "
        "VALUES ('t_x', 'team_sales', 'Follow-up', 'u6', '2026-11-18', 'i1', :ts)",  # i1 belongs to team_lead
        FOREIGN_KEY,
        ts=T0,
    )
    assert constraint == "fk_todos_source_issue_team"
    # Same team is fine.
    await db_session.execute(
        text(
            "INSERT INTO todos (id, team_id, title, owner_id, due_date, source_issue_id, created_at) "
            "VALUES ('t_ok', 'team_lead', 'Follow-up', 'u2', '2026-11-18', 'i1', :ts)"
        ),
        {"ts": T0},
    )


async def test_db_rejects_issue_whose_source_is_in_another_team(db_session: AsyncSession) -> None:
    constraint = await rejected_with(
        db_session,
        "INSERT INTO issues (id, team_id, title, owner_id, created_at, source_type, source_measurable_id, "
        "source_week_start) VALUES ('i_x', 'team_sales', 'T', 'u6', :ts, 'measurable', 'm1', '2026-10-05')",
        FOREIGN_KEY,
        ts=T0,
    )
    assert constraint == "fk_issues_source_measurable_team"
    constraint = await rejected_with(
        db_session,
        "INSERT INTO issues (id, team_id, title, owner_id, created_at, source_type, source_rock_id) "
        "VALUES ('i_x', 'team_sales', 'T', 'u6', :ts, 'rock', 'r4')",
        FOREIGN_KEY,
        ts=T0,
    )
    assert constraint == "fk_issues_source_rock_team"
    constraint = await rejected_with(
        db_session,
        "INSERT INTO issues (id, team_id, title, owner_id, created_at, source_type, source_todo_id) "
        "VALUES ('i_x', 'team_sales', 'T', 'u6', :ts, 'todo', 't12')",
        FOREIGN_KEY,
        ts=T0,
    )
    assert constraint == "fk_issues_source_todo_team"


async def test_db_rejects_rating_by_someone_who_did_not_attend(db_session: AsyncSession) -> None:
    # u6 is a team_member but not an attendee of mt1.
    constraint = await rejected_with(
        db_session,
        "INSERT INTO meeting_ratings (meeting_id, user_id, rating, updated_at) VALUES ('mt1', 'u6', 7, :ts)",
        FOREIGN_KEY,
        ts=T0,
    )
    assert constraint == "fk_meeting_ratings_attendee"


async def test_db_restricts_deletes_of_referenced_rows(db_session: AsyncSession) -> None:
    await rejected_with(db_session, "DELETE FROM teams WHERE id = 'team_lead'", FOREIGN_KEY)
    await rejected_with(db_session, "DELETE FROM issues WHERE id = 'i7'", FOREIGN_KEY)  # t20 points at it


# --- constraints: measurable goals --------------------------------------------------------------------------

INSERT_MEASURABLE = (
    "INSERT INTO measurables (id, team_id, name, owner_id, unit, goal_type, goal_value, goal_min, goal_max, "
    "sort_order) VALUES ('m_x', 'team_lead', 'X', 'u2', 'count', :type, :value, :min, :max, 99)"
)


@pytest.mark.parametrize(
    ("goal_type", "value", "goal_min", "goal_max"),
    [
        pytest.param("between", None, None, None, id="between-without-bounds"),
        pytest.param("between", None, 2, None, id="between-without-max"),
        pytest.param("between", None, None, 4, id="between-without-min"),
        pytest.param("between", 3, 2, 4, id="between-with-value"),
        pytest.param("between", None, 5, 4, id="between-min-above-max"),
        pytest.param("at_least", None, None, None, id="at_least-without-value"),
        pytest.param("at_most", 3, 1, None, id="at_most-with-min"),
        pytest.param("exactly", 1, None, 2, id="exactly-with-max"),
        pytest.param("roughly", 1, None, None, id="unknown-type"),
    ],
)
async def test_db_rejects_malformed_goals(
    db_session: AsyncSession, goal_type: str, value: int | None, goal_min: int | None, goal_max: int | None
) -> None:
    await rejected_with(
        db_session, INSERT_MEASURABLE, CHECK, type=goal_type, value=value, min=goal_min, max=goal_max
    )


async def test_db_accepts_well_formed_goals(db_session: AsyncSession) -> None:
    await db_session.execute(text(INSERT_MEASURABLE), {"type": "between", "value": None, "min": 2, "max": 4})
    await db_session.execute(
        text(INSERT_MEASURABLE.replace("'m_x'", "'m_y'")),
        {"type": "at_least", "value": 5, "min": None, "max": None},
    )


# --- constraints: meetings ----------------------------------------------------------------------------------

INSERT_ACTIVE_MEETING = (
    "INSERT INTO meetings (id, team_id, facilitator_id, started_at, current_section, section_started_at, "
    "scorecard_week) VALUES (:id, :team, 'u2', :ts, 'scorecard', :ts, '2026-11-02')"
)


async def test_db_allows_only_one_active_meeting_per_team(db_session: AsyncSession) -> None:
    await db_session.execute(text(INSERT_ACTIVE_MEETING), {"id": "mt_a", "team": "team_lead", "ts": T0})
    constraint = await rejected_with(
        db_session, INSERT_ACTIVE_MEETING, UNIQUE, id="mt_b", team="team_lead", ts=T0
    )
    assert constraint == "uq_meeting_one_active_per_team"

    # Another team may have its own active meeting, and ended meetings do not count.
    await db_session.execute(text(INSERT_ACTIVE_MEETING), {"id": "mt_c", "team": "team_sales", "ts": T0})
    await db_session.execute(
        text("UPDATE meetings SET ended_at = :ts, summary = CAST('{}' AS jsonb) WHERE id = 'mt_a'"),
        {"ts": T0},
    )
    await db_session.execute(text(INSERT_ACTIVE_MEETING), {"id": "mt_d", "team": "team_lead", "ts": T0})


async def test_db_requires_a_summary_exactly_when_a_meeting_has_ended(db_session: AsyncSession) -> None:
    await rejected_with(db_session, "UPDATE meetings SET summary = NULL WHERE id = 'mt1'", CHECK)
    await db_session.execute(text(INSERT_ACTIVE_MEETING), {"id": "mt_a", "team": "team_lead", "ts": T0})
    await rejected_with(db_session, "UPDATE meetings SET ended_at = :ts WHERE id = 'mt_a'", CHECK, ts=T0)
    await rejected_with(
        db_session, "UPDATE meetings SET summary = CAST('{}' AS jsonb) WHERE id = 'mt_a'", CHECK
    )
    await rejected_with(
        db_session,
        "UPDATE meetings SET ended_at = :before, summary = CAST('{}' AS jsonb) WHERE id = 'mt_a'",
        CHECK,
        before=T0 - timedelta(hours=1),
    )


async def test_db_rejects_unknown_section_and_role(db_session: AsyncSession) -> None:
    await rejected_with(db_session, "UPDATE meetings SET current_section = 'lunch' WHERE id = 'mt1'", CHECK)
    await rejected_with(db_session, "UPDATE users SET role = 'owner' WHERE id = 'u2'", CHECK)


# --- clocks (pure) ------------------------------------------------------------------------------------------


def test_manual_clock_advances_and_frozen_clock_does_not() -> None:
    frozen = FrozenClock(T0)
    manual = ManualClock(T0)
    manual.advance(90)
    assert frozen.now() == T0
    assert manual.now() == T0 + timedelta(seconds=90)
    assert manual.now().tzinfo is not None and manual.now().utcoffset() == timedelta(0)


def test_running_clock_is_app_now_plus_elapsed() -> None:
    ticks = iter([100.0, 100.0, 130.5])  # constructor, first now(), second now()
    clock = RunningClock(T0, timedelta(seconds=60), monotonic=lambda: next(ticks))
    assert clock.now() == T0 + timedelta(seconds=60)
    assert clock.now() == T0 + timedelta(seconds=90.5)
