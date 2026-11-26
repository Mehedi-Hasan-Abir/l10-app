"""The meeting state machine: start, join, advance, rate, end, recap, and the frozen summary snapshot."""

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import ManualClock
from app.models import Meeting, MeetingAttendee
from app.rules import AGENDA, section_timing
from tests.api.helpers import (
    BASE,
    NOW_ISO,
    Login,
    advance,
    assert_error,
    db_snapshot,
    end_meeting,
    join,
    make_issue,
    put_score,
    start_meeting,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "expected_rules_report.json"


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone(UTC)


async def state(c: httpx.AsyncClient, meeting_id: str) -> dict[str, Any]:
    response = await c.get(f"/api/meetings/{meeting_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def rate(c: httpx.AsyncClient, meeting_id: str, rating: Any) -> httpx.Response:
    return await c.put(f"/api/meetings/{meeting_id}/rating", json={"rating": rating})


async def summary(c: httpx.AsyncClient, meeting_id: str) -> httpx.Response:
    return await c.get(f"/api/meetings/{meeting_id}/summary")


async def to_conclude(c: httpx.AsyncClient, meeting_id: str) -> None:
    await advance(c, meeting_id)
    await advance(c, meeting_id)


# --- start ----------------------------------------------------------------------------------------------


async def test_start_creates_an_active_meeting_with_the_caller_as_facilitator(login: Login) -> None:
    c = await login("u2")
    response = await c.post(f"{BASE}/meetings")
    assert response.status_code == 201, response.text
    m = response.json()
    assert m["id"].startswith("mt_")
    assert m["team_id"] == "team_lead" and m["status"] == "active"
    assert m["facilitator"] == {"id": "u2", "name": "Blake Brooks", "role": "team_member"}
    assert m["started_at"] == NOW_ISO and m["ended_at"] is None
    assert m["scorecard_week"] == "2026-11-02"  # the last closed week
    assert m["current_section"] == "scorecard" and m["section_started_at"] == NOW_ISO
    assert m["server_now"] == NOW_ISO
    assert m["agenda"] == [
        {"key": "scorecard", "title": "Scorecard", "duration_seconds": 300},
        {"key": "ids", "title": "IDS", "duration_seconds": 3600},
        {"key": "conclude", "title": "Conclude", "duration_seconds": 600},
    ]
    assert m["agenda"] == [{"key": k, "title": t, "duration_seconds": d} for k, t, d in AGENDA]
    assert m["attendees"] == [
        {"id": "u2", "name": "Blake Brooks", "role": "team_member", "joined_at": NOW_ISO, "online": False}
    ]
    assert (m["ratings"], m["average_rating"]) == ([], None)


async def test_start_is_visible_in_team_detail_and_lists(login: Login) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    detail = (await (await login("u3")).get(BASE)).json()
    assert detail["active_meeting"] == {
        "id": meeting["id"],
        "facilitator": {"id": "u2", "name": "Blake Brooks", "role": "team_member"},
        "current_section": "scorecard",
    }
    listed = (await c.get(f"{BASE}/meetings", params={"status": "active"})).json()
    assert [m["id"] for m in listed] == [meeting["id"]]


async def test_second_start_is_409_with_the_active_meeting_id(login: Login) -> None:
    c, other = await login("u2"), await login("u3")
    meeting = await start_meeting(c)
    for client in (c, other):
        found = assert_error(await client.post(f"{BASE}/meetings"), 409, "meeting_already_active")
        assert found["meeting_id"] == meeting["id"]


async def test_concurrent_double_start_yields_exactly_one_meeting(
    login: Login, db_session: AsyncSession
) -> None:
    clients = [await login(u) for u in ("u2", "u3", "u4", "u1")]
    responses = await asyncio.gather(*(c.post(f"{BASE}/meetings") for c in clients for _ in range(2)))
    assert Counter(r.status_code for r in responses) == {201: 1, 409: 7}
    winner = next(r.json() for r in responses if r.status_code == 201)
    assert {r.json()["detail"]["meeting_id"] for r in responses if r.status_code == 409} == {winner["id"]}
    db_session.expire_all()
    active = (await db_session.scalars(select(Meeting).where(Meeting.ended_at.is_(None)))).all()
    assert [m.id for m in active] == [winner["id"]]
    # only the winner was auto-joined: the losers' transactions were rolled back
    attendees = (
        await db_session.scalars(select(MeetingAttendee).where(MeetingAttendee.meeting_id == winner["id"]))
    ).all()
    assert [a.user_id for a in attendees] == [winner["facilitator"]["id"]]


async def test_teams_are_independent_and_admin_can_facilitate_anywhere(login: Login) -> None:
    sales = await start_meeting(await login("u6"), "team_sales")
    ops = await start_meeting(await login("u1"), "team_ops")  # admin, not a member of team_ops
    assert ops["facilitator"]["id"] == "u1" and sales["team_id"] == "team_sales"


async def test_observers_and_non_members_cannot_start(login: Login, db_session: AsyncSession) -> None:
    before = await db_snapshot(db_session)
    assert_error(await (await login("u5")).post(f"{BASE}/meetings"), 403, "read_only_role")
    assert_error(await (await login("u6")).post(f"{BASE}/meetings"), 403, "team_forbidden")
    assert_error(await (await login("u7")).post(f"{BASE}/meetings"), 403, "team_forbidden")
    assert await db_snapshot(db_session) == before


# --- join -----------------------------------------------------------------------------------------------


async def test_join_is_idempotent_and_keeps_the_first_joined_at(
    login: Login, frozen_clock: ManualClock
) -> None:
    meeting = await start_meeting(await login("u2"))
    c = await login("u3")
    frozen_clock.advance(30)
    first = await join(c, meeting["id"])
    assert [a["id"] for a in first["attendees"]] == ["u2", "u3"]
    frozen_clock.advance(30)
    second = await join(c, meeting["id"])
    assert second["attendees"] == first["attendees"]  # unchanged, joined_at not rewritten
    assert next(a for a in second["attendees"] if a["id"] == "u3")["joined_at"] == "2026-11-11T15:00:30Z"


async def test_observers_and_admins_can_join(login: Login) -> None:
    meeting = await start_meeting(await login("u2"))
    after_observer = await join(await login("u5"), meeting["id"])
    after_admin = await join(await login("u1"), meeting["id"])
    assert [a["id"] for a in after_observer["attendees"]] == ["u2", "u5"]
    assert {a["id"]: a["role"] for a in after_admin["attendees"]} == {
        "u2": "team_member",
        "u5": "observer",
        "u1": "admin",
    }


async def test_non_members_cannot_join_or_read(login: Login) -> None:
    meeting = await start_meeting(await login("u2"))
    outsider = await login("u6")
    for method, path in (
        ("POST", f"/api/meetings/{meeting['id']}/join"),
        ("GET", f"/api/meetings/{meeting['id']}"),
        ("GET", f"/api/meetings/{meeting['id']}/recap"),
        ("GET", f"/api/meetings/{meeting['id']}/summary"),
    ):
        assert_error(await outsider.request(method, path), 403, "team_forbidden")


async def test_join_unknown_and_ended(login: Login) -> None:
    c = await login("u2")
    assert_error(await c.post("/api/meetings/mt_nope/join"), 403, "team_forbidden")  # no id probing
    assert_error(await (await login("u1")).post("/api/meetings/mt_nope/join"), 404, "meeting_not_found")
    meeting = await start_meeting(c)
    await end_meeting(c, meeting["id"])
    assert_error(await (await login("u3")).post(f"/api/meetings/{meeting['id']}/join"), 409, "meeting_ended")
    assert_error(await c.post("/api/meetings/mt1/join"), 409, "meeting_ended")  # a seeded, ended meeting


# --- advance, timer -------------------------------------------------------------------------------------


async def test_facilitator_advances_through_the_agenda(login: Login, frozen_clock: ManualClock) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    frozen_clock.advance(120)
    ids = await advance(c, meeting["id"])
    assert ids["current_section"] == "ids"
    assert ids["section_started_at"] == "2026-11-11T15:02:00Z"  # the new section starts now
    assert ids["started_at"] == NOW_ISO  # the meeting itself did not move
    frozen_clock.advance(60)
    conclude = await advance(c, meeting["id"])
    assert (conclude["current_section"], conclude["section_started_at"]) == (
        "conclude",
        "2026-11-11T15:03:00Z",
    )
    assert_error(await c.post(f"/api/meetings/{meeting['id']}/advance"), 409, "no_next_section")
    assert (await state(c, meeting["id"]))["current_section"] == "conclude"


async def test_advance_with_a_stale_from_section_is_409_and_never_skips(login: Login) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    url = f"/api/meetings/{meeting['id']}/advance"
    # A double-click: both requests say "I was looking at scorecard". Only the first one may advance.
    first, second = await asyncio.gather(
        c.post(url, json={"from_section": "scorecard"}), c.post(url, json={"from_section": "scorecard"})
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409]
    loser = first if first.status_code == 409 else second
    assert loser.json()["detail"]["code"] == "stale_section"
    assert loser.json()["detail"]["current_section"] == "ids"
    assert (await state(c, meeting["id"]))["current_section"] == "ids"  # not skipped to conclude
    # The matching section advances; an unknown section or extra field is a 422.
    assert (await c.post(url, json={"from_section": "ids"})).json()["current_section"] == "conclude"
    assert (await c.post(url, json={"from_section": "nowhere"})).status_code == 422
    assert (await c.post(url, json={"from_section": "conclude", "extra": 1})).status_code == 422


async def test_only_the_facilitator_may_advance(login: Login, db_session: AsyncSession) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    for user in ("u3", "u1", "u5"):  # teammate, an admin who is NOT the facilitator, an observer
        other = await login(user)
        await join(other, meeting["id"])
        assert_error(await other.post(f"/api/meetings/{meeting['id']}/advance"), 403, "not_facilitator")
    assert_error(
        await (await login("u6")).post(f"/api/meetings/{meeting['id']}/advance"), 403, "team_forbidden"
    )
    assert (await state(c, meeting["id"]))["current_section"] == "scorecard"


async def test_timer_is_derived_from_server_state_and_overrun_never_advances(
    login: Login, frozen_clock: ManualClock
) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)

    def timing(m: dict[str, Any]) -> tuple[int, bool]:
        duration = next(a["duration_seconds"] for a in m["agenda"] if a["key"] == m["current_section"])
        return section_timing(parse(m["section_started_at"]), parse(m["server_now"]), duration)

    assert timing(meeting) == (0, False)
    frozen_clock.advance(299)
    assert timing(await state(c, meeting["id"])) == (299, False)
    frozen_clock.advance(1)
    assert timing(await state(c, meeting["id"])) == (300, False)  # exactly 5:00 is not over time
    frozen_clock.advance(1)
    late = await state(c, meeting["id"])
    assert timing(late) == (301, True)
    assert late["current_section"] == "scorecard"  # the server never advances on its own
    frozen_clock.advance(3600)
    assert (await state(c, meeting["id"]))["current_section"] == "scorecard"
    # advancing restarts the timer for the next section
    ids = await advance(c, meeting["id"])
    assert timing(ids) == (0, False)
    frozen_clock.advance(3601)
    assert timing(await state(c, meeting["id"])) == (3601, True)


async def test_late_join_and_refresh_see_the_current_state(login: Login, frozen_clock: ManualClock) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    await advance(c, meeting["id"])
    frozen_clock.advance(90)
    late = await join(await login("u4"), meeting["id"])
    assert late["current_section"] == "ids"
    assert (parse(late["server_now"]) - parse(late["section_started_at"])).total_seconds() == 90
    assert late["facilitator"]["id"] == "u2"  # a refresh by the facilitator gets the same state back
    assert await state(c, meeting["id"]) == late


# --- rating ---------------------------------------------------------------------------------------------


async def test_rating_only_in_conclude(login: Login) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    assert_error(await rate(c, meeting["id"], 8), 409, "not_in_conclude")  # scorecard
    await advance(c, meeting["id"])
    assert_error(await rate(c, meeting["id"], 8), 409, "not_in_conclude")  # ids
    await advance(c, meeting["id"])
    response = await rate(c, meeting["id"], 8)
    assert response.status_code == 200, response.text
    assert response.json()["ratings"] == [{"user_id": "u2", "name": "Blake Brooks", "rating": 8}]
    assert response.json()["average_rating"] == 8.0


async def test_rating_can_be_changed_until_the_end_and_averages_half_up(login: Login) -> None:
    facilitator = await login("u2")
    meeting = await start_meeting(facilitator)
    others = {u: await login(u) for u in ("u3", "u4", "u1")}
    for client in others.values():
        await join(client, meeting["id"])
    await to_conclude(facilitator, meeting["id"])

    await rate(facilitator, meeting["id"], 8)
    await rate(others["u3"], meeting["id"], 9)
    await rate(others["u4"], meeting["id"], 8)
    final = (await rate(others["u1"], meeting["id"], 8)).json()
    assert {r["user_id"]: r["rating"] for r in final["ratings"]} == {"u1": 8, "u2": 8, "u3": 9, "u4": 8}
    assert final["average_rating"] == 8.3  # 33 / 4 = 8.25 -> 8.3 (half up)

    changed = (await rate(others["u3"], meeting["id"], 10)).json()  # 34 / 4 = 8.5
    assert changed["average_rating"] == 8.5
    assert len(changed["ratings"]) == 4  # an upsert, not a second row
    assert (await state(facilitator, meeting["id"]))["average_rating"] == 8.5

    ended = await end_meeting(facilitator, meeting["id"])
    assert ended["average_rating"] == 8.5
    assert_error(await rate(others["u3"], meeting["id"], 1), 409, "meeting_ended")


async def test_rating_requires_having_joined(login: Login) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    await to_conclude(c, meeting["id"])
    never_joined = await login("u3")  # a team member who has not opened the meeting
    assert_error(await rate(never_joined, meeting["id"], 7), 409, "not_attendee")
    await join(never_joined, meeting["id"])
    assert (await rate(never_joined, meeting["id"], 7)).status_code == 200


async def test_observer_can_join_but_not_rate(login: Login, db_session: AsyncSession) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    observer = await login("u5")
    await join(observer, meeting["id"])
    await to_conclude(c, meeting["id"])
    assert_error(await rate(observer, meeting["id"], 9), 403, "read_only_role")
    assert (await state(observer, meeting["id"]))["ratings"] == []
    # and an outsider is refused before anything else
    assert_error(await rate(await login("u6"), meeting["id"], 9), 403, "team_forbidden")


@pytest.mark.parametrize("rating", [0, 11, -1, 5.5, "8", None, True])
async def test_rating_range_and_type(login: Login, rating: Any) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    await to_conclude(c, meeting["id"])
    assert (await rate(c, meeting["id"], rating)).status_code == 422
    assert (
        await c.put(f"/api/meetings/{meeting['id']}/rating", json={"rating": 5, "x": 1})
    ).status_code == 422
    assert (await state(c, meeting["id"]))["ratings"] == []


async def test_rating_boundaries_are_accepted(login: Login) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    await to_conclude(c, meeting["id"])
    for value in (1, 10):
        assert (await rate(c, meeting["id"], value)).json()["average_rating"] == float(value)


# --- end ------------------------------------------------------------------------------------------------


async def test_only_the_facilitator_may_end_even_an_admin(login: Login) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    for user in ("u3", "u1", "u5"):
        assert_error(
            await (await login(user)).post(f"/api/meetings/{meeting['id']}/end"), 403, "not_facilitator"
        )
    assert (await state(c, meeting["id"]))["status"] == "active"


async def test_end_from_any_section_writes_ended_at_and_the_summary(
    login: Login, frozen_clock: ManualClock, db_session: AsyncSession
) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)  # still in the Scorecard section
    frozen_clock.advance(900)
    ended = await end_meeting(c, meeting["id"])
    assert ended["status"] == "ended" and ended["ended_at"] == "2026-11-11T15:15:00Z"
    assert ended["current_section"] == "scorecard"
    db_session.expire_all()
    row = await db_session.get(Meeting, meeting["id"])
    assert row is not None and row.ended_at is not None and row.summary is not None
    assert row.summary["ended_at"] == "2026-11-11T15:15:00Z"
    assert_error(await c.post(f"/api/meetings/{meeting['id']}/end"), 409, "meeting_ended")
    assert_error(await c.post(f"/api/meetings/{meeting['id']}/advance"), 409, "meeting_ended")


