"""WS /ws/teams/{team_id}: snapshot, meeting messages, invalidations, refcounted presence, ping, isolation.

Runs in-process (httpx-ws `ASGIWebSocketTransport`): the sockets share the event loop, the database and the
hub with the REST clients, so a REST call and the push it triggers can be asserted in one test.
"""

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from httpx_ws import AsyncWebSocketSession

from app.clock import ManualClock
from app.realtime.hub import Hub
from app.routers import ws as ws_router
from tests.api.helpers import BASE, Login, advance, start_meeting, ws_connect

Message = dict[str, Any]


async def next_message(ws: AsyncWebSocketSession, wait: float = 3.0) -> Message:
    return await ws.receive_json(timeout=wait)


async def next_of_type(ws: AsyncWebSocketSession, kind: str, wait: float = 3.0) -> Message:
    """The next message of `kind`, skipping others (invalidations and presence may interleave)."""
    async with asyncio.timeout(wait):
        while True:
            message = await ws.receive_json(timeout=wait)
            if message["type"] == kind:
                return message


async def drain(ws: AsyncWebSocketSession, quiet: float = 0.25) -> list[Message]:
    """Everything that arrives until the socket has been silent for `quiet` seconds."""
    seen: list[Message] = []
    while True:
        try:
            seen.append(await ws.receive_json(timeout=quiet))
        except TimeoutError:
            return seen


def online_map(meeting: Message) -> dict[str, bool]:
    return {a["id"]: a["online"] for a in meeting["attendees"]}


# --- snapshot and meeting messages ----------------------------------------------------------------------


async def test_every_client_gets_a_snapshot_on_connect(app: FastAPI, login: Login) -> None:
    u2, u3 = await login("u2"), await login("u3")
    async with ws_connect(app, u2) as first, ws_connect(app, u3) as second:
        assert await next_message(first) == {"type": "snapshot", "meeting": None}
        assert await next_message(second) == {"type": "snapshot", "meeting": None}


async def test_both_clients_see_the_new_section_after_the_facilitator_advances(
    app: FastAPI, login: Login
) -> None:
    u2, u3 = await login("u2"), await login("u3")
    async with ws_connect(app, u2) as facilitator_ws, ws_connect(app, u3) as teammate_ws:
        await next_message(facilitator_ws)  # snapshots
        await next_message(teammate_ws)

        meeting = await start_meeting(u2)
        for ws in (facilitator_ws, teammate_ws):
            started = (await next_of_type(ws, "meeting"))["meeting"]
            assert started["id"] == meeting["id"] and started["current_section"] == "scorecard"

        await advance(u2, meeting["id"])
        for ws in (facilitator_ws, teammate_ws):
            pushed = (await next_of_type(ws, "meeting"))["meeting"]
            assert pushed["id"] == meeting["id"]
            assert pushed["current_section"] == "ids"
            assert pushed["status"] == "active" and pushed["facilitator"]["id"] == "u2"


async def test_late_connect_gets_the_running_meeting_in_the_snapshot(
    app: FastAPI, login: Login, frozen_clock: ManualClock
) -> None:
    u2, u3 = await login("u2"), await login("u3")
    meeting = await start_meeting(u2)
    await advance(u2, meeting["id"])
    frozen_clock.advance(42)
    async with ws_connect(app, u3) as ws:  # refresh / reconnect / late join
        snapshot = await next_message(ws)
        assert snapshot["type"] == "snapshot"
        assert snapshot["meeting"]["id"] == meeting["id"]
        assert snapshot["meeting"]["current_section"] == "ids"
        assert snapshot["meeting"]["server_now"] == "2026-11-11T15:00:42Z"


async def test_join_rating_and_end_are_pushed(app: FastAPI, login: Login) -> None:
    u2, u3 = await login("u2"), await login("u3")
    meeting = await start_meeting(u2)
    async with ws_connect(app, u2) as ws:
        await next_message(ws)
        await u3.post(f"/api/meetings/{meeting['id']}/join")
        joined = (await next_of_type(ws, "meeting"))["meeting"]
        assert [a["id"] for a in joined["attendees"]] == ["u2", "u3"]
        await advance(u2, meeting["id"])
        await advance(u2, meeting["id"])
        await u2.put(f"/api/meetings/{meeting['id']}/rating", json={"rating": 9})
        async with asyncio.timeout(3):
            while True:
                rated = (await next_of_type(ws, "meeting"))["meeting"]
                if rated["ratings"]:
                    break
        assert rated["ratings"][0]["rating"] == 9 and rated["average_rating"] == 9.0
        await u2.post(f"/api/meetings/{meeting['id']}/end")
        async with asyncio.timeout(3):
            while True:
                ended = (await next_of_type(ws, "meeting"))["meeting"]
                if ended["status"] == "ended":
                    break
        assert ended["ended_at"] is not None


