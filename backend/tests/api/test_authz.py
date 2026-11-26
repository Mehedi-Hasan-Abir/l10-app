"""Server-side authorization: cross-team reads, observer writes, id probing, nested resources, WebSockets."""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx_ws import WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import COOKIE_NAME
from tests.api.helpers import (
    BASE,
    Login,
    advance,
    assert_error,
    db_snapshot,
    detail,
    join,
    start_meeting,
    ws_connect,
)

Request = tuple[str, str, Any]  # method, path, json body

# --- the endpoint catalogue ---------------------------------------------------------------------------

READS: list[Request] = [
    ("GET", f"{BASE}", None),
    ("GET", f"{BASE}/scorecard", None),
    ("GET", f"{BASE}/issues", None),
    ("GET", f"{BASE}/issues?status=solved", None),
    ("GET", f"{BASE}/todos", None),
    ("GET", f"{BASE}/rocks", None),
    ("GET", f"{BASE}/meetings", None),
    ("GET", "/api/meetings/mt1", None),
    ("GET", "/api/meetings/mt1/recap", None),
    ("GET", "/api/meetings/mt1/summary", None),
]

WRITES: list[Request] = [
    ("PUT", f"{BASE}/measurables/m1/scores/2026-11-02", {"value": 12}),
    ("PUT", f"{BASE}/measurables/m1/scores/2026-11-02", {"value": None}),
    ("POST", f"{BASE}/measurables/m1/scores/2026-10-26/issue", None),
    ("POST", f"{BASE}/rocks/r2/issue", None),
    ("POST", f"{BASE}/todos/t12/issue", None),
    ("POST", f"{BASE}/issues", {"title": "Sneaky"}),
    ("PATCH", f"{BASE}/issues/i1", {"priority": 1}),
    ("POST", f"{BASE}/issues/i1/solve", {"resolution_note": "nope"}),
    ("POST", f"{BASE}/issues/i1/todos", {"title": "Nope", "owner_id": "u2"}),
    ("PATCH", f"{BASE}/todos/t12", {"done": True}),
    ("PATCH", f"{BASE}/todos/t20", {"done": False}),
    ("POST", f"{BASE}/meetings", None),
]


def label(request: Request) -> str:
    method, path, _ = request
    return f"{method} {path.removeprefix('/api/teams/team_lead')}"


# --- a Team Member of another team ----------------------------------------------------------------------


@pytest.mark.parametrize("request_", READS, ids=label)
async def test_team_member_of_another_team_cannot_read(login: Login, request_: Request) -> None:
    method, path, body = request_
    u6 = await login("u6")  # a team_member of team_sales only
    response = await u6.request(method, path, json=body)
    assert_error(response, 403, "team_forbidden")


@pytest.mark.parametrize("request_", WRITES, ids=label)
async def test_team_member_of_another_team_cannot_write(
    login: Login, db_session: AsyncSession, request_: Request
) -> None:
    before = await db_snapshot(db_session)
    method, path, body = request_
    response = await (await login("u6")).request(method, path, json=body)
    assert_error(response, 403, "team_forbidden")
    assert await db_snapshot(db_session) == before


async def test_outsider_is_refused_on_meeting_endpoints_too(login: Login) -> None:
    meeting = await start_meeting(await login("u2"))
    u6 = await login("u6")
    for method, suffix in (
        ("GET", ""),
        ("POST", "/join"),
        ("POST", "/advance"),
        ("POST", "/end"),
        ("GET", "/recap"),
        ("GET", "/summary"),
    ):
        assert_error(
            await u6.request(method, f"/api/meetings/{meeting['id']}{suffix}"), 403, "team_forbidden"
        )
    assert_error(
        await u6.put(f"/api/meetings/{meeting['id']}/rating", json={"rating": 5}), 403, "team_forbidden"
    )


async def test_an_observer_of_another_team_is_forbidden_not_read_only(login: Login) -> None:
    u7 = await login("u7")  # observer in team_sales only
    assert_error(await u7.get(f"{BASE}/scorecard"), 403, "team_forbidden")
    assert_error(await u7.post(f"{BASE}/issues", json={"title": "x"}), 403, "team_forbidden")


# --- an Observer ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("request_", WRITES, ids=label)
async def test_observer_write_is_refused_and_changes_nothing(
    login: Login, db_session: AsyncSession, request_: Request
) -> None:
    before = await db_snapshot(db_session)
    method, path, body = request_
    response = await (await login("u5")).request(method, path, json=body)  # u5: observer in team_lead
    assert_error(response, 403, "read_only_role")
    assert await db_snapshot(db_session) == before


@pytest.mark.parametrize("request_", READS, ids=label)
async def test_observer_can_read_everything_in_their_team(login: Login, request_: Request) -> None:
    method, path, body = request_
    response = await (await login("u5")).request(method, path, json=body)
    assert response.status_code == 200, response.text


