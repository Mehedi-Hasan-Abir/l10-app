"""Make it an Issue under concurrency: duplicate-free by construction (partial unique index + ON CONFLICT).

Each request uses its own client, hence its own HTTP request, DB session and connection, so these tests really
race on Postgres instead of being serialized by a shared session.
"""

import asyncio
from collections import Counter
from datetime import date

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Issue
from tests.api.helpers import BASE, Login, assert_error, cell, make_issue

OFF_TRACK_CELL = ("m1", "2026-10-26")  # 9 < 10: off track, and nobody has made an Issue for it
USERS = ("u1", "u2", "u3", "u4")


async def open_issues(session: AsyncSession, **source: object) -> list[Issue]:
    session.expire_all()
    rows = await session.scalars(
        select(Issue).where(Issue.solved_at.is_(None), *[getattr(Issue, k) == v for k, v in source.items()])
    )
    return list(rows)


async def race(clients: list[httpx.AsyncClient], url: str, *, repeat: int = 1) -> list[httpx.Response]:
    """Fire `repeat` requests per client at once."""
    return list(await asyncio.gather(*(c.post(url) for c in clients for _ in range(repeat))))


async def test_concurrent_make_issue_returns_one_issue(login: Login, db_session: AsyncSession) -> None:
    clients = [await login(u) for u in USERS]  # four users, four separate clients
    assert (
        await open_issues(db_session, source_measurable_id="m1", source_week_start=date(2026, 10, 26)) == []
    )

    responses = await race(clients, f"{BASE}/measurables/m1/scores/2026-10-26/issue", repeat=3)  # 12 requests
    assert len(responses) == 12

    statuses = Counter(r.status_code for r in responses)
    assert statuses == {201: 1, 200: 11}, statuses
    bodies = [r.json() for r in responses]
    assert len({b["issue"]["id"] for b in bodies}) == 1  # every caller got the same Issue
    assert Counter(b["created"] for b in bodies) == {True: 1, False: 11}
    created = next(b for b in bodies if b["created"])
    assert created["issue"]["title"] == "New deals closed, week of Oct 26"
    assert all(b["issue"] == created["issue"] for b in bodies)  # identical payload for everyone

    rows = await open_issues(db_session, source_measurable_id="m1", source_week_start=date(2026, 10, 26))
    assert len(rows) == 1  # exactly one unsolved row in the database
    assert rows[0].id == created["issue"]["id"]
    # the owner is whoever won the race (the user who clicked first), never a mix
    winner = rows[0].owner_id
    assert winner in USERS and created["issue"]["owner"]["id"] == winner
    total = await db_session.scalar(
        select(func.count()).select_from(Issue).where(Issue.title == rows[0].title)
    )
    assert total == 1


async def test_many_rounds_on_different_cells_never_duplicate(login: Login, db_session: AsyncSession) -> None:
    clients = [await login(u) for u in ("u2", "u3", "u4")]
    cells = [("m3", "2026-11-02"), ("m3", "2026-10-12"), ("m5", "2026-11-02"), ("m4", "2026-10-26")]
    # m4 / 2026-10-26 has a SOLVED Issue (i6) already: a new unsolved one may be created
    results = await asyncio.gather(
        *(race(clients, f"{BASE}/measurables/{m}/scores/{w}/issue", repeat=2) for m, w in cells)
    )
    for (measurable, week), responses in zip(cells, results, strict=True):
        assert Counter(r.status_code for r in responses) == {201: 1, 200: 5}, (measurable, week)
        assert len({r.json()["issue"]["id"] for r in responses}) == 1
        rows = await open_issues(
            db_session, source_measurable_id=measurable, source_week_start=date.fromisoformat(week)
        )
        assert len(rows) == 1


async def test_existing_unsolved_issue_is_returned_not_duplicated(
    login: Login, db_session: AsyncSession
) -> None:
    clients = [await login(u) for u in ("u2", "u3")]
    responses = await race(clients, f"{BASE}/measurables/m1/scores/2026-11-02/issue", repeat=3)
    assert {r.status_code for r in responses} == {200}
    assert {r.json()["issue"]["id"] for r in responses} == {"i5"}  # seeded unsolved Issue
    assert {r.json()["created"] for r in responses} == {False}
    assert (
        len(await open_issues(db_session, source_measurable_id="m1", source_week_start=date(2026, 11, 2)))
        == 1
    )


async def test_on_track_and_pending_cells_cannot_become_issues(
    login: Login, db_session: AsyncSession
) -> None:
    c = await login("u2")
    before = await db_session.scalar(select(func.count()).select_from(Issue))
    # m2 2026-11-02 is exactly 3.0 (boundary, on track); m1 2026-11-09 has no score yet (pending);
    # m4 2026-11-02 is on track
    for measurable, week in (("m2", "2026-11-02"), ("m1", "2026-11-09"), ("m4", "2026-11-02")):
        found = assert_error(await make_issue(c, measurable, week), 409, "not_off_track")
        assert found["status"] in {"on_track", "pending"}
    db_session.expire_all()
    assert await db_session.scalar(select(func.count()).select_from(Issue)) == before


