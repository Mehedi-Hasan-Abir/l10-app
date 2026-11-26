"""Teams: the switcher list and the team detail (members, permissions, business clock, active meeting)."""

import httpx

from tests.api.helpers import BASE, Login, start_meeting


async def team_ids(c: httpx.AsyncClient) -> list[str]:
    response = await c.get("/api/teams")
    assert response.status_code == 200, response.text
    return [t["id"] for t in response.json()]


async def test_team_list_per_role(login: Login) -> None:
    assert await team_ids(await login("u1")) == ["team_lead", "team_ops", "team_sales"]  # admin: every team
    assert await team_ids(await login("u2")) == ["team_lead", "team_sales"]
    assert await team_ids(await login("u5")) == ["team_lead"]  # an observer sees their own team
    assert await team_ids(await login("u9")) == ["team_ops"]
    body = (await (await login("u2")).get("/api/teams")).json()
    assert body == [{"id": "team_lead", "name": "Leadership"}, {"id": "team_sales", "name": "Sales"}]


async def test_team_detail_shape(login: Login) -> None:
    body = (await (await login("u2")).get(BASE)).json()
    assert set(body) == {"id", "name", "members", "eligible_owners", "permissions", "clock", "active_meeting"}
    assert (body["id"], body["name"]) == ("team_lead", "Leadership")
    assert {m["id"] for m in body["members"]} == {"u1", "u2", "u3", "u4", "u5"}  # observers are members too
    # eligible To-Do owners: admins and team members of the team, never observers
    assert {o["id"] for o in body["eligible_owners"]} == {"u1", "u2", "u3", "u4"}
    assert body["active_meeting"] is None
    assert body["clock"] == {
        "now": "2026-11-11T15:00:00Z",
        "today": "2026-11-11",
        "current_week": "2026-11-09",
        "last_closed_week": "2026-11-02",
        "quarter": "2026-Q4",
    }


async def test_eligible_owners_per_team(login: Login) -> None:
    admin = await login("u1")
    expected = {
        "team_lead": {"u1", "u2", "u3", "u4"},
        "team_sales": {"u1", "u2", "u6"},
        "team_ops": {"u1", "u4", "u8", "u9"},
    }
    for team, owners in expected.items():
        body = (await admin.get(f"/api/teams/{team}")).json()
        assert {o["id"] for o in body["eligible_owners"]} == owners, team


async def test_permissions_by_role(login: Login) -> None:
    full = {"can_write": True, "can_facilitate": True, "can_rate": True}
    none = {"can_write": False, "can_facilitate": False, "can_rate": False}
    assert (await (await login("u1")).get(BASE)).json()["permissions"] == full
    assert (await (await login("u2")).get(BASE)).json()["permissions"] == full
    assert (await (await login("u5")).get(BASE)).json()["permissions"] == none
    assert (await (await login("u1")).get("/api/teams/team_ops")).json()[
        "permissions"
    ] == full  # admin anywhere


async def test_active_meeting_ref_follows_the_meeting(login: Login) -> None:
    c = await login("u3")
    meeting = await start_meeting(c)
    body = (await c.get(BASE)).json()
    assert body["active_meeting"]["id"] == meeting["id"]
    await c.post(f"/api/meetings/{meeting['id']}/end")
    assert (await c.get(BASE)).json()["active_meeting"] is None


async def test_clock_follows_the_injected_clock(login: Login, frozen_clock) -> None:
    c = await login("u2")
    frozen_clock.advance(7 * 86_400)  # a week later: Nov 18
    clock = (await c.get(BASE)).json()["clock"]
    assert (clock["today"], clock["current_week"], clock["last_closed_week"]) == (
        "2026-11-18",
        "2026-11-16",
        "2026-11-09",
    )
    # the 13-week window moved with it
    card = (await c.get(f"{BASE}/scorecard")).json()
    assert card["weeks"][-1] == "2026-11-16" and card["weeks"][0] == "2026-08-24"
