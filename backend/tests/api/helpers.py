"""Shared helpers for the API tests (plain functions and context managers; no autouse fixtures)."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from httpx_ws import AsyncWebSocketSession, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Issue,
    Meeting,
    MeetingAttendee,
    MeetingRating,
    Score,
    Todo,
)

Login = Callable[[str], Awaitable[httpx.AsyncClient]]

TEAM = "team_lead"
BASE = f"/api/teams/{TEAM}"
NOW_ISO = "2026-11-11T15:00:00Z"


def detail(response: httpx.Response) -> dict[str, Any]:
    """The `detail` object of an error response."""
    body = response.json()
    assert isinstance(body["detail"], dict), body
    return body["detail"]


def assert_error(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    found = detail(response)
    assert found["code"] == code, found
    return found


async def put_score(
    c: httpx.AsyncClient,
    measurable: str,
    week: str,
    value: float | int | None,
    *,
    team: str = TEAM,
    **extra: Any,
) -> httpx.Response:
    return await c.put(
        f"/api/teams/{team}/measurables/{measurable}/scores/{week}", json={"value": value, **extra}
    )


async def make_issue(c: httpx.AsyncClient, measurable: str, week: str, *, team: str = TEAM) -> httpx.Response:
    return await c.post(f"/api/teams/{team}/measurables/{measurable}/scores/{week}/issue")


async def start_meeting(c: httpx.AsyncClient, team: str = TEAM) -> dict[str, Any]:
    response = await c.post(f"/api/teams/{team}/meetings")
    assert response.status_code == 201, response.text
    return response.json()


async def join(c: httpx.AsyncClient, meeting_id: str) -> dict[str, Any]:
    response = await c.post(f"/api/meetings/{meeting_id}/join")
    assert response.status_code == 200, response.text
    return response.json()


async def advance(c: httpx.AsyncClient, meeting_id: str) -> dict[str, Any]:
    response = await c.post(f"/api/meetings/{meeting_id}/advance")
    assert response.status_code == 200, response.text
    return response.json()


async def end_meeting(c: httpx.AsyncClient, meeting_id: str) -> dict[str, Any]:
    response = await c.post(f"/api/meetings/{meeting_id}/end")
    assert response.status_code == 200, response.text
    return response.json()


def cell(scorecard: dict[str, Any], measurable: str, week: str) -> dict[str, Any]:
    """One cell of a scorecard response."""
    weeks = scorecard["weeks"]
    row = next(m for m in scorecard["measurables"] if m["id"] == measurable)
    return row["cells"][weeks.index(week)]


async def db_snapshot(session: AsyncSession) -> dict[str, Any]:
    """Everything a write could change, for "nothing changed" assertions."""
    session.expire_all()
    return {
        "issues": sorted(
            (r.id, r.title, r.priority, r.version, r.solved_at, r.resolution_note)
            for r in await session.scalars(select(Issue))
        ),
        "todos": sorted((r.id, r.title, r.done_at, r.due_date) for r in await session.scalars(select(Todo))),
        "scores": sorted(
            (r.measurable_id, r.week_start, r.value, r.version) for r in await session.scalars(select(Score))
        ),
        "meetings": sorted(
            (r.id, r.current_section, r.ended_at, r.facilitator_id)
            for r in await session.scalars(select(Meeting))
        ),
        "attendees": (await session.scalar(select(func.count()).select_from(MeetingAttendee))),
        "ratings": sorted(
            (r.meeting_id, r.user_id, r.rating) for r in await session.scalars(select(MeetingRating))
        ),
    }


@asynccontextmanager
async def ws_connect(
    app: FastAPI,
    user_client: httpx.AsyncClient,
    team: str = TEAM,
    *,
    origin: str | None = "http://localhost:3000",
    cookies: bool = True,
) -> AsyncIterator[AsyncWebSocketSession]:
    """An open WebSocket on `app` (same event loop and hub as the REST clients), authenticated like
    `user_client` (cookie jar copied). `origin=None` sends no Origin header (non-browser client)."""
    headers: dict[str, str] = {}
    if origin is not None:
        headers["origin"] = origin
    async with httpx.AsyncClient(
        transport=ASGIWebSocketTransport(app=app),
        base_url="http://test",
        cookies=user_client.cookies if cookies else None,
    ) as ws_client:
        async with aconnect_ws(f"/ws/teams/{team}", ws_client, headers=headers) as ws:
            yield ws