async def test_editing_a_score_changes_whether_an_issue_can_be_made(login: Login) -> None:
    c = await login("u2")
    fixed = await c.put(f"{BASE}/measurables/m1/scores/2026-10-26", json={"value": 12})
    assert fixed.json()["status"] == "on_track"
    assert_error(await make_issue(c, "m1", "2026-10-26"), 409, "not_off_track")
    broken = await c.put(f"{BASE}/measurables/m1/scores/2026-10-26", json={"value": 1})
    assert broken.json()["status"] == "off_track"
    assert (await make_issue(c, "m1", "2026-10-26")).status_code == 201


async def test_missing_score_in_a_closed_week_is_off_track_and_can_become_an_issue(login: Login) -> None:
    c = await login("u2")
    response = await make_issue(c, "m5", "2026-10-19")  # no score at all, week closed
    assert response.status_code == 201
    assert response.json()["issue"]["title"] == "Cash on hand, week of Oct 19"
    card = (await c.get(f"{BASE}/scorecard")).json()
    assert cell(card, "m5", "2026-10-19")["open_issue_id"] == response.json()["issue"]["id"]


async def test_after_solving_a_new_issue_can_be_made(login: Login, db_session: AsyncSession) -> None:
    c = await login("u2")
    first = await make_issue(c, *OFF_TRACK_CELL)
    assert first.status_code == 201
    first_id = first.json()["issue"]["id"]
    assert (await make_issue(c, *OFF_TRACK_CELL)).json()["issue"]["id"] == first_id  # still the same

    solved = await c.post(f"{BASE}/issues/{first_id}/solve", json={"resolution_note": "fixed the pipeline"})
    assert solved.status_code == 200
    card = (await c.get(f"{BASE}/scorecard")).json()
    assert cell(card, *OFF_TRACK_CELL)["open_issue_id"] is None  # the link disappears with the solve

    second = await make_issue(c, *OFF_TRACK_CELL)
    assert second.status_code == 201 and second.json()["created"] is True
    assert second.json()["issue"]["id"] != first_id

    db_session.expire_all()
    all_rows = list(
        await db_session.scalars(
            select(Issue).where(
                Issue.source_measurable_id == "m1", Issue.source_week_start == date(2026, 10, 26)
            )
        )
    )
    assert len(all_rows) == 2  # the solved one is kept as history
    assert sum(1 for r in all_rows if r.solved_at is None) == 1


async def test_racing_make_issue_against_solve_never_leaves_two_unsolved(
    login: Login, db_session: AsyncSession
) -> None:
    """Solve the existing Issue while others keep clicking Make it an Issue."""
    clients = [await login(u) for u in ("u2", "u3", "u4")]
    url = f"{BASE}/measurables/m1/scores/2026-11-02/issue"  # i5 is unsolved here
    solve = clients[0].post(f"{BASE}/issues/i5/solve", json={"resolution_note": "resolved while clicking"})
    clicks = [c.post(url) for c in clients[1:] for _ in range(4)]
    results = await asyncio.gather(solve, *clicks)
    assert results[0].status_code == 200
    assert all(r.status_code in (200, 201) for r in results[1:])
    rows = await open_issues(db_session, source_measurable_id="m1", source_week_start=date(2026, 11, 2))
    assert len(rows) <= 1


async def test_concurrent_make_issue_from_rock(login: Login, db_session: AsyncSession) -> None:
    clients = [await login(u) for u in ("u2", "u3", "u4", "u1")]
    responses = await race(clients, f"{BASE}/rocks/r2/issue", repeat=2)
    assert Counter(r.status_code for r in responses) == {201: 1, 200: 7}
    assert len({r.json()["issue"]["id"] for r in responses}) == 1
    rows = await open_issues(db_session, source_rock_id="r2")
    assert len(rows) == 1 and rows[0].title == "Hire VP of Sales (Rock off track)"


async def test_concurrent_make_issue_from_todo(login: Login, db_session: AsyncSession) -> None:
    clients = [await login(u) for u in ("u2", "u3", "u4")]
    responses = await race(clients, f"{BASE}/todos/t12/issue", repeat=3)
    assert Counter(r.status_code for r in responses) == {201: 1, 200: 8}
    assert len({r.json()["issue"]["id"] for r in responses}) == 1
    assert len(await open_issues(db_session, source_todo_id="t12")) == 1


async def test_different_cells_get_different_issues(login: Login) -> None:
    c = await login("u2")
    a, b = await asyncio.gather(make_issue(c, "m1", "2026-10-26"), make_issue(c, "m3", "2026-11-02"))
    assert a.status_code == b.status_code == 201
    assert a.json()["issue"]["id"] != b.json()["issue"]["id"]
