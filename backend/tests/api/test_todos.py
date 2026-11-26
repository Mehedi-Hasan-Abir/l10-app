"""To-Dos: list order, overdue flags, last-closed-week completion, done toggle, overdue -> Issue (bonus)."""

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Todo
from tests.api.helpers import BASE, NOW_ISO, Login, assert_error, db_snapshot

TODOS = f"{BASE}/todos"


async def get_todos(c: httpx.AsyncClient, team: str = "team_lead") -> dict:
    response = await c.get(f"/api/teams/{team}/todos")
    assert response.status_code == 200, response.text
    return response.json()


def by_id(body: dict) -> dict[str, dict]:
    return {t["id"]: t for t in body["todos"]}


async def test_list_shape_and_completion_for_the_last_closed_week(login: Login) -> None:
    body = await get_todos(await login("u2"))
    assert set(body) == {"todos", "last_closed_week", "completion"}
    assert body["last_closed_week"] == "2026-11-02"
    # 10 due that week; t29 (done Mon 00:01 local) does not count, t28 (Sun 23:59 local) does: 9/10 = 90%
    assert body["completion"] == {
        "week_start": "2026-11-02",
        "due": 10,
        "done": 9,
        "rate_percent": 90,
        "meets_target": True,
    }
    assert len(body["todos"]) == 18  # every team_lead to-do (20 seeded, minus sales t40 and ops t41)


async def test_todo_shape(login: Login) -> None:
    todos = by_id(await get_todos(await login("u2")))
    assert todos["t30"] == {
        "id": "t30",
        "team_id": "team_lead",
        "title": "Set payroll reminder",
        "owner": {"id": "u3", "name": "Casey Cho", "role": "team_member"},
        "due_date": "2026-11-11",
        "done_at": None,
        "done": False,
        "overdue": False,  # due today is not overdue
        "source_issue": {"id": "i6", "title": "Payroll runs, week of Oct 26"},
        "created_at": "2026-11-04T15:45:00Z",
    }
    assert todos["t20"]["done"] is True and todos["t20"]["done_at"] == "2026-11-03T20:00:00Z"
    assert todos["t20"]["source_issue"]["id"] == "i7"
    assert todos["t1"]["source_issue"] is None


async def test_overdue_flags(login: Login) -> None:
    todos = by_id(await get_todos(await login("u2")))
    assert {tid for tid, t in todos.items() if t["overdue"]} == {"t12", "t31"}
    # a done to-do is never overdue, however old its due date
    assert todos["t1"]["overdue"] is False


async def test_order_open_by_due_date_then_done_newest_first(login: Login) -> None:
    body = await get_todos(await login("u2"))
    order = [t["id"] for t in body["todos"]]
    assert order[:4] == ["t12", "t31", "t30", "t32"]  # open: due 11-01, 11-10, 11-11, 11-13
    assert all(not t["done"] for t in body["todos"][:4])
    done = body["todos"][4:]
    assert all(t["done"] for t in done)
    done_times = [t["done_at"] for t in done]
    assert done_times == sorted(done_times, reverse=True)
    assert done[0]["id"] == "t29"  # done 2026-11-09T06:01Z, the latest


async def test_other_team_lists(login: Login) -> None:
    sales = await get_todos(await login("u6"), "team_sales")
    assert [t["id"] for t in sales["todos"]] == ["t40"]
    assert sales["completion"] is None  # nothing due in the last closed week
    ops = await get_todos(await login("u8"), "team_ops")
    assert ops["completion"] == {
        "week_start": "2026-11-02",
        "due": 1,
        "done": 1,
        "rate_percent": 100,
        "meets_target": True,
    }


async def test_observer_can_read(login: Login) -> None:
    assert len((await get_todos(await login("u5")))["todos"]) == 18


# --- done toggle ----------------------------------------------------------------------------------------


async def test_mark_done_sets_done_at_to_now(login: Login) -> None:
    c = await login("u2")
    response = await c.patch(f"{TODOS}/t12", json={"done": True})
    assert response.status_code == 200, response.text
    todo = response.json()
    assert (todo["done"], todo["done_at"], todo["overdue"]) == (True, NOW_ISO, False)
    assert by_id(await get_todos(c))["t12"]["done_at"] == NOW_ISO