async def test_observer_meeting_powers_are_join_only(login: Login, db_session: AsyncSession) -> None:
    facilitator = await login("u2")
    meeting = await start_meeting(facilitator)
    observer = await login("u5")
    await join(observer, meeting["id"])  # joining is allowed
    assert_error(await observer.post(f"/api/meetings/{meeting['id']}/advance"), 403, "not_facilitator")
    assert_error(await observer.post(f"/api/meetings/{meeting['id']}/end"), 403, "not_facilitator")
    await advance(facilitator, meeting["id"])
    await advance(facilitator, meeting["id"])
    before = await db_snapshot(db_session)
    assert_error(
        await observer.put(f"/api/meetings/{meeting['id']}/rating", json={"rating": 9}), 403, "read_only_role"
    )
    assert await db_snapshot(db_session) == before


async def test_observer_cannot_become_facilitator_or_owner(login: Login) -> None:
    observer = await login("u5")
    assert_error(await observer.post(f"{BASE}/meetings"), 403, "read_only_role")
    # an observer is not an eligible owner for a To-Do even when an eligible user asks for it
    response = await (await login("u2")).post(
        f"{BASE}/issues/i1/todos", json={"title": "x", "owner_id": "u5"}
    )
    assert_error(response, 422, "owner_not_eligible")


# --- unknown teams and id probing -------------------------------------------------------------------------


@pytest.mark.parametrize("request_", [*READS[:7], *WRITES[:12]], ids=label)
async def test_unknown_team_non_admin_403_admin_404(login: Login, request_: Request) -> None:
    method, path, body = request_
    path = path.replace("team_lead", "no_such_team")
    non_admin = await (await login("u2")).request(method, path, json=body)
    assert_error(non_admin, 403, "team_forbidden")
    admin = await (await login("u1")).request(method, path, json=body)
    assert_error(admin, 404, "team_not_found")


async def test_nonexistent_team_looks_exactly_like_a_forbidden_one(login: Login) -> None:
    u2 = await login("u2")  # not a member of team_ops
    forbidden = await u2.get("/api/teams/team_ops/scorecard")
    missing = await u2.get("/api/teams/no_such_team/scorecard")
    assert (forbidden.status_code, forbidden.json()) == (missing.status_code, missing.json())
    assert detail(missing)["code"] == "team_forbidden"


async def test_admin_can_work_in_every_team(login: Login) -> None:
    admin = await login("u1")
    for team in ("team_lead", "team_sales", "team_ops"):
        assert (await admin.get(f"/api/teams/{team}/scorecard")).status_code == 200
        created = await admin.post(f"/api/teams/{team}/issues", json={"title": f"Admin issue in {team}"})
        assert created.status_code == 201 and created.json()["team_id"] == team
    detail_body = (await admin.get("/api/teams")).json()
    assert {t["id"] for t in detail_body} == {"team_lead", "team_sales", "team_ops"}


# --- nested resources of another team ---------------------------------------------------------------------


NESTED_THROUGH_TEAM_LEAD: list[tuple[Request, str]] = [
    (("PATCH", f"{BASE}/issues/i8", {"priority": 1}), "issue_not_found"),  # i8 is team_sales'
    (("POST", f"{BASE}/issues/i8/solve", {"resolution_note": "x"}), "issue_not_found"),
    (("POST", f"{BASE}/issues/i8/todos", {"title": "x", "owner_id": "u2"}), "issue_not_found"),
    (("PATCH", f"{BASE}/todos/t40", {"done": True}), "todo_not_found"),  # t40 is team_sales'
    (("POST", f"{BASE}/todos/t40/issue", None), "todo_not_found"),
    (("PUT", f"{BASE}/measurables/m6/scores/2026-11-02", {"value": 1}), "measurable_not_found"),
    (("POST", f"{BASE}/measurables/m6/scores/2026-11-02/issue", None), "measurable_not_found"),
    (("POST", f"{BASE}/rocks/r6/issue", None), "rock_not_found"),
]


@pytest.mark.parametrize(
    ("request_", "code"), NESTED_THROUGH_TEAM_LEAD, ids=lambda v: label(v) if isinstance(v, tuple) else v
)
async def test_other_teams_resource_via_my_teams_path_is_404(
    login: Login, db_session: AsyncSession, request_: Request, code: str
) -> None:
    before = await db_snapshot(db_session)
    method, path, body = request_
    # u2 belongs to BOTH teams, so only the path decides whose resource is addressed
    assert_error(await (await login("u2")).request(method, path, json=body), 404, code)
    assert await db_snapshot(db_session) == before


