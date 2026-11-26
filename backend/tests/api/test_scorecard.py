"""Scorecard: shape, seeded statuses, score edits (upsert, clear, validation, version conflicts)."""

from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Score
from tests.api.helpers import BASE, Login, assert_error, cell, detail, make_issue, put_score

WINDOW = [
    "2026-08-17",
    "2026-08-24",
    "2026-08-31",
    "2026-09-07",
    "2026-09-14",
    "2026-09-21",
    "2026-09-28",
    "2026-10-05",
    "2026-10-12",
    "2026-10-19",
    "2026-10-26",
    "2026-11-02",
    "2026-11-09",
]


async def get_scorecard(c: httpx.AsyncClient, team: str = "team_lead") -> dict:
    response = await c.get(f"/api/teams/{team}/scorecard")
    assert response.status_code == 200, response.text
    return response.json()


async def test_scorecard_shape_and_window(login: Login) -> None:
    c = await login("u2")
    card = await get_scorecard(c)
    assert card["weeks"] == WINDOW
    assert card["current_week"] == "2026-11-09"
    assert card["last_closed_week"] == "2026-11-02"
    assert [m["id"] for m in card["measurables"]] == ["m1", "m2", "m3", "m4", "m5"]  # seed order
    for m in card["measurables"]:
        assert [x["week_start"] for x in m["cells"]] == WINDOW  # aligned with `weeks`
        assert set(m) == {"id", "name", "unit", "owner", "goal", "goal_label", "cells"}
    m1 = card["measurables"][0]
    assert m1["name"] == "New deals closed"
    assert m1["owner"] == {"id": "u2", "name": "Blake Brooks", "role": "team_member"}
    assert m1["goal"] == {"type": "at_least", "value": 10, "min": None, "max": None}
    assert set(cell(card, "m1", "2026-11-02")) == {
        "week_start",
        "value",
        "status",
        "version",
        "open_issue_id",
    }


async def test_goal_labels(login: Login) -> None:
    card = await get_scorecard(await login("u2"))
    labels = {m["id"]: m["goal_label"] for m in card["measurables"]}
    assert labels == {"m1": "≥ 10", "m2": "≤ 3 %", "m3": "2–4 hours", "m4": "= 1", "m5": "≥ $500k"}
    between = next(m for m in card["measurables"] if m["id"] == "m3")["goal"]
    assert between == {"type": "between", "value": None, "min": 2, "max": 4}


async def test_seeded_statuses_and_traps(login: Login) -> None:
    card = await get_scorecard(await login("u2"))
    # m5 has no score for 2026-10-19 and 2026-11-02 (both closed weeks): off track, and a missing value
    assert cell(card, "m5", "2026-10-19") == {
        "week_start": "2026-10-19",
        "value": None,
        "status": "off_track",
        "version": None,
        "open_issue_id": None,
    }
    assert cell(card, "m5", "2026-11-02")["status"] == "off_track"
    # ... but the current week HAS a score, so it is evaluated like any other week
    assert cell(card, "m5", "2026-11-09") | {"open_issue_id": None} == {
        "week_start": "2026-11-09",
        "value": 520,
        "status": "on_track",
        "version": 1,
        "open_issue_id": None,
    }
    # current week without a score is pending (not off track)
    for m in ("m1", "m2", "m3", "m4"):
        c = cell(card, m, "2026-11-09")
        assert (c["value"], c["status"], c["version"]) == (None, "pending", None)
    # boundaries are on track: m2 at_most 3.0 with 3.0; m1 at_least 10 with 10 (2026-09-07)
    assert cell(card, "m2", "2026-11-02")["status"] == "on_track"
    assert cell(card, "m1", "2026-09-07")["status"] == "on_track"
    assert cell(card, "m1", "2026-08-31")["status"] == "off_track"  # 9 < 10
    assert cell(card, "m3", "2026-11-02")["status"] == "off_track"  # 4.5 outside 2-4
    assert cell(card, "m4", "2026-09-21")["status"] == "off_track"  # exactly 1: 2 != 1


