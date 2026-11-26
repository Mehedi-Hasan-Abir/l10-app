"""Issues: order, shapes, create / prioritize / solve / add a To-Do, validation and version conflicts."""

from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Issue
from tests.api.helpers import BASE, NOW_ISO, Login, assert_error, db_snapshot

ISSUES = f"{BASE}/issues"


async def ids(c: httpx.AsyncClient, status: str | None = None, team: str = "team_lead") -> list[str]:
    params = {} if status is None else {"status": status}
    response = await c.get(f"/api/teams/{team}/issues", params=params)
    assert response.status_code == 200, response.text
    return [i["id"] for i in response.json()]


async def create(c: httpx.AsyncClient, **body: Any) -> dict[str, Any]:
    response = await c.post(ISSUES, json=body)
    assert response.status_code == 201, response.text
    return response.json()


# --- reading --------------------------------------------------------------------------------------------


async def test_unsolved_issue_order_matches_the_rules(login: Login) -> None:
    c = await login("u2")
    # i2/i1 are priority 5 (oldest first), then i5 (4), i3 (3), and i4 without priority last
    assert await ids(c) == ["i2", "i1", "i5", "i3", "i4"]
    assert await ids(c, "unsolved") == ["i2", "i1", "i5", "i3", "i4"]


async def test_solved_issues_newest_solved_first(login: Login) -> None:
    c = await login("u2")
    assert await ids(c, "solved") == ["i6", "i7"]  # solved 2026-11-04 then 2026-10-28


async def test_issue_shape(login: Login) -> None:
    c = await login("u2")
    issues = {i["id"]: i for i in (await c.get(ISSUES)).json()}
    assert issues["i1"] == {
        "id": "i1",
        "team_id": "team_lead",
        "title": "Hiring pipeline is slow",
        "owner": {"id": "u3", "name": "Casey Cho", "role": "team_member"},
        "priority": 5,
        "created_at": "2026-10-20T14:00:00Z",
        "solved_at": None,
        "resolution_note": None,
        "source": None,
        "version": 1,
        "todo_count": 0,
    }
    assert issues["i5"]["source"] == {
        "type": "measurable",
        "measurable_id": "m1",
        "measurable_name": "New deals closed",
        "week_start": "2026-11-02",
    }
    assert issues["i4"]["priority"] is None
    solved = {i["id"]: i for i in (await c.get(ISSUES, params={"status": "solved"})).json()}
    assert solved["i6"]["solved_at"] == "2026-11-04T15:40:00Z"
    assert solved["i6"]["resolution_note"] == "Bank holiday moved the run; added a reminder."
    assert solved["i6"]["source"]["measurable_name"] == "Payroll runs"
    assert (solved["i6"]["todo_count"], solved["i7"]["todo_count"]) == (1, 1)  # t30 and t20


async def test_other_teams_issues_are_separate(login: Login) -> None:
    assert await ids(await login("u6"), team="team_sales") == ["i8"]
    assert await ids(await login("u8"), team="team_ops") == ["i9"]


async def test_invalid_status_filter_is_422(login: Login) -> None:
    response = await (await login("u2")).get(ISSUES, params={"status": "everything"})
    assert response.status_code == 422


async def test_observer_can_read_issues(login: Login) -> None:
    assert await ids(await login("u5")) == ["i2", "i1", "i5", "i3", "i4"]


# --- create ---------------------------------------------------------------------------------------------


async def test_create_issue_owner_is_caller_and_created_now(login: Login) -> None:
    c = await login("u3")
    issue = await create(c, title="  Pricing is confusing  ", priority=2)
    assert issue["title"] == "Pricing is confusing"  # stripped
    assert issue["owner"]["id"] == "u3"
    assert issue["priority"] == 2
    assert issue["created_at"] == NOW_ISO
    assert issue["solved_at"] is None and issue["source"] is None
    assert (issue["version"], issue["todo_count"]) == (1, 0)
    assert issue["id"].startswith("i_")
    # newer than the older priority-2-less issues; priority 2 sorts after i3 (3) and before i4 (none)
    assert await ids(c) == ["i2", "i1", "i5", "i3", issue["id"], "i4"]


async def test_create_issue_priority_is_optional(login: Login) -> None:
    c = await login("u2")
    issue = await create(c, title="No priority yet")
    assert issue["priority"] is None
    assert (await ids(c))[-2:] == ["i4", issue["id"]]  # unprioritised: oldest first, i4 predates it


