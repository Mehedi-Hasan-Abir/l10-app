"""Audit trail (HARDENING H3): one event per real change, none for no-ops and refusals, append-only.

Every scenario below follows one shape: do the setup (which may itself write events), call `probe.mark()`,
perform the action under test, and compare only the events written after the mark.
"""

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any, NamedTuple, get_args

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import ManualClock
from app.config import Settings
from app.models import AuditEvent, Score, Todo
from app.schemas import AuditAction
from app.seed import load_seed
from app.services import audit as audit_service
from tests.api.helpers import (
    BASE,
    NOW_ISO,
    TEAM,
    Login,
    advance,
    assert_error,
    end_meeting,
    join,
    make_issue,
    put_score,
    start_meeting,
)

AUDIT = f"{BASE}/audit"
ISSUES = f"{BASE}/issues"
TODOS = f"{BASE}/todos"

ACTIONS = {
    "score.set",
    "score.cleared",
    "issue.created",
    "issue.priority_changed",
    "issue.solved",
    "todo.created",
    "todo.done",
    "todo.reopened",
    "meeting.started",
    "meeting.advanced",
    "meeting.rated",
    "meeting.ended",
}


# --- helpers --------------------------------------------------------------------------------------------


async def get_page(c: httpx.AsyncClient, team: str = TEAM, **params: Any) -> dict[str, Any]:
    response = await c.get(f"/api/teams/{team}/audit", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def all_events(c: httpx.AsyncClient, team: str = TEAM) -> list[dict[str, Any]]:
    """Every event of the team, newest first (the test data never exceeds one page of 200)."""
    return (await get_page(c, team, limit=200))["events"]


class Probe:
    """Sees only the events written after `mark()` (setup writes events too)."""

    def __init__(self, reader: httpx.AsyncClient) -> None:
        self.reader = reader
        self.after = 0

    async def mark(self) -> None:
        events = (await get_page(self.reader, limit=1))["events"]
        self.after = events[0]["id"] if events else 0

    async def new(self) -> list[dict[str, Any]]:
        return [e for e in await all_events(self.reader) if e["id"] > self.after]


@pytest.fixture
async def probe(login: Login) -> Probe:
    return Probe(await login("u1"))  # an admin can read every team's trail


class Expected(NamedTuple):
    actor: str
    action: str
    entity_type: str
    entity_id: str
    data: dict[str, Any]


Scenario = Callable[[Login, Probe], Awaitable[Expected]]
Refusal = Callable[[Login, Probe], Awaitable[httpx.Response]]


async def rate(c: httpx.AsyncClient, meeting_id: str, rating: Any) -> httpx.Response:
    return await c.put(f"/api/meetings/{meeting_id}/rating", json={"rating": rating})


async def to_conclude(c: httpx.AsyncClient, meeting_id: str) -> None:
    await advance(c, meeting_id)
    await advance(c, meeting_id)


def ok_json(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code in (200, 201), response.text
    return response.json()


# --- one event per audited action ----------------------------------------------

SCORE_8 = {
    "measurable_id": "m1",
    "measurable_name": "New deals closed",
    "week_start": "2026-11-02",
}


async def score_set(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    await probe.mark()
    ok_json(await put_score(c, "m1", "2026-11-02", 12))
    return Expected("u2", "score.set", "score", "m1:2026-11-02", {**SCORE_8, "old": 8, "new": 12})


async def score_set_first_value(login: Login, probe: Probe) -> Expected:
    c = await login("u3")
    await probe.mark()
    ok_json(await put_score(c, "m2", "2026-11-09", 2.5))  # the current week has no score yet
    return Expected(
        "u3",
        "score.set",
        "score",
        "m2:2026-11-09",
        {
            "measurable_id": "m2",
            "measurable_name": "Customer churn",
            "week_start": "2026-11-09",
            "old": None,
            "new": 2.5,
        },
    )


async def score_set_with_expected_version(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    await probe.mark()
    ok_json(await put_score(c, "m1", "2026-11-02", 11, expected_version=1))
    return Expected("u2", "score.set", "score", "m1:2026-11-02", {**SCORE_8, "old": 8, "new": 11})


async def score_cleared(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    await probe.mark()
    ok_json(await put_score(c, "m1", "2026-11-02", None))
    return Expected("u2", "score.cleared", "score", "m1:2026-11-02", {**SCORE_8, "old": 8, "new": None})


async def issue_created_manual(login: Login, probe: Probe) -> Expected:
    c = await login("u3")
    await probe.mark()
    issue = ok_json(await c.post(ISSUES, json={"title": "Pricing is confusing", "priority": 2}))
    return Expected(
        "u3",
        "issue.created",
        "issue",
        issue["id"],
        {"title": "Pricing is confusing", "source": {"type": "manual"}},
    )


async def issue_created_from_score(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    await probe.mark()
    result = ok_json(await make_issue(c, "m5", "2026-11-02"))
    assert result["created"] is True
    return Expected(
        "u2",
        "issue.created",
        "issue",
        result["issue"]["id"],
        {
            "title": "Cash on hand, week of Nov 2",
            "source": {"type": "measurable", "measurable_id": "m5", "week_start": "2026-11-02"},
        },
    )


async def issue_created_from_rock(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    await probe.mark()
    result = ok_json(await c.post(f"{BASE}/rocks/r2/issue"))
    assert result["created"] is True
    return Expected(
        "u2",
        "issue.created",
        "issue",
        result["issue"]["id"],
        {"title": "Hire VP of Sales (Rock off track)", "source": {"type": "rock", "rock_id": "r2"}},
    )


async def issue_created_from_todo(login: Login, probe: Probe) -> Expected:
    c = await login("u3")
    await probe.mark()
    result = ok_json(await c.post(f"{TODOS}/t12/issue"))
    assert result["created"] is True
    return Expected(
        "u3",
        "issue.created",
        "issue",
        result["issue"]["id"],
        {"title": "Publish hiring ad (To-Do overdue)", "source": {"type": "todo", "todo_id": "t12"}},
    )


async def issue_priority_changed(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    await probe.mark()
    ok_json(await c.patch(f"{ISSUES}/i1", json={"priority": 2}))
    return Expected(
        "u2",
        "issue.priority_changed",
        "issue",
        "i1",
        {"title": "Hiring pipeline is slow", "old": 5, "new": 2},
    )


async def issue_priority_set_from_none(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    await probe.mark()
    ok_json(await c.patch(f"{ISSUES}/i4", json={"priority": 3, "expected_version": 1}))
    return Expected(
        "u2",
        "issue.priority_changed",
        "issue",
        "i4",
        {"title": "Idea: quarterly customer survey", "old": None, "new": 3},
    )


async def issue_solved(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    await probe.mark()
    ok_json(await c.post(f"{ISSUES}/i2/solve", json={"resolution_note": "Both accounts renewed."}))
    return Expected(
        "u2",
        "issue.solved",
        "issue",
        "i2",
        {"title": "Two key accounts at risk", "resolution_note": "Both accounts renewed."},
    )


async def todo_created(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    await probe.mark()
    todo = ok_json(await c.post(f"{ISSUES}/i1/todos", json={"title": "Post the VP role", "owner_id": "u3"}))
    return Expected(
        "u2",
        "todo.created",
        "todo",
        todo["id"],
        {"title": "Post the VP role", "source_issue_id": "i1", "owner_id": "u3", "due_date": "2026-11-18"},
    )


async def todo_done(login: Login, probe: Probe) -> Expected:
    c = await login("u3")
    await probe.mark()
    ok_json(await c.patch(f"{TODOS}/t12", json={"done": True}))
    return Expected("u3", "todo.done", "todo", "t12", {"title": "Publish hiring ad"})


async def todo_reopened(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    await probe.mark()
    ok_json(await c.patch(f"{TODOS}/t20", json={"done": False}))
    return Expected("u2", "todo.reopened", "todo", "t20", {"title": "Assign onboarding docs owner"})


async def meeting_started(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    await probe.mark()
    meeting = await start_meeting(c)
    return Expected("u2", "meeting.started", "meeting", meeting["id"], {})


async def meeting_advanced(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    meeting = await start_meeting(c)
    await probe.mark()
    await advance(c, meeting["id"])
    return Expected("u2", "meeting.advanced", "meeting", meeting["id"], {"from": "scorecard", "to": "ids"})


async def meeting_advanced_to_conclude(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    meeting = await start_meeting(c)
    await advance(c, meeting["id"])
    await probe.mark()
    await advance(c, meeting["id"])
    return Expected("u2", "meeting.advanced", "meeting", meeting["id"], {"from": "ids", "to": "conclude"})


async def meeting_rated(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    meeting = await start_meeting(c)
    await to_conclude(c, meeting["id"])
    await probe.mark()
    ok_json(await rate(c, meeting["id"], 8))
    return Expected("u2", "meeting.rated", "meeting", meeting["id"], {"rating": 8})


async def meeting_rating_changed(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    meeting = await start_meeting(c)
    await to_conclude(c, meeting["id"])
    ok_json(await rate(c, meeting["id"], 8))
    await probe.mark()
    ok_json(await rate(c, meeting["id"], 9))
    return Expected("u2", "meeting.rated", "meeting", meeting["id"], {"rating": 9})


async def meeting_ended(login: Login, probe: Probe) -> Expected:
    c = await login("u2")
    meeting = await start_meeting(c)
    await probe.mark()
    await end_meeting(c, meeting["id"])
    return Expected("u2", "meeting.ended", "meeting", meeting["id"], {})


SCENARIOS = [
    pytest.param(score_set, id="score.set"),
    pytest.param(score_set_first_value, id="score.set-first-value"),
    pytest.param(score_set_with_expected_version, id="score.set-expected-version"),
    pytest.param(score_cleared, id="score.cleared"),
    pytest.param(issue_created_manual, id="issue.created-manual"),
    pytest.param(issue_created_from_score, id="issue.created-from-score"),
    pytest.param(issue_created_from_rock, id="issue.created-from-rock"),
    pytest.param(issue_created_from_todo, id="issue.created-from-todo"),
    pytest.param(issue_priority_changed, id="issue.priority_changed"),
    pytest.param(issue_priority_set_from_none, id="issue.priority_changed-from-none"),
    pytest.param(issue_solved, id="issue.solved"),
    pytest.param(todo_created, id="todo.created"),
    pytest.param(todo_done, id="todo.done"),
    pytest.param(todo_reopened, id="todo.reopened"),
    pytest.param(meeting_started, id="meeting.started"),
    pytest.param(meeting_advanced, id="meeting.advanced"),
    pytest.param(meeting_advanced_to_conclude, id="meeting.advanced-to-conclude"),
    pytest.param(meeting_rated, id="meeting.rated"),
    pytest.param(meeting_rating_changed, id="meeting.rated-changed"),
    pytest.param(meeting_ended, id="meeting.ended"),
]


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_each_audited_action_writes_exactly_one_event(
    scenario: Scenario, login: Login, probe: Probe, db_session: AsyncSession
) -> None:
    expected = await scenario(login, probe)

    events = await probe.new()
    assert len(events) == 1, events
    event = events[0]
    assert event["actor"]["id"] == expected.actor
    assert (event["action"], event["entity_type"], event["entity_id"]) == expected[1:4]
    assert event["data"] == expected.data
    assert event["team_id"] == TEAM
    assert event["at"] == NOW_ISO  # the injected clock, not the system clock
    assert event["action"] in ACTIONS
    # the actor is the full UserRef, as on every other response
    assert set(event["actor"]) == {"id", "name", "role"}
    # and the row really is in the database, in the same transaction as the change
    db_session.expire_all()
    row = await db_session.get(AuditEvent, event["id"])
    assert row is not None and row.team_id == TEAM and row.actor_id == expected.actor


def test_every_action_in_the_catalogue_has_a_scenario() -> None:
    covered = {param.id.split("-")[0] for param in SCENARIOS if param.id}
    assert covered == ACTIONS == set(get_args(AuditAction))


# --- no event for no-ops and refusals ----------------------------------------------


async def make_issue_returns_existing(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    response = await make_issue(c, "m1", "2026-11-02")  # i5 already covers this cell
    assert response.json()["created"] is False and response.json()["issue"]["id"] == "i5"
    return response


async def make_issue_twice(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    assert (await make_issue(c, "m5", "2026-11-02")).status_code == 201
    await probe.mark()
    response = await make_issue(await login("u3"), "m5", "2026-11-02")
    assert response.json()["created"] is False
    return response


async def todo_done_twice(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    assert (await c.patch(f"{TODOS}/t12", json={"done": True})).status_code == 200
    await probe.mark()
    return await c.patch(f"{TODOS}/t12", json={"done": True})


async def todo_reopen_when_open(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await c.patch(f"{TODOS}/t12", json={"done": False})  # t12 is open already


async def score_same_value(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await put_score(c, "m1", "2026-11-02", 8)  # already 8


async def score_clear_empty_cell(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await put_score(c, "m5", "2026-11-02", None)  # nothing there to clear


async def priority_same_value(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await c.patch(f"{ISSUES}/i1", json={"priority": 5})  # already 5


async def rating_same_value(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    meeting = await start_meeting(c)
    await to_conclude(c, meeting["id"])
    assert (await rate(c, meeting["id"], 8)).status_code == 200
    await probe.mark()
    return await rate(c, meeting["id"], 8)


async def join_is_not_audited(login: Login, probe: Probe) -> httpx.Response:
    meeting = await start_meeting(await login("u2"))
    await probe.mark()
    return await (await login("u3")).post(f"/api/meetings/{meeting['id']}/join")


async def observer_cannot_set_a_score(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u5")
    await probe.mark()
    return await put_score(c, "m1", "2026-11-02", 99)


async def observer_cannot_toggle_a_todo(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u5")
    await probe.mark()
    return await c.patch(f"{TODOS}/t12", json={"done": True})


async def observer_cannot_create_an_issue(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u5")
    await probe.mark()
    return await c.post(ISSUES, json={"title": "Nope"})


async def observer_cannot_start_a_meeting(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u5")
    await probe.mark()
    return await c.post(f"{BASE}/meetings")


async def non_member_cannot_write(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u6")  # team_sales only
    await probe.mark()
    return await put_score(c, "m1", "2026-11-02", 99)


async def solve_twice(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    assert (await c.post(f"{ISSUES}/i2/solve", json={"resolution_note": "Done."})).status_code == 200
    await probe.mark()
    return await c.post(f"{ISSUES}/i2/solve", json={"resolution_note": "Done again."})


async def prioritize_a_solved_issue(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await c.patch(f"{ISSUES}/i6", json={"priority": 1})  # i6 is solved


async def start_while_a_meeting_is_active(login: Login, probe: Probe) -> httpx.Response:
    await start_meeting(await login("u2"))
    await probe.mark()
    return await (await login("u3")).post(f"{BASE}/meetings")


async def make_issue_on_an_on_track_cell(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await make_issue(c, "m1", "2026-09-07")  # exactly on target


async def stale_score_version(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await put_score(c, "m1", "2026-11-02", 12, expected_version=99)


async def stale_priority_version(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await c.patch(f"{ISSUES}/i1", json={"priority": 1, "expected_version": 99})


async def stale_advance(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    meeting = await start_meeting(c)
    await probe.mark()
    return await c.post(f"/api/meetings/{meeting['id']}/advance", json={"from_section": "ids"})


async def only_the_facilitator_advances(login: Login, probe: Probe) -> httpx.Response:
    meeting = await start_meeting(await login("u2"))
    other = await login("u3")
    await join(other, meeting["id"])
    await probe.mark()
    return await other.post(f"/api/meetings/{meeting['id']}/advance")


async def rating_outside_conclude(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    meeting = await start_meeting(c)
    await probe.mark()
    return await rate(c, meeting["id"], 8)


async def rating_after_the_meeting_ended(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    meeting = await start_meeting(c)
    await to_conclude(c, meeting["id"])
    await end_meeting(c, meeting["id"])
    await probe.mark()
    return await rate(c, meeting["id"], 8)


async def end_twice(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    meeting = await start_meeting(c)
    await end_meeting(c, meeting["id"])
    await probe.mark()
    return await c.post(f"/api/meetings/{meeting['id']}/end")


async def score_on_a_non_monday(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await put_score(c, "m1", "2026-11-03", 5)


async def score_outside_the_window(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await put_score(c, "m1", "2026-11-16", 5)  # next week


async def score_not_a_number(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await put_score(c, "m1", "2026-11-02", "twelve")


async def issue_with_a_blank_title(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await c.post(ISSUES, json={"title": "   "})


async def issue_with_priority_six(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await c.patch(f"{ISSUES}/i1", json={"priority": 6})


async def solve_without_a_note(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await c.post(f"{ISSUES}/i2/solve", json={"resolution_note": ""})


async def todo_for_an_ineligible_owner(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await c.post(f"{ISSUES}/i1/todos", json={"title": "Whose job?", "owner_id": "u5"})  # an observer


async def rating_out_of_range(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    meeting = await start_meeting(c)
    await to_conclude(c, meeting["id"])
    await probe.mark()
    return await rate(c, meeting["id"], 11)


async def solve_an_unknown_issue(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await c.post(f"{ISSUES}/i_nope/solve", json={"resolution_note": "x"})


async def toggle_an_unknown_todo(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u2")
    await probe.mark()
    return await c.patch(f"{TODOS}/t_nope", json={"done": True})


async def solve_another_teams_issue(login: Login, probe: Probe) -> httpx.Response:
    c = await login("u1")
    await probe.mark()
    return await c.post(f"{ISSUES}/i8/solve", json={"resolution_note": "wrong team"})  # i8 is team_sales'


NO_EVENT = [
    pytest.param(make_issue_returns_existing, 200, id="make-it-an-issue-returns-existing"),
    pytest.param(make_issue_twice, 200, id="make-it-an-issue-second-caller"),
    pytest.param(todo_done_twice, 200, id="done-true-repeated"),
    pytest.param(todo_reopen_when_open, 200, id="done-false-on-an-open-todo"),
    pytest.param(score_same_value, 200, id="score-same-value"),
    pytest.param(score_clear_empty_cell, 200, id="score-clear-empty-cell"),
    pytest.param(priority_same_value, 200, id="priority-same-value"),
    pytest.param(rating_same_value, 200, id="rating-same-value"),
    pytest.param(join_is_not_audited, 200, id="join-is-not-audited"),
    pytest.param(observer_cannot_set_a_score, 403, id="403-observer-score"),
    pytest.param(observer_cannot_toggle_a_todo, 403, id="403-observer-todo"),
    pytest.param(observer_cannot_create_an_issue, 403, id="403-observer-issue"),
    pytest.param(observer_cannot_start_a_meeting, 403, id="403-observer-meeting"),
    pytest.param(non_member_cannot_write, 403, id="403-non-member"),
    pytest.param(only_the_facilitator_advances, 403, id="403-not-facilitator"),
    pytest.param(solve_another_teams_issue, 404, id="404-other-teams-issue"),
    pytest.param(solve_an_unknown_issue, 404, id="404-unknown-issue"),
    pytest.param(toggle_an_unknown_todo, 404, id="404-unknown-todo"),
    pytest.param(solve_twice, 409, id="409-solve-twice"),
    pytest.param(prioritize_a_solved_issue, 409, id="409-prioritize-solved"),
    pytest.param(start_while_a_meeting_is_active, 409, id="409-second-meeting"),
    pytest.param(make_issue_on_an_on_track_cell, 409, id="409-not-off-track"),
    pytest.param(stale_score_version, 409, id="409-stale-score-version"),
    pytest.param(stale_priority_version, 409, id="409-stale-priority-version"),
    pytest.param(stale_advance, 409, id="409-stale-section"),
    pytest.param(rating_outside_conclude, 409, id="409-rating-outside-conclude"),
    pytest.param(rating_after_the_meeting_ended, 409, id="409-rating-after-end"),
    pytest.param(end_twice, 409, id="409-end-twice"),
    pytest.param(score_on_a_non_monday, 422, id="422-non-monday"),
    pytest.param(score_outside_the_window, 422, id="422-outside-window"),
    pytest.param(score_not_a_number, 422, id="422-score-not-a-number"),
    pytest.param(issue_with_a_blank_title, 422, id="422-blank-title"),
    pytest.param(issue_with_priority_six, 422, id="422-priority-six"),
    pytest.param(solve_without_a_note, 422, id="422-empty-note"),
    pytest.param(todo_for_an_ineligible_owner, 422, id="422-ineligible-owner"),
    pytest.param(rating_out_of_range, 422, id="422-rating-eleven"),
]


@pytest.mark.parametrize(("refusal", "status"), NO_EVENT)
async def test_no_event_for_no_ops_and_refused_requests(
    refusal: Refusal, status: int, login: Login, probe: Probe
) -> None:
    response = await refusal(login, probe)
    assert response.status_code == status, response.text
    assert await probe.new() == []


# --- transactions, concurrency and the clock ----------------------------------------------


async def test_a_failed_request_leaves_neither_the_change_nor_its_audit_row(
    login: Login, probe: Probe, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit insert shares the request's transaction: if anything fails before the commit, both vanish."""
    c = await login("u2")
    real_record = audit_service.record

    async def record_then_fail(*args: Any, **kwargs: Any) -> None:
        await real_record(*args, **kwargs)  # the audit row IS inserted ...
        raise RuntimeError("boom before the commit")  # ... and then the request dies

    await probe.mark()
    with monkeypatch.context() as patched:
        patched.setattr(audit_service, "record", record_then_fail)
        with pytest.raises(RuntimeError, match="boom before the commit"):
            await c.patch(f"{TODOS}/t12", json={"done": True})

    db_session.expire_all()
    todo = await db_session.get(Todo, "t12")
    assert todo is not None and todo.done_at is None
    assert await db_session.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert await probe.new() == []
    # and the very same request succeeds (with one event) once the failure is gone
    assert (await c.patch(f"{TODOS}/t12", json={"done": True})).status_code == 200
    assert len(await probe.new()) == 1


async def test_concurrent_make_it_an_issue_writes_one_event(login: Login, probe: Probe) -> None:
    clients = [await login(u) for u in ("u2", "u3", "u4", "u1")]
    await probe.mark()
    responses = await asyncio.gather(*(make_issue(c, "m5", "2026-11-02") for c in clients for _ in range(2)))
    assert Counter(r.status_code for r in responses) == {201: 1, 200: 7}
    winner = next(r.json()["issue"] for r in responses if r.status_code == 201)
    events = await probe.new()
    assert [(e["action"], e["entity_id"]) for e in events] == [("issue.created", winner["id"])]


async def test_concurrent_done_toggles_write_one_event(login: Login, probe: Probe) -> None:
    clients = [await login(u) for u in ("u2", "u3", "u4")]
    await probe.mark()
    responses = await asyncio.gather(*(c.patch(f"{TODOS}/t12", json={"done": True}) for c in clients * 2))
    assert {r.status_code for r in responses} == {200}
    events = await probe.new()
    assert [(e["action"], e["entity_id"]) for e in events] == [("todo.done", "t12")]


async def test_concurrent_meeting_starts_write_one_event(login: Login, probe: Probe) -> None:
    clients = [await login(u) for u in ("u2", "u3", "u4", "u1")]
    await probe.mark()
    responses = await asyncio.gather(*(c.post(f"{BASE}/meetings") for c in clients for _ in range(2)))
    assert Counter(r.status_code for r in responses) == {201: 1, 409: 7}
    winner = next(r.json() for r in responses if r.status_code == 201)
    events = await probe.new()
    assert [(e["action"], e["entity_id"]) for e in events] == [("meeting.started", winner["id"])]


async def test_concurrent_score_edits_chain_old_to_new(login: Login, probe: Probe) -> None:
    """Whichever edit lands second reports the first one's value as `old` (the read is row-locked)."""
    a, b = await login("u2"), await login("u3")
    await probe.mark()
    responses = await asyncio.gather(
        put_score(a, "m1", "2026-11-02", 100), put_score(b, "m1", "2026-11-02", 200)
    )
    assert {r.status_code for r in responses} == {200}
    first, second = sorted(await probe.new(), key=lambda e: e["id"])
    assert first["data"]["old"] == 8
    assert second["data"]["old"] == first["data"]["new"]
    assert {first["data"]["new"], second["data"]["new"]} == {100, 200}


async def test_events_carry_the_injected_clock_and_the_time_stored_on_the_entity(
    login: Login, probe: Probe, frozen_clock: ManualClock, db_session: AsyncSession
) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)

    frozen_clock.advance(60)
    advanced = await advance(c, meeting["id"])
    frozen_clock.advance(3600)
    ended = await end_meeting(c, meeting["id"])
    frozen_clock.advance(90)
    assert (await put_score(c, "m1", "2026-11-02", 12)).status_code == 200

    by_action = {e["action"]: e for e in await all_events(probe.reader)}
    assert by_action["meeting.started"]["at"] == meeting["started_at"] == NOW_ISO
    assert by_action["meeting.advanced"]["at"] == advanced["section_started_at"] == "2026-11-11T15:01:00Z"
    assert by_action["meeting.ended"]["at"] == ended["ended_at"] == "2026-11-11T16:01:00Z"
    assert by_action["score.set"]["at"] == "2026-11-11T16:02:30Z"
    score = await db_session.scalar(
        select(Score).where(Score.measurable_id == "m1", Score.week_start == date(2026, 11, 2))
    )
    assert score is not None and score.updated_at == datetime(2026, 11, 11, 16, 2, 30, tzinfo=UTC)


# --- the endpoint ----------------------------------------------


async def test_a_fresh_seed_has_an_empty_trail(login: Login) -> None:
    assert await get_page(await login("u2")) == {"events": [], "next_before_id": None}


async def test_events_come_newest_first_with_the_full_shape(login: Login) -> None:
    c = await login("u2")
    await put_score(c, "m1", "2026-11-02", 12)
    await c.patch(f"{TODOS}/t12", json={"done": True})
    page = await get_page(c)
    assert [e["action"] for e in page["events"]] == ["todo.done", "score.set"]
    assert page["next_before_id"] is None
    assert page["events"][0] == {
        "id": page["events"][0]["id"],
        "team_id": TEAM,
        "actor": {"id": "u2", "name": "Blake Brooks", "role": "team_member"},
        "action": "todo.done",
        "entity_type": "todo",
        "entity_id": "t12",
        "at": NOW_ISO,
        "data": {"title": "Publish hiring ad"},
    }
    assert page["events"][0]["id"] > page["events"][1]["id"]


async def test_pagination_walks_every_event_once_newest_first(login: Login) -> None:
    c = await login("u2")
    weeks = ["2026-08-17", "2026-08-24", "2026-08-31", "2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
    for i, week in enumerate(weeks):
        assert (await put_score(c, "m1", week, 100 + i)).status_code == 200

    everything = await all_events(c)
    assert [e["entity_id"] for e in everything] == [f"m1:{w}" for w in reversed(weeks)]

    seen: list[int] = []
    before_id: int | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 3}
        if before_id is not None:
            params["before_id"] = before_id
        page = await get_page(c, **params)
        pages += 1
        seen += [e["id"] for e in page["events"]]
        if page["next_before_id"] is None:
            assert len(page["events"]) <= 3
            break
        assert len(page["events"]) == 3
        assert page["next_before_id"] == page["events"][-1]["id"]  # the cursor is the oldest id on the page
        before_id = page["next_before_id"]
    assert pages == 3  # 3 + 3 + 1
    assert seen == [e["id"] for e in everything]  # no gaps, no duplicates, newest first
    assert seen == sorted(seen, reverse=True)


async def test_a_page_that_ends_exactly_on_the_last_event_has_no_cursor(login: Login) -> None:
    c = await login("u2")
    for i, week in enumerate(["2026-09-07", "2026-09-14", "2026-09-21"]):
        await put_score(c, "m1", week, 100 + i)
    exact = await get_page(c, limit=3)
    assert len(exact["events"]) == 3 and exact["next_before_id"] is None
    short = await get_page(c, limit=2)
    assert len(short["events"]) == 2 and short["next_before_id"] == short["events"][-1]["id"]
    assert await get_page(c, limit=2, before_id=short["next_before_id"]) == {
        "events": [exact["events"][2]],
        "next_before_id": None,
    }
    # a cursor older than everything gives an empty page
    oldest = exact["events"][-1]["id"]
    assert await get_page(c, before_id=oldest) == {"events": [], "next_before_id": None}


async def test_default_limit_is_50(login: Login, db_session: AsyncSession) -> None:
    await db_session.execute(
        text(
            "INSERT INTO audit_events (team_id, actor_id, action, entity_type, entity_id, at) "
            "SELECT 'team_lead', 'u2', 'todo.done', 'todo', 't' || n, now() FROM generate_series(1, 120) AS n"
        )
    )
    await db_session.commit()
    c = await login("u2")
    page = await get_page(c)
    assert len(page["events"]) == 50 and page["next_before_id"] == page["events"][-1]["id"]
    assert len((await get_page(c, limit=200))["events"]) == 120
    assert (await get_page(c, limit=200))["next_before_id"] is None


@pytest.mark.parametrize("limit", [1, 200])
async def test_limit_bounds_are_accepted(limit: int, login: Login) -> None:
    assert (await (await login("u2")).get(AUDIT, params={"limit": limit})).status_code == 200


@pytest.mark.parametrize("limit", [0, -1, 201, 1000, "many", ""])
async def test_limit_outside_1_to_200_is_422(limit: Any, login: Login) -> None:
    response = await (await login("u2")).get(AUDIT, params={"limit": limit})
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], list)


@pytest.mark.parametrize("before_id", [0, -5, "abc"])
async def test_a_bad_cursor_is_422(before_id: Any, login: Login) -> None:
    assert (await (await login("u2")).get(AUDIT, params={"before_id": before_id})).status_code == 422


async def test_each_team_sees_only_its_own_events(login: Login) -> None:
    lead, sales = await login("u2"), await login("u6")
    await put_score(lead, "m1", "2026-11-02", 12)
    assert (await put_score(sales, "m6", "2026-11-02", 61, team="team_sales")).status_code == 200
    assert [e["entity_id"] for e in await all_events(lead)] == ["m1:2026-11-02"]
    assert [e["entity_id"] for e in await all_events(sales, "team_sales")] == ["m6:2026-11-02"]
    admin = await login("u1")
    assert [e["entity_id"] for e in await all_events(admin, "team_sales")] == ["m6:2026-11-02"]
    assert [e["entity_id"] for e in await all_events(admin)] == ["m1:2026-11-02"]


# --- authorization ----------------------------------------------


async def test_reading_another_teams_trail_is_403(login: Login) -> None:
    await put_score(await login("u2"), "m1", "2026-11-02", 12)
    for user in ("u6", "u8"):  # team_sales and team_ops members
        response = await (await login(user)).get(AUDIT)
        assert_error(response, 403, "team_forbidden")
        assert "m1" not in response.text


async def test_unknown_team_is_403_for_members_and_404_for_admins(login: Login) -> None:
    assert_error(await (await login("u2")).get("/api/teams/team_nope/audit"), 403, "team_forbidden")
    assert_error(await (await login("u1")).get("/api/teams/team_nope/audit"), 404, "team_not_found")


async def test_anonymous_is_401(client: httpx.AsyncClient) -> None:
    assert_error(await client.get(AUDIT), 401, "not_authenticated")


async def test_observers_and_admins_can_read(login: Login) -> None:
    await put_score(await login("u2"), "m1", "2026-11-02", 12)
    for user in ("u5", "u1", "u3"):
        assert len((await get_page(await login(user)))["events"]) == 1


async def test_the_trail_is_read_only_over_http(login: Login) -> None:
    c = await login("u1")
    for method in ("post", "put", "patch", "delete"):
        response = await getattr(c, method)(AUDIT)
        assert response.status_code == 405, (method, response.status_code)


async def test_openapi_documents_the_endpoint_and_models(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/api/openapi.json")).json()
    operation = spec["paths"]["/api/teams/{team_id}/audit"]["get"]
    assert operation["summary"] == "Audit trail"
    assert {"200", "401", "403", "422"} <= set(operation["responses"])
    assert {p["name"] for p in operation["parameters"]} == {"team_id", "limit", "before_id"}
    schemas = spec["components"]["schemas"]
    assert {"AuditEvent", "AuditPage"} <= set(schemas)
    assert set(schemas["AuditEvent"]["properties"]) == {
        "id",
        "team_id",
        "actor",
        "action",
        "entity_type",
        "entity_id",
        "at",
        "data",
    }
    assert set(schemas["AuditPage"]["properties"]) == {"events", "next_before_id"}


# --- append-only at the database level ----------------------------------------------


async def test_the_database_rejects_update_and_delete(login: Login, db_session: AsyncSession) -> None:
    await put_score(await login("u2"), "m1", "2026-11-02", 12)
    before = (await db_session.execute(select(AuditEvent.id, AuditEvent.action, AuditEvent.data))).all()
    assert len(before) == 1

    for statement in (
        "UPDATE audit_events SET action = 'tampered'",
        "UPDATE audit_events SET data = '{}'::jsonb WHERE team_id = 'team_lead'",
        "UPDATE audit_events SET at = now()",
        "DELETE FROM audit_events",
        "DELETE FROM audit_events WHERE id = (SELECT min(id) FROM audit_events)",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            await db_session.execute(text(statement))
        await db_session.rollback()

    after = (await db_session.execute(select(AuditEvent.id, AuditEvent.action, AuditEvent.data))).all()
    assert after == before


async def test_the_orm_cannot_update_or_delete_an_event_either(
    login: Login, db_session: AsyncSession
) -> None:
    await put_score(await login("u2"), "m1", "2026-11-02", 12)
    event = await db_session.scalar(select(AuditEvent))
    assert event is not None
    event.action = "tampered"
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.flush()
    await db_session.rollback()

    event = await db_session.scalar(select(AuditEvent))
    assert event is not None
    await db_session.delete(event)
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.flush()
    await db_session.rollback()
    assert await db_session.scalar(select(func.count()).select_from(AuditEvent)) == 1


async def test_entity_type_is_constrained_by_the_database(db_session: AsyncSession) -> None:
    with pytest.raises(DBAPIError, match="ck_audit_events_entity_type"):
        await db_session.execute(
            text(
                "INSERT INTO audit_events (team_id, actor_id, action, entity_type, entity_id, at) "
                "VALUES ('team_lead', 'u2', 'x', 'rock', 'r1', now())"
            )
        )
    await db_session.rollback()


async def test_truncate_still_works_so_seed_reset_clears_the_trail(
    login: Login,
    db_session_factory: async_sessionmaker[AsyncSession],
    test_settings: Settings,
    db_session: AsyncSession,
) -> None:
    c = await login("u2")
    await put_score(c, "m1", "2026-11-02", 12)
    await put_score(c, "m1", "2026-11-02", 13)
    assert await db_session.scalar(select(func.count()).select_from(AuditEvent)) == 2
    await db_session.rollback()

    async with db_session_factory() as session, session.begin():
        await load_seed(
            session, test_settings.seed_path, reset=True
        )  # what `python -m app.seed --reset` runs

    assert await db_session.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert await get_page(c) == {"events": [], "next_before_id": None}
    # RESTART IDENTITY: ids start again from 1
    await put_score(c, "m1", "2026-11-02", 12)
    assert [e["id"] for e in await all_events(c)] == [1]