async def test_after_ending_a_new_meeting_can_start(login: Login) -> None:
    c = await login("u2")
    first = await start_meeting(c)
    await end_meeting(c, first["id"])
    second = await start_meeting(await login("u3"))
    assert second["id"] != first["id"] and second["facilitator"]["id"] == "u3"
    listed = (await c.get(f"{BASE}/meetings")).json()
    assert [m["id"] for m in listed] == [
        second["id"],
        first["id"],
        "mt2",
        "mt1",
    ]  # active first, then newest ended


async def test_ended_meeting_state_has_nobody_online(login: Login) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    ended = await end_meeting(c, meeting["id"])
    assert all(a["online"] is False for a in ended["attendees"])


# --- recap and summary ----------------------------------------------------------------------------------


async def test_recap_lists_what_happened_during_the_meeting(login: Login, frozen_clock: ManualClock) -> None:
    c = await login("u2")
    before = await c.post(f"{BASE}/issues/i1/solve", json={"resolution_note": "before the meeting"})
    assert before.status_code == 200
    frozen_clock.advance(60)
    meeting = await start_meeting(c)
    frozen_clock.advance(60)
    assert (await c.post(f"{BASE}/issues/i3/solve", json={"resolution_note": "during"})).status_code == 200
    todo = await c.post(f"{BASE}/issues/i3/todos", json={"title": "Follow up with support", "owner_id": "u4"})
    assert todo.status_code == 201

    recap = (await (await login("u5")).get(f"/api/meetings/{meeting['id']}/recap")).json()
    assert [i["id"] for i in recap["issues_solved"]] == ["i3"]  # i1 was solved before the meeting started
    assert recap["issues_solved"][0]["resolution_note"] == "during"
    assert recap["issues_solved"][0]["todo_count"] == 1  # the To-Do added below
    assert [t["id"] for t in recap["todos_created"]] == [todo.json()["id"]]
    assert recap["todos_created"][0]["source_issue"] == {"id": "i3", "title": "Support coverage on weekends"}
    assert recap["todos_created"][0]["due_date"] == "2026-11-18"