async def test_admin_can_create_in_any_team(login: Login) -> None:
    c = await login("u1")
    response = await c.post("/api/teams/team_ops/issues", json={"title": "From admin"})
    assert response.status_code == 201
    assert response.json()["team_id"] == "team_ops" and response.json()["owner"]["id"] == "u1"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"title": "ok", "priority": 0},
        {"title": "ok", "priority": 6},
        {"title": "ok", "priority": 2.5},
        {"title": "ok", "priority": "3"},
        {"title": "ok", "owner_id": "u3"},  # owner is always the caller
        {"title": "ok", "extra": 1},
    ],
)
async def test_create_issue_validation(login: Login, body: dict[str, Any], db_session: AsyncSession) -> None:
    before = await db_session.scalar(select(func.count()).select_from(Issue))
    response = await (await login("u2")).post(ISSUES, json=body)
    assert response.status_code == 422, response.text
    assert await db_session.scalar(select(func.count()).select_from(Issue)) == before


async def test_title_of_exactly_200_chars_is_accepted(login: Login) -> None:
    issue = await create(await login("u2"), title="x" * 200)
    assert len(issue["title"]) == 200


# --- priority -------------------------------------------------------------------------------------------


async def test_set_priority_reorders_and_bumps_version(login: Login) -> None:
    c = await login("u2")
    response = await c.patch(f"{ISSUES}/i4", json={"priority": 5})
    assert response.status_code == 200, response.text
    assert response.json()["priority"] == 5 and response.json()["version"] == 2
    # priority 5, oldest first: i4 (Sep 1) now leads i2 (Oct 14) and i1 (Oct 20)
    assert await ids(c) == ["i4", "i2", "i1", "i5", "i3"]


async def test_clear_priority_with_null(login: Login) -> None:
    c = await login("u2")
    response = await c.patch(f"{ISSUES}/i2", json={"priority": None})
    assert response.status_code == 200 and response.json()["priority"] is None
    assert await ids(c) == ["i1", "i5", "i3", "i4", "i2"]  # unprioritised last, oldest first (i4 Sep, i2 Oct)


@pytest.mark.parametrize("body", [{}, {"priority": 0}, {"priority": 6}, {"priority": "high"}, {"x": 1}])
async def test_set_priority_validation(login: Login, body: dict[str, Any]) -> None:
    response = await (await login("u2")).patch(f"{ISSUES}/i1", json=body)
    assert response.status_code == 422


async def test_priority_expected_version(login: Login) -> None:
    a, b = await login("u2"), await login("u3")
    ok = await a.patch(f"{ISSUES}/i1", json={"priority": 4, "expected_version": 1})
    assert ok.status_code == 200 and ok.json()["version"] == 2
    stale = await b.patch(f"{ISSUES}/i1", json={"priority": 1, "expected_version": 1})
    found = assert_error(stale, 409, "version_conflict")
    assert found["current"]["priority"] == 4 and found["current"]["version"] == 2
    assert (await b.get(ISSUES)).json()[0]["id"] in {"i2", "i1", "i5"}  # untouched by the stale write
    current = next(i for i in (await b.get(ISSUES)).json() if i["id"] == "i1")
    assert current["priority"] == 4


async def test_priority_without_expected_version_is_last_save_wins(login: Login) -> None:
    a, b = await login("u2"), await login("u3")
    await a.patch(f"{ISSUES}/i1", json={"priority": 2})
    response = await b.patch(f"{ISSUES}/i1", json={"priority": 1})
    assert response.status_code == 200 and response.json()["priority"] == 1


async def test_cannot_prioritize_a_solved_issue(login: Login) -> None:
    response = await (await login("u2")).patch(f"{ISSUES}/i6", json={"priority": 5})
    assert_error(response, 409, "issue_solved")


# --- solve ----------------------------------------------------------------------------------------------


async def test_solve_requires_a_note_and_records_it(login: Login) -> None:
    c = await login("u2")
    response = await c.post(f"{ISSUES}/i3/solve", json={"resolution_note": "  Hired weekend cover.  "})
    assert response.status_code == 200, response.text
    solved = response.json()
    assert solved["solved_at"] == NOW_ISO
    assert solved["resolution_note"] == "Hired weekend cover."
    assert solved["version"] == 2
    assert await ids(c) == ["i2", "i1", "i5", "i4"]
    assert (await ids(c, "solved"))[0] == "i3"  # newest solved first


@pytest.mark.parametrize(
    "body",
    [{}, {"resolution_note": ""}, {"resolution_note": "  "}, {"resolution_note": "x" * 2001}, {"note": "x"}],
)
async def test_solve_validation(login: Login, body: dict[str, Any], db_session: AsyncSession) -> None:
    response = await (await login("u2")).post(f"{ISSUES}/i3/solve", json=body)
    assert response.status_code == 422
    await db_session.rollback()
    assert (await db_session.get(Issue, "i3")).solved_at is None  # type: ignore[union-attr]


async def test_note_of_exactly_2000_chars_is_accepted(login: Login) -> None:
    response = await (await login("u2")).post(f"{ISSUES}/i3/solve", json={"resolution_note": "n" * 2000})
    assert response.status_code == 200