# --- invalidations --------------------------------------------------------------------------------------


async def test_score_edit_pushes_an_invalidation(app: FastAPI, login: Login) -> None:
    u2, u3 = await login("u2"), await login("u3")
    async with ws_connect(app, u3) as ws:
        await next_message(ws)
        await u2.put(f"{BASE}/measurables/m1/scores/2026-11-02", json={"value": 12})
        assert await next_message(ws) == {"type": "invalidate", "resources": ["scorecard"]}


@pytest.mark.parametrize(
    ("method", "path", "body", "resources"),
    [
        ("POST", "/measurables/m1/scores/2026-10-26/issue", None, ["scorecard", "issues"]),
        ("POST", "/issues", {"title": "New"}, ["issues"]),
        ("PATCH", "/issues/i1", {"priority": 1}, ["issues"]),
        ("POST", "/issues/i3/solve", {"resolution_note": "done"}, ["issues", "scorecard"]),
        ("POST", "/issues/i3/todos", {"title": "T", "owner_id": "u2"}, ["todos", "issues"]),
        ("PATCH", "/todos/t12", {"done": True}, ["todos"]),
        ("POST", "/rocks/r2/issue", None, ["issues"]),
        ("POST", "/todos/t12/issue", None, ["issues"]),
    ],
)
async def test_mutations_publish_the_right_invalidations(
    app: FastAPI, login: Login, method: str, path: str, body: Any, resources: list[str]
) -> None:
    actor, watcher = await login("u2"), await login("u3")
    async with ws_connect(app, watcher) as ws:
        await next_message(ws)
        response = await actor.request(method, BASE + path, json=body)
        assert response.status_code in (200, 201), response.text
        assert await next_message(ws) == {"type": "invalidate", "resources": resources}


async def test_failed_or_noop_requests_publish_nothing(app: FastAPI, login: Login) -> None:
    actor, watcher = await login("u2"), await login("u3")
    async with ws_connect(app, watcher) as ws:
        await next_message(ws)
        assert (await actor.post(f"{BASE}/issues", json={"title": ""})).status_code == 422
        assert (await actor.patch(f"{BASE}/issues/i6", json={"priority": 3})).status_code == 409
        assert (await actor.post(f"{BASE}/measurables/m2/scores/2026-11-02/issue")).status_code == 409
        existing = await actor.post(f"{BASE}/measurables/m1/scores/2026-11-02/issue")
        assert existing.status_code == 200  # i5 already exists: nothing changed, so nothing is pushed
        assert await drain(ws) == []


async def test_events_stay_inside_their_team(app: FastAPI, login: Login) -> None:
    lead, sales = await login("u2"), await login("u6")  # u2 is in both teams, u6 only in sales
    async with ws_connect(app, sales, "team_sales") as ws:
        await next_message(ws)
        await lead.put(f"{BASE}/measurables/m1/scores/2026-11-02", json={"value": 12})
        await start_meeting(lead, "team_lead")
        assert await drain(ws) == []
        await lead.put("/api/teams/team_sales/measurables/m6/scores/2026-11-02", json={"value": 70})
        assert await next_message(ws) == {"type": "invalidate", "resources": ["scorecard"]}


async def test_observer_can_listen(app: FastAPI, login: Login) -> None:
    async with ws_connect(app, await login("u5")) as ws:
        assert (await next_message(ws))["type"] == "snapshot"


# --- presence -------------------------------------------------------------------------------------------


async def test_presence_flips_only_when_the_users_first_or_last_socket_changes(
    app: FastAPI, login: Login
) -> None:
    u2, u3 = await login("u2"), await login("u3")
    meeting = await start_meeting(u2)
    await u3.post(f"/api/meetings/{meeting['id']}/join")  # attendee, but no socket yet

    async def u3_online() -> bool:
        return (await u2.get(f"/api/meetings/{meeting['id']}")).json()["attendees"][1]["online"]

    async with ws_connect(app, u2) as observer_ws:
        snapshot = await next_message(observer_ws)
        assert online_map(snapshot["meeting"]) == {"u2": True, "u3": False}
        assert await u3_online() is False

        async with ws_connect(app, u3) as first_tab:  # u3 comes online: everyone is told
            assert online_map((await next_of_type(observer_ws, "meeting"))["meeting"])["u3"] is True
            assert online_map((await next_message(first_tab))["meeting"])["u3"] is True
            assert await u3_online() is True

            async with ws_connect(app, u3) as second_tab:  # same user again (StrictMode, second tab)
                assert (await next_message(second_tab))["type"] == "snapshot"
                assert await drain(observer_ws) == []  # no flip, no broadcast

            # the second tab closed, the first is still open: still online, still silent
            assert await drain(observer_ws) == []
            assert await u3_online() is True

        # the last socket closed: u3 goes offline and everybody is told
        offline = (await next_of_type(observer_ws, "meeting"))["meeting"]
        assert online_map(offline)["u3"] is False
    assert await u3_online() is False