async def test_done_is_idempotent_and_keeps_the_first_done_time(login: Login, frozen_clock) -> None:
    c = await login("u2")
    await c.patch(f"{TODOS}/t12", json={"done": True})
    frozen_clock.advance(3600)
    again = await c.patch(f"{TODOS}/t12", json={"done": True})
    assert again.status_code == 200 and again.json()["done_at"] == NOW_ISO


async def test_undo_clears_done_at_and_is_idempotent(login: Login) -> None:
    c = await login("u2")
    first = await c.patch(f"{TODOS}/t20", json={"done": False})
    assert first.status_code == 200
    assert (first.json()["done"], first.json()["done_at"]) == (False, None)
    assert (await c.patch(f"{TODOS}/t20", json={"done": False})).json()["done_at"] is None
    # t20 is due 2026-11-04 and no longer done, so it is overdue again
    assert first.json()["overdue"] is True


async def test_toggling_a_todo_moves_the_completion_rate(login: Login) -> None:
    c = await login("u2")
    await c.patch(f"{TODOS}/t27", json={"done": False})  # due 2026-11-07, was done on time
    completion = (await get_todos(c))["completion"]
    assert completion == {
        "week_start": "2026-11-02",
        "due": 10,
        "done": 8,
        "rate_percent": 80,
        "meets_target": False,
    }
    await c.patch(f"{TODOS}/t27", json={"done": True})  # done now (2026-11-11), after the due week
    completion = (await get_todos(c))["completion"]
    assert completion["done"] == 8  # done after its due week still counts as not done


@pytest.mark.parametrize("body", [{}, {"done": "yes"}, {"done": None}, {"done": True, "title": "x"}])
async def test_done_body_validation(login: Login, body: dict) -> None:
    assert (await (await login("u2")).patch(f"{TODOS}/t12", json=body)).status_code == 422


async def test_unknown_and_other_teams_todo_is_404(login: Login) -> None:
    c = await login("u2")
    assert_error(await c.patch(f"{TODOS}/t_nope", json={"done": True}), 404, "todo_not_found")
    # t40 belongs to team_sales
    assert_error(await c.patch(f"{TODOS}/t40", json={"done": True}), 404, "todo_not_found")


async def test_observer_cannot_toggle(login: Login, db_session: AsyncSession) -> None:
    before = await db_snapshot(db_session)
    response = await (await login("u5")).patch(f"{TODOS}/t12", json={"done": True})
    assert_error(response, 403, "read_only_role")
    assert await db_snapshot(db_session) == before
    row = await db_session.get(Todo, "t12", populate_existing=True)
    assert row is not None and row.done_at is None


# --- bonus: Make it an Issue from an overdue To-Do ------------------------------------------------------


async def test_make_issue_from_overdue_todo(login: Login) -> None:
    c = await login("u3")
    first = await c.post(f"{TODOS}/t12/issue")
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["created"] is True
    issue = body["issue"]
    assert issue["title"] == "Publish hiring ad (To-Do overdue)"
    assert issue["owner"]["id"] == "u3" and issue["priority"] is None
    assert issue["source"] == {"type": "todo", "todo_id": "t12", "todo_title": "Publish hiring ad"}

    second = await (await login("u2")).post(f"{TODOS}/t12/issue")
    assert second.status_code == 200
    assert second.json() == {"issue": issue, "created": False}


async def test_make_issue_from_todo_requires_overdue(login: Login) -> None:
    c = await login("u2")
    assert_error(await c.post(f"{TODOS}/t30/issue"), 409, "not_overdue")  # due today
    assert_error(await c.post(f"{TODOS}/t32/issue"), 409, "not_overdue")  # due in the future
    assert_error(await c.post(f"{TODOS}/t20/issue"), 409, "not_overdue")  # done
    assert_error(await c.post(f"{TODOS}/t40/issue"), 404, "todo_not_found")  # other team
    assert_error(await (await login("u5")).post(f"{TODOS}/t12/issue"), 403, "read_only_role")


async def test_a_new_issue_can_be_made_after_the_first_is_solved(login: Login) -> None:
    c = await login("u2")
    first = (await c.post(f"{TODOS}/t12/issue")).json()["issue"]
    solved = await c.post(f"{BASE}/issues/{first['id']}/solve", json={"resolution_note": "handled"})
    assert solved.status_code == 200
    again = await c.post(f"{TODOS}/t12/issue")
    assert again.status_code == 201 and again.json()["issue"]["id"] != first["id"]