async def test_second_solve_is_409(login: Login) -> None:
    c = await login("u2")
    assert (await c.post(f"{ISSUES}/i3/solve", json={"resolution_note": "first"})).status_code == 200
    again = await c.post(f"{ISSUES}/i3/solve", json={"resolution_note": "second"})
    assert_error(again, 409, "issue_solved")
    seeded = await c.post(f"{ISSUES}/i6/solve", json={"resolution_note": "again"})
    assert_error(seeded, 409, "issue_solved")
    # the first note stands
    solved = {i["id"]: i for i in (await c.get(ISSUES, params={"status": "solved"})).json()}
    assert solved["i3"]["resolution_note"] == "first"


async def test_unknown_issue_is_404(login: Login) -> None:
    c = await login("u2")
    assert_error(
        await c.post(f"{ISSUES}/i_nope/solve", json={"resolution_note": "x"}), 404, "issue_not_found"
    )
    assert_error(await c.patch(f"{ISSUES}/i_nope", json={"priority": 1}), 404, "issue_not_found")
    todo = {"title": "x", "owner_id": "u2"}
    assert_error(await c.post(f"{ISSUES}/i_nope/todos", json=todo), 404, "issue_not_found")


# --- add a To-Do ----------------------------------------------------------------------------------------


async def test_add_todo_from_issue(login: Login) -> None:
    c = await login("u2")
    response = await c.post(f"{ISSUES}/i1/todos", json={"title": " Call recruiter ", "owner_id": "u4"})
    assert response.status_code == 201, response.text
    todo = response.json()
    assert todo["title"] == "Call recruiter"
    assert todo["owner"] == {"id": "u4", "name": "Dana Diaz", "role": "team_member"}
    assert todo["due_date"] == "2026-11-18"  # today (Nov 11 company time) + 7 days
    assert todo["created_at"] == NOW_ISO
    assert todo["source_issue"] == {"id": "i1", "title": "Hiring pipeline is slow"}
    assert (todo["done"], todo["done_at"], todo["overdue"]) == (False, None, False)
    assert todo["id"].startswith("t_")

    issues = {i["id"]: i for i in (await c.get(ISSUES)).json()}
    assert issues["i1"]["todo_count"] == 1
    todos = (await c.get(f"{BASE}/todos")).json()["todos"]
    assert todo["id"] in [t["id"] for t in todos]


@pytest.mark.parametrize("owner", ["u5", "u6", "u7", "nobody"])
async def test_owner_must_be_eligible(login: Login, owner: str, db_session: AsyncSession) -> None:
    # u5 observer (team member of team_lead), u6 only in sales, u7 observer, nobody does not exist
    response = await (await login("u2")).post(f"{ISSUES}/i1/todos", json={"title": "x", "owner_id": owner})
    assert_error(response, 422, "owner_not_eligible")


async def test_eligible_owners_include_admin_and_members(login: Login) -> None:
    c = await login("u2")
    for owner in ("u1", "u2", "u3", "u4"):
        response = await c.post(f"{ISSUES}/i1/todos", json={"title": f"for {owner}", "owner_id": owner})
        assert response.status_code == 201, (owner, response.text)


async def test_todo_validation(login: Login) -> None:
    c = await login("u2")
    for body in (
        {},
        {"title": "x"},
        {"owner_id": "u2"},
        {"title": "", "owner_id": "u2"},
        {"title": "x" * 201, "owner_id": "u2"},
        {"title": "x", "owner_id": "u2", "due_date": "2026-12-01"},
    ):
        assert (await c.post(f"{ISSUES}/i1/todos", json=body)).status_code == 422, body


async def test_todo_can_be_added_to_a_solved_issue(login: Login) -> None:
    c = await login("u2")
    response = await c.post(f"{ISSUES}/i6/todos", json={"title": "Follow up", "owner_id": "u3"})
    assert response.status_code == 201
    assert response.json()["source_issue"]["id"] == "i6"


async def test_observer_cannot_change_issues_and_nothing_changes(
    login: Login, db_session: AsyncSession
) -> None:
    before = await db_snapshot(db_session)
    c = await login("u5")
    assert_error(await c.post(ISSUES, json={"title": "x"}), 403, "read_only_role")
    assert_error(await c.patch(f"{ISSUES}/i1", json={"priority": 1}), 403, "read_only_role")
    assert_error(await c.post(f"{ISSUES}/i1/solve", json={"resolution_note": "x"}), 403, "read_only_role")
    assert_error(
        await c.post(f"{ISSUES}/i1/todos", json={"title": "x", "owner_id": "u2"}), 403, "read_only_role"
    )
    assert await db_snapshot(db_session) == before
