"""Rocks: current quarter, computed status/progress, milestones; bonus Make it an Issue (off-track Rock)."""

import httpx

from tests.api.helpers import BASE, Login, assert_error

ROCKS = f"{BASE}/rocks"


async def get_rocks(c: httpx.AsyncClient, team: str = "team_lead") -> dict:
    response = await c.get(f"/api/teams/{team}/rocks")
    assert response.status_code == 200, response.text
    return response.json()


def by_id(body: dict) -> dict[str, dict]:
    return {r["id"]: r for r in body["rocks"]}


async def test_current_quarter_rocks_with_computed_values(login: Login) -> None:
    body = await get_rocks(await login("u2"))
    assert body["quarter"] == "2026-Q4"
    assert [r["id"] for r in body["rocks"]] == ["r1", "r2", "r3", "r4", "r5"]  # seed order
    rocks = by_id(body)
    expected = {
        "r1": ("on_track", "on_track", 33),  # ms2 is due today: not past due
        "r2": ("on_track", "off_track", 13),  # ms5 due yesterday; 1 of 8 milestones = 12.5 -> 13
        "r3": ("complete", "complete", 100),
        "r4": ("off_track", "off_track", 0),
        "r5": ("on_track", "on_track", 0),
    }
    for rock_id, (seeded, status, progress) in expected.items():
        assert (
            rocks[rock_id]["seeded_status"],
            rocks[rock_id]["status"],
            rocks[rock_id]["progress_percent"],
        ) == (
            seeded,
            status,
            progress,
        ), rock_id


async def test_rock_shape_and_milestones(login: Login) -> None:
    rocks = by_id(await get_rocks(await login("u2")))
    r1 = rocks["r1"]
    assert r1["title"] == "Launch partner portal"
    assert r1["owner"] == {"id": "u2", "name": "Blake Brooks", "role": "team_member"}
    assert r1["milestones"] == [
        {"id": "ms1", "title": "Partner API spec", "due_date": "2026-10-15", "done": True, "past_due": False},
        {"id": "ms2", "title": "Portal beta", "due_date": "2026-11-11", "done": False, "past_due": False},
        {"id": "ms3", "title": "Portal launch", "due_date": "2026-12-01", "done": False, "past_due": False},
    ]
    r2 = rocks["r2"]
    assert len(r2["milestones"]) == 8
    assert {m["id"] for m in r2["milestones"] if m["past_due"]} == {"ms5"}
    assert rocks["r3"]["milestones"] == []


async def test_other_teams_rocks(login: Login) -> None:
    sales = await get_rocks(await login("u6"), "team_sales")
    assert [r["id"] for r in sales["rocks"]] == ["r6"]
    assert (sales["rocks"][0]["status"], sales["rocks"][0]["progress_percent"]) == ("on_track", 0)
    ops = await get_rocks(await login("u8"), "team_ops")
    assert [r["id"] for r in ops["rocks"]] == ["r7"]


async def test_observer_can_read_and_rocks_are_read_only(login: Login) -> None:
    c = await login("u5")
    assert len((await get_rocks(c))["rocks"]) == 5
    # there is no write endpoint for rocks or milestones
    for method in ("PUT", "PATCH", "DELETE"):
        response = await c.request(method, f"{ROCKS}/r1", json={})
        assert response.status_code in (404, 405), (method, response.status_code)
    assert (await c.post(ROCKS, json={})).status_code == 405  # the path exists, but only for GET


# --- bonus: Make it an Issue from a Rock ----------------------------------------------------------------


async def test_make_issue_from_off_track_rock(login: Login) -> None:
    c = await login("u2")
    first = await c.post(f"{ROCKS}/r2/issue")
    assert first.status_code == 201, first.text
    issue = first.json()["issue"]
    assert first.json()["created"] is True
    assert issue["title"] == "Hire VP of Sales (Rock off track)"
    assert issue["owner"]["id"] == "u2" and issue["priority"] is None
    assert issue["source"] == {"type": "rock", "rock_id": "r2", "rock_title": "Hire VP of Sales"}

    second = await (await login("u3")).post(f"{ROCKS}/r2/issue")
    assert second.status_code == 200
    assert second.json() == {"issue": issue, "created": False}

    # a rock that is off track only because it was seeded that way (no milestones) works too
    seeded_off = await c.post(f"{ROCKS}/r4/issue")
    assert (
        seeded_off.status_code == 201
        and seeded_off.json()["issue"]["title"] == "Refresh brand (Rock off track)"
    )


async def test_make_issue_requires_an_off_track_rock(login: Login) -> None:
    c = await login("u2")
    assert_error(await c.post(f"{ROCKS}/r1/issue"), 409, "not_off_track")  # on track
    assert_error(await c.post(f"{ROCKS}/r3/issue"), 409, "not_off_track")  # complete
    assert_error(await c.post(f"{ROCKS}/r6/issue"), 404, "rock_not_found")  # team_sales' rock
    assert_error(await c.post(f"{ROCKS}/r_nope/issue"), 404, "rock_not_found")
    assert_error(await (await login("u5")).post(f"{ROCKS}/r2/issue"), 403, "read_only_role")


async def test_solving_the_rock_issue_allows_a_new_one(login: Login) -> None:
    c = await login("u2")
    first = (await c.post(f"{ROCKS}/r2/issue")).json()["issue"]
    assert (
        await c.post(f"{BASE}/issues/{first['id']}/solve", json={"resolution_note": "plan agreed"})
    ).status_code == 200
    again = await c.post(f"{ROCKS}/r2/issue")
    assert again.status_code == 201 and again.json()["issue"]["id"] != first["id"]