async def test_recap_window_is_inclusive_of_the_instant_of_the_meeting_start(login: Login) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)  # the clock is frozen: solves happen at exactly started_at
    await c.post(f"{BASE}/issues/i3/solve", json={"resolution_note": "same instant"})
    recap = (await c.get(f"/api/meetings/{meeting['id']}/recap")).json()
    assert [i["id"] for i in recap["issues_solved"]] == ["i3"]


async def test_summary_of_an_active_meeting_is_409(login: Login) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    assert_error(await summary(c, meeting["id"]), 409, "meeting_active")


async def test_summary_contents_after_a_full_meeting(login: Login, frozen_clock: ManualClock) -> None:
    facilitator = await login("u2")
    teammate = await login("u3")
    meeting = await start_meeting(facilitator)
    await join(teammate, meeting["id"])
    await join(await login("u5"), meeting["id"])  # observer attends but cannot rate
    created = await make_issue(teammate, "m1", "2026-10-26")  # not part of the summary, just activity
    assert created.status_code == 201

    frozen_clock.advance(300)
    await advance(facilitator, meeting["id"])
    assert (
        await facilitator.post(f"{BASE}/issues/i2/solve", json={"resolution_note": "Called both accounts"})
    ).status_code == 200
    todo = await teammate.post(
        f"{BASE}/issues/i2/todos", json={"title": "Send recap to accounts", "owner_id": "u3"}
    )
    frozen_clock.advance(1800)
    await advance(facilitator, meeting["id"])
    await rate(facilitator, meeting["id"], 9)
    await rate(teammate, meeting["id"], 8)
    frozen_clock.advance(120)
    ended = await end_meeting(facilitator, meeting["id"])

    body = (await summary(await login("u4"), meeting["id"])).json()
    assert body["meeting_id"] == meeting["id"] and body["team_id"] == "team_lead"
    assert body["scorecard_week"] == "2026-11-02"
    assert body["started_at"] == NOW_ISO and body["ended_at"] == ended["ended_at"] == "2026-11-11T15:37:00Z"
    status = {e["measurable_id"]: e for e in body["measurable_status"]}
    assert list(status) == ["m1", "m2", "m3", "m4", "m5"]  # scorecard order
    assert (status["m1"]["value"], status["m1"]["status"], status["m1"]["name"]) == (
        8,
        "off_track",
        "New deals closed",
    )
    assert (status["m2"]["value"], status["m2"]["status"]) == (3.0, "on_track")  # boundary
    assert (status["m5"]["value"], status["m5"]["status"]) == (None, "off_track")  # reviewed week is closed
    assert body["issues_solved"] == [{"id": "i2", "title": "Two key accounts at risk"}]
    assert body["todos_created"] == [
        {
            "id": todo.json()["id"],
            "title": "Send recap to accounts",
            "owner": {"id": "u3", "name": "Casey Cho", "role": "team_member"},
            "due_date": "2026-11-18",
        }
    ]
    assert [a["id"] for a in body["attendees"]] == ["u2", "u3", "u5"]
    assert body["rating"] == 8.5


