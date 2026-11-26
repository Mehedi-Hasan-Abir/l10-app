"""The rules, driven straight from seed.json, must reproduce the answer key at the default clock.

Pure: reads two JSON files, no database. This is the rule-level twin of the API/report test, so a rules
regression is caught without Postgres. Skipped when the seed file is not available (e.g. a slim image).
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.rules import (
    Goal,
    IssueFacts,
    MilestoneFacts,
    TimeContext,
    TodoFacts,
    is_overdue,
    measurable_status,
    meeting_rating,
    order_issues,
    rock_progress,
    rock_status,
    scorecard_week_for,
    scorecard_weeks,
    todo_completion,
    week_start_of,
)
from app.rules.timeutil import company_date

NOW = "2026-11-11T15:00:00Z"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "expected_rules_report.json"
SEED = Path(os.environ.get("SEED_PATH") or Path(__file__).resolve().parents[3] / "seed" / "seed.json")


def ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@pytest.fixture(scope="module")
def seed() -> dict[str, Any]:
    if not SEED.is_file():
        pytest.skip(f"seed file not found at {SEED}")
    return json.loads(SEED.read_text(encoding="utf-8"), parse_float=Decimal)  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def expected() -> dict[str, Any]:
    if not FIXTURE.is_file():
        pytest.skip(f"answer key not found at {FIXTURE}")
    return json.loads(FIXTURE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def goal_of(raw: dict[str, Any]) -> Goal:
    return Goal(raw["type"], raw.get("value"), raw.get("min"), raw.get("max"))


def team_ids(seed: dict[str, Any]) -> list[str]:
    return sorted(t["id"] for t in seed["teams"])


def test_the_answer_key_is_for_the_default_clock(expected: dict[str, Any]) -> None:
    assert expected["now"] == NOW


def test_measurable_status_matches_the_answer_key(seed: dict[str, Any], expected: dict[str, Any]) -> None:
    ctx = TimeContext.at(ts(NOW))
    scores = {(s["measurable_id"], date.fromisoformat(s["week_start"])): s["value"] for s in seed["scores"]}
    for team in team_ids(seed):
        actual = {
            m["id"]: {
                week.isoformat(): measurable_status(
                    goal_of(m["goal"]), scores.get((m["id"], week)), week, ctx.current_week
                )
                for week in scorecard_weeks(ctx.current_week)
            }
            for m in seed["measurables"]
            if m["team_id"] == team
        }
        assert actual == expected["teams"][team]["measurable_status"], team


def test_todo_completion_and_overdue_match_the_answer_key(
    seed: dict[str, Any], expected: dict[str, Any]
) -> None:
    ctx = TimeContext.at(ts(NOW))
    for team in team_ids(seed):
        facts = [
            TodoFacts(t["id"], date.fromisoformat(t["due_date"]), ts(t["done_at"]) if t["done_at"] else None)
            for t in seed["todos"]
            if t["team_id"] == team
        ]
        completion: dict[str, Any] = {}
        for week in scorecard_weeks(ctx.current_week):
            result = todo_completion(facts, week)
            completion[week.isoformat()] = (
                None
                if result is None
                else {
                    "due": result.due,
                    "done": result.done,
                    "rate_percent": result.rate_percent,
                    "meets_target": result.meets_target,
                }
            )
        assert completion == expected["teams"][team]["todo_completion"], team
        overdue = sorted(f.id for f in facts if is_overdue(f.due_date, f.done_at, ctx.today))
        assert overdue == expected["teams"][team]["overdue_todos"], team


def test_rock_status_and_progress_match_the_answer_key(
    seed: dict[str, Any], expected: dict[str, Any]
) -> None:
    ctx = TimeContext.at(ts(NOW))
    for team in team_ids(seed):
        actual: dict[str, Any] = {}
        for rock in seed["rocks"]:
            if rock["team_id"] != team or rock["quarter"] != ctx.quarter:
                continue
            milestones = [
                MilestoneFacts(date.fromisoformat(m["due_date"]), m["done"])
                for m in seed["milestones"]
                if m["rock_id"] == rock["id"]
            ]
            actual[rock["id"]] = {
                "status": rock_status(rock["status"], milestones, ctx.today),
                "progress_percent": rock_progress(rock["status"], milestones),
            }
        assert actual == expected["teams"][team]["rocks"], team


def test_issue_order_matches_the_answer_key(seed: dict[str, Any], expected: dict[str, Any]) -> None:
    for team in team_ids(seed):
        unsolved = [
            IssueFacts(i["id"], i["priority"], ts(i["created_at"]))
            for i in seed["issues"]
            if i["team_id"] == team and i["solved_at"] is None
        ]
        assert [i.id for i in order_issues(unsolved)] == expected["teams"][team]["issue_order"], team


def test_meeting_scorecard_week_status_and_rating_match_the_answer_key(
    seed: dict[str, Any], expected: dict[str, Any]
) -> None:
    scores = {(s["measurable_id"], date.fromisoformat(s["week_start"])): s["value"] for s in seed["scores"]}
    for meeting in seed["meetings"]:
        if not meeting["ended_at"]:
            continue
        want = expected["teams"][meeting["team_id"]]["meetings"][meeting["id"]]
        started = ts(meeting["started_at"])
        week = scorecard_week_for(started)
        meeting_week = week_start_of(company_date(started))
        assert week.isoformat() == want["scorecard_week"], meeting["id"]
        statuses = {
            m["id"]: measurable_status(goal_of(m["goal"]), scores.get((m["id"], week)), week, meeting_week)
            for m in seed["measurables"]
            if m["team_id"] == meeting["team_id"]
        }
        assert statuses == want["measurable_status"], meeting["id"]
        rating = meeting_rating([r["rating"] for r in meeting["ratings"]])
        assert (None if rating is None else float(rating)) == want["rating"], meeting["id"]