async def test_open_issue_link_only_for_unsolved_issues(login: Login) -> None:
    card = await get_scorecard(await login("u2"))
    assert cell(card, "m1", "2026-11-02")["open_issue_id"] == "i5"  # unsolved
    assert cell(card, "m4", "2026-10-26")["open_issue_id"] is None  # i6 is solved
    assert cell(card, "m1", "2026-10-26")["open_issue_id"] is None  # off track, nobody made an issue


async def test_scorecards_are_per_team(login: Login) -> None:
    sales = await get_scorecard(await login("u6"), "team_sales")
    assert [m["id"] for m in sales["measurables"]] == ["m6"]
    assert sales["measurables"][0]["goal_label"] == "≥ 50"
    ops = await get_scorecard(await login("u8"), "team_ops")
    assert [m["id"] for m in ops["measurables"]] == ["m7"]


async def test_observer_and_admin_can_read(login: Login) -> None:
    for user in ("u5", "u1"):
        card = await get_scorecard(await login(user))
        assert len(card["measurables"]) == 5


# --- editing --------------------------------------------------------------------------------------------


async def test_put_updates_existing_cell_and_bumps_version(login: Login, db_session: AsyncSession) -> None:
    c = await login("u2")
    response = await put_score(c, "m1", "2026-11-02", 12)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "week_start": "2026-11-02",
        "value": 12,
        "status": "on_track",  # was 8 (off track), now meets the goal
        "version": 2,
        "open_issue_id": "i5",
    }
    again = await put_score(c, "m1", "2026-11-02", 7)
    assert again.json()["version"] == 3 and again.json()["status"] == "off_track"
    card = await get_scorecard(c)
    assert cell(card, "m1", "2026-11-02")["value"] == 7
    row = await db_session.get(Score, ("m1", date(2026, 11, 2)))
    assert row is not None and row.value == Decimal("7") and row.updated_by == "u2"


async def test_put_creates_a_cell_and_current_week_leaves_pending(login: Login) -> None:
    c = await login("u2")
    response = await put_score(c, "m1", "2026-11-09", 3)
    assert response.status_code == 200
    assert response.json() | {"open_issue_id": None} == {
        "week_start": "2026-11-09",
        "value": 3,
        "status": "off_track",  # evaluated as soon as it has a score
        "version": 1,
        "open_issue_id": None,
    }


async def test_null_clears_the_cell_and_status_falls_back(login: Login) -> None:
    c = await login("u2")
    closed = await put_score(c, "m1", "2026-11-02", None)
    assert closed.status_code == 200
    assert closed.json()["value"] is None and closed.json()["version"] is None
    assert closed.json()["status"] == "off_track"  # a closed week without a score
    current = await put_score(c, "m5", "2026-11-09", None)
    assert current.json()["status"] == "pending"  # the current week without a score
    # clearing an empty cell is a harmless no-op
    assert (await put_score(c, "m5", "2026-11-02", None)).status_code == 200


async def test_decimal_values_round_trip_exactly(login: Login, db_session: AsyncSession) -> None:
    c = await login("u3")
    response = await put_score(c, "m2", "2026-11-09", 8.25)
    assert response.json()["value"] == 8.25
    row = (await db_session.scalars(select(Score).where(Score.measurable_id == "m2"))).all()
    stored = next(r for r in row if str(r.week_start) == "2026-11-09")
    assert stored.value == Decimal("8.25")  # not 8.2499999...


@pytest.mark.parametrize(
    ("value", "expected"),
    [(10, "on_track"), (9.99, "off_track"), (10.01, "on_track")],
)
async def test_boundary_values_use_exact_comparison(login: Login, value: float, expected: str) -> None:
    c = await login("u2")
    assert (await put_score(c, "m1", "2026-10-26", value)).json()["status"] == expected


async def test_last_save_wins_without_expected_version(login: Login) -> None:
    a, b = await login("u2"), await login("u3")
    assert (await put_score(a, "m1", "2026-11-02", 11)).status_code == 200
    second = await put_score(b, "m1", "2026-11-02", 13)  # no expected_version: always accepted
    assert second.status_code == 200 and second.json()["value"] == 13 and second.json()["version"] == 3