async def test_summary_snapshot_stays_unchanged_after_later_edits(
    login: Login, frozen_clock: ManualClock, db_session: AsyncSession
) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    await c.post(f"{BASE}/issues/i3/solve", json={"resolution_note": "during the meeting"})
    await to_conclude(c, meeting["id"])
    await rate(c, meeting["id"], 7)
    frozen_clock.advance(600)
    await end_meeting(c, meeting["id"])
    original = (await summary(c, meeting["id"])).json()
    stored = await db_session.scalar(select(Meeting.summary).where(Meeting.id == meeting["id"]))

    # Everything the summary is derived from changes afterwards ...
    frozen_clock.advance(86_400)
    assert (await put_score(c, "m1", "2026-11-02", 25)).status_code == 200  # scorecard week of the meeting
    assert (await put_score(c, "m5", "2026-11-02", 600)).status_code == 200  # was missing, now on track
    assert (
        await c.post(f"{BASE}/issues/i1/solve", json={"resolution_note": "solved the next day"})
    ).status_code == 200
    await c.post(f"{BASE}/issues/i2/todos", json={"title": "Afterwards", "owner_id": "u2"})
    assert (await c.patch(f"{BASE}/todos/t12", json={"done": True})).status_code == 200
    card = (await c.get(f"{BASE}/scorecard")).json()
    assert next(m for m in card["measurables"] if m["id"] == "m1")["cells"][-2]["value"] == 25

    # ... and the stored snapshot does not move
    assert (await summary(c, meeting["id"])).json() == original
    assert original["issues_solved"] == [{"id": "i3", "title": "Support coverage on weekends"}]
    assert next(e for e in original["measurable_status"] if e["measurable_id"] == "m1")["value"] == 8
    assert (
        next(e for e in original["measurable_status"] if e["measurable_id"] == "m5")["status"] == "off_track"
    )
    db_session.expire_all()
    assert await db_session.scalar(select(Meeting.summary).where(Meeting.id == meeting["id"])) == stored
    # while the live recap (a different view) does follow the data
    recap = (await c.get(f"/api/meetings/{meeting['id']}/recap")).json()
    assert [i["id"] for i in recap["issues_solved"]] == [
        "i3"
    ]  # window is closed at ended_at: i1 is outside it


