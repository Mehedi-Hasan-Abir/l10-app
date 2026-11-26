"""Auth endpoints: seeded-user switcher login (no passwords), logout, who am I."""

import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    CurrentUser,
    SessionDep,
    SettingsDep,
    clear_session_cookie,
    set_session_cookie,
)
from app.config import Settings
from app.errors import not_found
from app.models import Membership, Team, User
from app.ratelimit import SlidingWindowLimiter
from app.schemas import ErrorBody, TeamRef, UserRef  # one definition: OpenAPI component names stay put

router = APIRouter(prefix="/auth", tags=["auth"])


class UserWithTeams(UserRef):
    """`UserRef` plus the user's teams (every team for admins); feeds the login screen's user switcher."""

    teams: list[TeamRef]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str


class LoginResponse(BaseModel):
    user: UserRef


class MeResponse(BaseModel):
    user: UserRef
    teams: list[TeamRef]


def _login_limiter(request: Request, settings: Settings) -> SlidingWindowLimiter:
    """The app's login limiter, created on first use from the settings (stored on `app.state`).

    No `await` between the lookup and the assignment, so concurrent first requests cannot create two.
    """
    limiter: SlidingWindowLimiter | None = getattr(request.app.state, "login_limiter", None)
    if limiter is None:
        limiter = SlidingWindowLimiter(settings.login_rate_limit_per_minute, window_s=60.0)
        request.app.state.login_limiter = limiter
    return limiter


async def enforce_login_rate_limit(request: Request, settings: SettingsDep) -> None:
    """429 `rate_limited` (+ `Retry-After`) once a client IP exceeds `LOGIN_RATE_LIMIT_PER_MINUTE`.

    Runs before the user lookup, so probing unknown user ids is limited exactly like real sign-ins. The key is
    `request.client.host`; behind Caddy uvicorn's `--proxy-headers` makes that the real client address.
    """
    retry_after = _login_limiter(request, settings).hit(request.client.host if request.client else "unknown")
    if retry_after is None:
        return
    seconds = max(1, math.ceil(retry_after))
    # HTTPException (not AppError) because the response needs a `Retry-After` header; the body keeps the
    # standard `{"detail": {"code", "message"}}` envelope.
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": "rate_limited", "message": f"Too many sign-in attempts; try again in {seconds} s"},
        headers={"Retry-After": str(seconds)},
    )


_LOGIN_429: dict[int | str, dict[str, Any]] = {
    429: {
        "model": ErrorBody,
        "description": "Too many sign-in attempts from this address (`rate_limited`); see `Retry-After`",
        "headers": {
            "Retry-After": {
                "description": "Seconds until another sign-in attempt is accepted",
                "schema": {"type": "integer", "minimum": 1},
            }
        },
    }
}


async def _teams_for(session: AsyncSession, user: User) -> list[TeamRef]:
    query = select(Team.id, Team.name).where(Team.company_id == user.company_id).order_by(Team.name, Team.id)
    if user.role != "admin":
        query = query.join(Membership, Membership.team_id == Team.id).where(Membership.user_id == user.id)
    return [TeamRef(id=row.id, name=row.name) for row in await session.execute(query)]


@router.get("/users")
async def list_users(session: SessionDep) -> list[UserWithTeams]:
    users = list(await session.scalars(select(User).order_by(User.name, User.id)))
    teams = list(await session.scalars(select(Team).order_by(Team.name, Team.id)))
    memberships = (await session.execute(select(Membership.user_id, Membership.team_id))).all()
    team_ids_by_user: dict[str, set[str]] = {}
    for user_id, team_id in memberships:
        team_ids_by_user.setdefault(user_id, set()).add(team_id)
    return [
        UserWithTeams(
            **UserRef.model_validate(u).model_dump(),
            teams=[
                TeamRef(id=t.id, name=t.name)
                for t in teams
                if t.company_id == u.company_id
                and (u.role == "admin" or t.id in team_ids_by_user.get(u.id, ()))
            ],
        )
        for u in users
    ]


@router.post(
    "/login",
    dependencies=[Depends(enforce_login_rate_limit)],
    responses=_LOGIN_429,
)
async def login(
    body: LoginRequest, response: Response, session: SessionDep, settings: SettingsDep
) -> LoginResponse:
    user = await session.get(User, body.user_id)
    if user is None:
        raise not_found("user_not_found", "Unknown user")
    set_session_cookie(response, settings, user.id)
    return LoginResponse(user=UserRef.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, settings: SettingsDep) -> None:
    clear_session_cookie(response, settings)


@router.get("/me")
async def me(user: CurrentUser, session: SessionDep) -> MeResponse:
    return MeResponse(
        user=UserRef.model_validate(user),
        teams=await _teams_for(session, user),
    )