async def test_presence_without_an_active_meeting_is_silent(app: FastAPI, login: Login) -> None:
    async with ws_connect(app, await login("u2")) as ws:
        await next_message(ws)
        async with ws_connect(app, await login("u3")):
            assert await drain(ws) == []


# --- ping, hub ------------------------------------------------------------------------------------------


async def test_server_pings(app: FastAPI, login: Login, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ws_router, "PING_INTERVAL_S", 0.05)
    async with ws_connect(app, await login("u2")) as ws:
        await next_message(ws)
        assert await next_message(ws) == {"type": "ping"}
        assert await next_message(ws) == {"type": "ping"}


class FakeSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[Message] = []

    async def send_json(self, message: Message) -> None:
        if self.fail:
            raise RuntimeError("socket is dead")
        self.sent.append(message)


async def test_hub_drops_dead_sockets_without_raising() -> None:
    hub = Hub()
    good, dead = FakeSocket(), FakeSocket(fail=True)
    assert await hub.connect("t", "u1", good) is True  # type: ignore[arg-type]
    assert await hub.connect("t", "u2", dead) is True  # type: ignore[arg-type]
    assert hub.online_user_ids("t") == {"u1", "u2"}
    await hub.publish("t", {"type": "ping"})  # must not raise
    assert good.sent == [{"type": "ping"}]
    assert hub.online_user_ids("t") == {"u1"}  # the dead socket was dropped
    await hub.publish("nobody", {"type": "ping"})  # an empty team is fine


async def test_hub_refcounts_presence_per_user() -> None:
    hub = Hub()
    a, b, other = FakeSocket(), FakeSocket(), FakeSocket()
    assert await hub.connect("t", "u1", a) is True  # type: ignore[arg-type]
    assert await hub.connect("t", "u1", b) is False  # type: ignore[arg-type]
    assert await hub.connect("t", "u2", other) is True  # type: ignore[arg-type]
    assert await hub.disconnect("t", "u1", a) is False  # type: ignore[arg-type]
    assert hub.online_user_ids("t") == {"u1", "u2"}
    assert await hub.disconnect("t", "u1", b) is True  # type: ignore[arg-type]
    assert hub.online_user_ids("t") == {"u2"}
    assert await hub.disconnect("t", "u1", b) is True  # type: ignore[arg-type]  # idempotent


async def test_hub_lazy_publish_is_skipped_without_listeners_and_never_raises() -> None:
    hub = Hub()
    calls = 0

    async def build() -> Message:
        nonlocal calls
        calls += 1
        raise RuntimeError("database is down")

    await hub.publish_lazy("t", build)
    assert calls == 0  # nobody listens: not even built
    socket = FakeSocket()
    await hub.connect("t", "u1", socket)  # type: ignore[arg-type]
    await hub.publish_lazy("t", build)  # a failing builder is logged, not raised
    assert calls == 1 and socket.sent == []


# --- sub-second timestamps ------------------------------------------------------------------------------


async def test_meeting_timestamps_keep_sub_second_precision(
    app: FastAPI, login: Login, frozen_clock: ManualClock
) -> None:
    u2 = await login("u2")
    frozen_clock.advance(0.125)
    meeting = await start_meeting(u2)
    assert meeting["started_at"] == meeting["section_started_at"] == "2026-11-11T15:00:00.125000Z"
    frozen_clock.advance(3.387345)
    async with ws_connect(app, u2) as ws:
        pushed = (await next_message(ws))["meeting"]
    assert pushed["server_now"] == "2026-11-11T15:00:03.512345Z"
    assert pushed["section_started_at"] == "2026-11-11T15:00:00.125000Z"
    assert pushed["attendees"][0]["joined_at"] == "2026-11-11T15:00:00.125000Z"
    advanced = await advance(u2, meeting["id"])
    assert advanced["section_started_at"] == "2026-11-11T15:00:03.512345Z"
    assert advanced["server_now"] == "2026-11-11T15:00:03.512345Z"