# --- seeded meetings ------------------------------------------------------------------------------------


def as_report_entry(s: dict[str, Any]) -> dict[str, Any]:
    """MeetingSummary -> the entry shape of the rules report."""
    return {
        "scorecard_week": s["scorecard_week"],
        "measurable_status": {e["measurable_id"]: e["status"] for e in s["measurable_status"]},
        "issues_solved": sorted(i["id"] for i in s["issues_solved"]),
        "todos_created": sorted(t["id"] for t in s["todos_created"]),
        "attendee_ids": sorted(a["id"] for a in s["attendees"]),
        "rating": s["rating"],
    }


async def test_seeded_summaries_match_the_answer_key(login: Login) -> None:
    c = await login("u2")
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["teams"]["team_lead"]["meetings"]
    for meeting_id in ("mt1", "mt2"):
        response = await summary(c, meeting_id)
        assert response.status_code == 200, response.text
        assert as_report_entry(response.json()) == expected[meeting_id]


async def test_seeded_summary_details(login: Login) -> None:
    c = await login("u5")  # an observer attended mt1 and may read it
    mt1 = (await summary(c, "mt1")).json()
    assert (mt1["scorecard_week"], mt1["started_at"], mt1["ended_at"]) == (
        "2026-10-19",
        "2026-10-28T15:00:00Z",
        "2026-10-28T16:15:00Z",
    )
    assert mt1["rating"] == 8.0
    assert mt1["todos_created"] == [
        {
            "id": "t20",
            "title": "Assign onboarding docs owner",
            "owner": {"id": "u4", "name": "Dana Diaz", "role": "team_member"},
            "due_date": "2026-11-04",
        }
    ]
    mt2 = (await summary(c, "mt2")).json()
    assert mt2["rating"] == 8.3  # 33 / 4 = 8.25 -> 8.3
    assert [e["status"] for e in mt2["measurable_status"]] == [
        "off_track",
        "on_track",
        "on_track",
        "off_track",
        "on_track",
    ]