async def test_team_leads_resource_via_the_sales_path_is_404(login: Login) -> None:
    u2 = await login("u2")
    sales = "/api/teams/team_sales"
    assert_error(await u2.patch(f"{sales}/issues/i1", json={"priority": 1}), 404, "issue_not_found")
    assert_error(await u2.patch(f"{sales}/todos/t12", json={"done": True}), 404, "todo_not_found")
    assert_error(
        await u2.put(f"{sales}/measurables/m1/scores/2026-11-02", json={"value": 1}),
        404,
        "measurable_not_found",
    )
    assert_error(await u2.post(f"{sales}/rocks/r1/issue"), 404, "rock_not_found")


async def test_unknown_and_foreign_meetings_look_the_same_to_non_admins(login: Login) -> None:
    u3 = await login("u3")  # team_lead only
    sales_meeting = await start_meeting(await login("u6"), "team_sales")
    # No id probing: "doesn't exist" and "not your team" are the same 403 for non-admins.
    assert_error(await u3.get("/api/meetings/mt_nope"), 403, "team_forbidden")
    assert_error(await u3.get(f"/api/meetings/{sales_meeting['id']}"), 403, "team_forbidden")
    # Admins can see every team, so for them an unknown id is simply 404.
    assert_error(await (await login("u1")).get("/api/meetings/mt_nope"), 404, "meeting_not_found")


# --- unauthenticated --------------------------------------------------------------------------------------


@pytest.mark.parametrize("request_", [*READS, *WRITES], ids=label)
async def test_unauthenticated_requests_are_401(client: httpx.AsyncClient, request_: Request) -> None:
    method, path, body = request_
    assert_error(await client.request(method, path, json=body), 401, "not_authenticated")


async def test_meeting_actions_need_a_session(client: httpx.AsyncClient) -> None:
    for method, suffix in (("POST", "/join"), ("POST", "/advance"), ("POST", "/end"), ("PUT", "/rating")):
        response = await client.request(method, f"/api/meetings/mt1{suffix}", json={"rating": 5})
        assert_error(response, 401, "not_authenticated")


async def test_a_forged_cookie_is_401(client: httpx.AsyncClient) -> None:
    client.cookies.set(COOKIE_NAME, "u1")
    assert_error(await client.get(f"{BASE}/scorecard"), 401, "not_authenticated")


# --- WebSocket handshake ----------------------------------------------------------------------------------


async def refusal_code(app: FastAPI, client: httpx.AsyncClient, team: str = "team_lead", **kw: Any) -> int:
    """Open a socket that must be refused; the close code the browser would see."""
    async with ws_connect(app, client, team, **kw) as ws:
        try:
            first = await ws.receive_json(timeout=2)  # nothing but the close may arrive
        except WebSocketDisconnect as refused:
            return refused.code
    raise AssertionError(f"the server accepted the socket and sent {first!r}")


async def test_websocket_requires_a_session(app: FastAPI, client: httpx.AsyncClient) -> None:
    assert await refusal_code(app, client, cookies=False) == 4401


async def test_websocket_rejects_a_tampered_cookie(app: FastAPI, login: Login) -> None:
    c = await login("u2")
    cookie = c.cookies[COOKIE_NAME]
    # Change the first character: it decodes to payload bits, so the signature can never still match. (The
    # LAST signature character is not safe to flip: its low bits are base64 padding and may be ignored.)
    c.cookies.set(COOKIE_NAME, ("x" if cookie[0] != "x" else "y") + cookie[1:])
    assert await refusal_code(app, c) == 4401


async def test_websocket_rejects_a_foreign_origin(app: FastAPI, login: Login) -> None:
    c = await login("u2")  # a valid cookie, but the page that opens the socket is on another site
    assert await refusal_code(app, c, origin="https://evil.example") == 4403


async def test_websocket_rejects_non_members_and_unknown_teams(app: FastAPI, login: Login) -> None:
    assert await refusal_code(app, await login("u6")) == 4403  # a member of team_sales, not team_lead
    assert await refusal_code(app, await login("u2"), "no_such_team") == 4403
    assert await refusal_code(app, await login("u1"), "no_such_team") == 4403  # even an admin gets no detail


async def test_websocket_allows_members_observers_admins_and_non_browser_clients(
    app: FastAPI, login: Login
) -> None:
    for user in ("u2", "u5", "u1"):
        async with ws_connect(app, await login(user)) as ws:
            assert (await ws.receive_json(timeout=2))["type"] == "snapshot"
    async with ws_connect(app, await login("u3"), origin=None) as ws:  # no Origin header: not a browser
        assert (await ws.receive_json(timeout=2))["type"] == "snapshot"
    async with ws_connect(app, await login("u1"), "team_ops") as ws:  # an admin may listen to any team
        assert (await ws.receive_json(timeout=2))["type"] == "snapshot"