# --- validation -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("week", "code"),
    [
        ("2026-11-03", "week_not_monday"),
        ("2026-08-10", "week_out_of_window"),  # one week before the window
        ("2026-11-16", "week_out_of_window"),  # future week
        ("2027-01-04", "week_out_of_window"),
    ],
)
async def test_week_must_be_a_monday_inside_the_window(login: Login, week: str, code: str) -> None:
    response = await put_score(await login("u2"), "m1", week, 5)
    assert_error(response, 422, code)
    assert_error(await make_issue(await login("u2"), "m1", week), 422, code)


async def test_malformed_week_is_422(login: Login) -> None:
    c = await login("u2")
    response = await c.put(f"{BASE}/measurables/m1/scores/not-a-date", json={"value": 1})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"value": 1e12},  # |v| must be < 1e12
        {"value": -1e12},
        {"value": "12"},  # numbers only
        {"value": True},
        {"value": 1, "unexpected": 1},  # unknown field
        {},  # value is required (null clears)
        {"value": 1, "expected_version": 0},
        {"value": 1, "expected_version": 1.5},
        {"value": 1, "expected_version": "1"},
    ],
)
async def test_invalid_bodies_are_422_and_change_nothing(login: Login, body: dict) -> None:
    c = await login("u2")
    response = await c.put(f"{BASE}/measurables/m1/scores/2026-11-02", json=body)
    assert response.status_code == 422, response.text
    card = await get_scorecard(c)
    assert cell(card, "m1", "2026-11-02")["value"] == 8 and cell(card, "m1", "2026-11-02")["version"] == 1


async def test_nan_and_infinity_are_rejected(login: Login) -> None:
    c = await login("u2")
    for literal in ("NaN", "Infinity", "-Infinity"):
        response = await c.put(
            f"{BASE}/measurables/m1/scores/2026-11-02",
            content=f'{{"value": {literal}}}',
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422, (literal, response.text)


async def test_unknown_measurable_and_other_teams_measurable_are_404(login: Login) -> None:
    c = await login("u2")
    assert_error(await put_score(c, "m_nope", "2026-11-02", 1), 404, "measurable_not_found")
    # m6 belongs to team_sales: through team_lead's path it does not exist
    assert_error(await put_score(c, "m6", "2026-11-02", 1), 404, "measurable_not_found")


# --- optimistic locking ---------------------------------------------------------------------------------


async def test_expected_version_matches_then_goes_stale(login: Login) -> None:
    c = await login("u2")
    ok = await put_score(c, "m1", "2026-11-02", 10, expected_version=1)
    assert ok.status_code == 200 and ok.json()["version"] == 2

    stale = await put_score(c, "m1", "2026-11-02", 99, expected_version=1)
    found = assert_error(stale, 409, "version_conflict")
    assert found["current"]["value"] == 10 and found["current"]["version"] == 2
    # nothing was written
    assert cell(await get_scorecard(c), "m1", "2026-11-02")["value"] == 10


async def test_two_editors_second_one_with_old_version_loses(login: Login) -> None:
    a, b = await login("u2"), await login("u3")
    card = await get_scorecard(a)
    version = cell(card, "m1", "2026-11-02")["version"]
    assert (await put_score(a, "m1", "2026-11-02", 15, expected_version=version)).status_code == 200
    lost = await put_score(b, "m1", "2026-11-02", 16, expected_version=version)
    assert_error(lost, 409, "version_conflict")


async def test_expected_version_on_clear_and_on_missing_cell(login: Login) -> None:
    c = await login("u2")
    assert_error(await put_score(c, "m1", "2026-11-02", None, expected_version=5), 409, "version_conflict")
    cleared = await put_score(c, "m1", "2026-11-02", None, expected_version=1)
    assert cleared.status_code == 200 and cleared.json()["version"] is None
    # the cell is gone, so an update that expects a version conflicts and reports the empty cell
    gone = await put_score(c, "m1", "2026-11-02", 9, expected_version=1)
    found = assert_error(gone, 409, "version_conflict")
    assert found["current"]["value"] is None and found["current"]["version"] is None


async def test_conflict_body_is_the_error_envelope(login: Login) -> None:
    c = await login("u2")
    response = await put_score(c, "m1", "2026-11-02", 1, expected_version=7)
    assert set(detail(response)) == {"code", "message", "current"}