async def test_seeded_meeting_states(login: Login) -> None:
    c = await login("u2")
    mt1 = await state(c, "mt1")
    assert (mt1["status"], mt1["facilitator"]["id"], mt1["ended_at"]) == (
        "ended",
        "u3",
        "2026-10-28T16:15:00Z",
    )
    assert mt1["scorecard_week"] == "2026-10-19"
    assert [a["id"] for a in mt1["attendees"]] == ["u1", "u2", "u3", "u4", "u5"]
    assert [(r["user_id"], r["rating"]) for r in mt1["ratings"]] == [("u1", 8), ("u2", 9), ("u3", 7)]
    assert mt1["average_rating"] == 8.0
    ended = (await c.get(f"{BASE}/meetings", params={"status": "ended"})).json()
    assert [m["id"] for m in ended] == ["mt2", "mt1"]  # newest first
    assert (await c.get(f"{BASE}/meetings", params={"status": "active"})).json() == []
    assert (await c.get(f"{BASE}/meetings")).status_code == 200
    assert (await c.get(f"{BASE}/meetings", params={"status": "paused"})).status_code == 422


async def test_unknown_meeting_is_404_for_admins_and_unauthenticated_is_401(
    login: Login, client: httpx.AsyncClient
) -> None:
    c = await login("u1")  # admin: non-admins get 403 team_forbidden instead (see test_authz)
    for method, path in (
        ("GET", "/api/meetings/mt_nope"),
        ("POST", "/api/meetings/mt_nope/advance"),
        ("POST", "/api/meetings/mt_nope/end"),
        ("GET", "/api/meetings/mt_nope/recap"),
        ("GET", "/api/meetings/mt_nope/summary"),
    ):
        assert_error(await c.request(method, path), 404, "meeting_not_found")
        assert_error(await client.request(method, path), 401, "not_authenticated")


async def test_meetings_are_not_readable_across_teams(login: Login) -> None:
    sales_meeting = await start_meeting(await login("u6"), "team_sales")
    lead_member = await login("u3")  # team_lead only
    assert_error(await lead_member.get(f"/api/meetings/{sales_meeting['id']}"), 403, "team_forbidden")
    assert_error(await lead_member.get("/api/teams/team_sales/meetings"), 403, "team_forbidden")


async def test_meeting_count_never_exceeds_one_active_per_team(
    login: Login, db_session: AsyncSession
) -> None:
    await start_meeting(await login("u2"))
    db_session.expire_all()
    active = await db_session.scalar(
        select(func.count())
        .select_from(Meeting)
        .where(Meeting.team_id == "team_lead", Meeting.ended_at.is_(None))
    )
    assert active == 1
