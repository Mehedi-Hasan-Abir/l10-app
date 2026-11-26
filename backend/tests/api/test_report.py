"""The rules report: equals the answer key and the independent oracle, and never touches live data."""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.report import generate_report, parse_now
from tests.api.helpers import BASE, Login, db_snapshot, end_meeting, start_meeting

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
FIXTURE = BACKEND / "tests" / "fixtures" / "expected_rules_report.json"
ORACLE_PATH = REPO / "tools" / "oracle_report.py"
DEFAULT_NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)

ORACLE_DATES = [
    "2026-11-18T15:00:00Z",
    "2026-12-02T05:59:00Z",  # 23:59 CST on Dec 1: one minute before the local date changes
    "2026-12-02T06:00:00Z",  # 00:00 CST on Dec 2: the exact instant "today" moves on
    "2026-10-01T12:00:00Z",  # before most of the seed
    "2027-01-05T15:00:00Z",  # a new quarter and year: no current-quarter Rocks
]

Factory = async_sessionmaker[AsyncSession]


def load_oracle() -> ModuleType:
    spec = importlib.util.spec_from_file_location("oracle_report", ORACLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def report_at(
    factory: Factory, settings: Settings, now: datetime, *, live: bool = False
) -> dict[str, Any]:
    return await generate_report(factory, settings.seed_path, now, live=live)


# --- fresh mode equals the answer key and the oracle --------------------------------------------------------


async def test_default_clock_equals_the_committed_answer_key(
    db_session_factory: Factory, test_settings: Settings
) -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert await report_at(db_session_factory, test_settings, DEFAULT_NOW) == expected


@pytest.mark.parametrize("now", ["2026-11-11T15:00:00Z", *ORACLE_DATES])
async def test_report_equals_the_independent_oracle(
    db_session_factory: Factory, test_settings: Settings, now: str
) -> None:
    oracle = load_oracle()
    seed = json.loads(test_settings.seed_path.read_text(encoding="utf-8"))
    instant = parse_now(now)
    assert await report_at(db_session_factory, test_settings, instant) == oracle.build(seed, instant)


async def test_report_shape_and_completeness(db_session_factory: Factory, test_settings: Settings) -> None:
    report = await report_at(db_session_factory, test_settings, DEFAULT_NOW)
    assert list(report) == ["now", "teams"]
    assert report["now"] == "2026-11-11T15:00:00Z"
    assert list(report["teams"]) == ["team_lead", "team_ops", "team_sales"]
    keys = ["measurable_status", "todo_completion", "overdue_todos", "rocks", "issue_order", "meetings"]
    for team in report["teams"].values():
        assert list(team) == keys  # the order of the starter README
        assert all(len(weeks) == 13 for weeks in team["measurable_status"].values())
        assert len(team["todo_completion"]) == 13  # a week with nothing due is null, not missing
    lead = report["teams"]["team_lead"]
    assert list(lead["measurable_status"]["m1"])[0] == "2026-08-17"
    assert list(lead["measurable_status"]["m1"])[-1] == "2026-11-09"
    assert lead["todo_completion"]["2026-08-17"] is None
    assert lead["todo_completion"]["2026-11-02"] == {
        "due": 10,
        "done": 9,
        "rate_percent": 90,
        "meets_target": True,
    }
    assert lead["overdue_todos"] == ["t12", "t31"]
    assert lead["issue_order"] == ["i2", "i1", "i5", "i3", "i4"]
    assert lead["rocks"]["r2"] == {"status": "off_track", "progress_percent": 13}
    assert lead["meetings"]["mt2"]["rating"] == 8.3 and lead["meetings"]["mt1"]["rating"] == 8.0
    assert list(lead["meetings"]) == ["mt1", "mt2"]


# --- fresh mode does not touch the live database ------------------------------------------------


async def test_fresh_mode_leaves_live_data_untouched_and_ignores_it(
    login: Login, db_session_factory: Factory, test_settings: Settings, db_session: AsyncSession
) -> None:
    c = await login("u2")
    created = await c.post(f"{BASE}/issues", json={"title": "Created before the report", "priority": 5})
    assert created.status_code == 201
    new_id = created.json()["id"]
    await c.put(f"{BASE}/measurables/m1/scores/2026-11-02", json={"value": 99})
    before = await db_snapshot(db_session)
    await db_session.rollback()  # do not hold a transaction open while the report truncates

    fresh = await report_at(db_session_factory, test_settings, DEFAULT_NOW)

    assert fresh == json.loads(FIXTURE.read_text(encoding="utf-8"))  # the seed, not the live data
    assert new_id not in fresh["teams"]["team_lead"]["issue_order"]
    assert fresh["teams"]["team_lead"]["measurable_status"]["m1"]["2026-11-02"] == "off_track"
    after = await db_snapshot(db_session)
    assert after == before  # the issue and the edited score are still there
    assert any(row[0] == new_id for row in after["issues"])
    assert new_id in [i["id"] for i in (await c.get(f"{BASE}/issues")).json()]  # still served by the API


async def test_live_mode_reports_the_current_database(
    login: Login, db_session_factory: Factory, test_settings: Settings
) -> None:
    c = await login("u2")
    new_id = (await c.post(f"{BASE}/issues", json={"title": "Urgent", "priority": 5})).json()["id"]
    await c.put(f"{BASE}/measurables/m1/scores/2026-11-02", json={"value": 99})
    await c.patch(f"{BASE}/todos/t12", json={"done": True})

    live = await report_at(db_session_factory, test_settings, DEFAULT_NOW, live=True)

    lead = live["teams"]["team_lead"]
    assert lead["issue_order"] == ["i2", "i1", new_id, "i5", "i3", "i4"]  # priority 5, newest of the 5s last
    assert lead["measurable_status"]["m1"]["2026-11-02"] == "on_track"  # 99 >= 10
    assert lead["overdue_todos"] == ["t31"]  # t12 was completed


async def test_live_report_includes_meetings_ended_through_the_api(
    login: Login, db_session_factory: Factory, test_settings: Settings, frozen_clock: Any
) -> None:
    c = await login("u2")
    meeting = await start_meeting(c)
    await c.post(f"{BASE}/issues/i3/solve", json={"resolution_note": "done"})
    frozen_clock.advance(600)
    await end_meeting(c, meeting["id"])

    live = await report_at(db_session_factory, test_settings, frozen_clock.now(), live=True)
    entry = live["teams"]["team_lead"]["meetings"][meeting["id"]]
    assert entry["scorecard_week"] == "2026-11-02"
    assert entry["issues_solved"] == ["i3"] and entry["attendee_ids"] == ["u2"] and entry["rating"] is None
    fresh = await report_at(db_session_factory, test_settings, frozen_clock.now())
    assert meeting["id"] not in fresh["teams"]["team_lead"]["meetings"]


# --- the command line ------------------------------------------------------------------------


def run_cli(test_settings: Settings, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": test_settings.database_url}
    env.pop("APP_NOW", None)
    return subprocess.run(
        [sys.executable, "-m", "app.report", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


async def test_command_line_prints_the_answer_key(test_settings: Settings) -> None:
    result = await asyncio.to_thread(run_cli, test_settings)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith('{\n  "now": "2026-11-11T15:00:00Z",\n  "teams": {\n')  # indent=2
    assert json.loads(result.stdout) == json.loads(FIXTURE.read_text(encoding="utf-8"))


async def test_command_line_now_option(test_settings: Settings) -> None:
    result = await asyncio.to_thread(run_cli, test_settings, "--now", "2026-12-02T06:00:00Z")
    assert result.returncode == 0, result.stderr
    oracle = load_oracle()
    seed = json.loads(test_settings.seed_path.read_text(encoding="utf-8"))
    assert json.loads(result.stdout) == oracle.build(seed, parse_now("2026-12-02T06:00:00Z"))
    assert json.loads(result.stdout)["now"] == "2026-12-02T06:00:00Z"


def test_parse_now() -> None:
    assert parse_now("2026-11-18T15:00:00Z") == datetime(2026, 11, 18, 15, tzinfo=UTC)
    assert parse_now("2026-11-18T10:00:00-05:00") == datetime(2026, 11, 18, 15, tzinfo=UTC)
    assert parse_now("2026-11-18T15:00:00") == datetime(2026, 11, 18, 15, tzinfo=UTC)  # naive means UTC
