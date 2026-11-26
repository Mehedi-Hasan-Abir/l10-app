"""Session cookie + server-side authorization (PLAN section 5).

Authorization lives here and nowhere else:

* `current_user`            - 401 `not_authenticated` without a valid signed session cookie.
* `team_reader(team_id)`    - any member (or any admin) may read a team.
* `team_writer(team_id)`    - admins/team members may write; observers get 403 `read_only_role`.
* Non-admins get 403 `team_forbidden` for both "not a member" and "team does not exist" (no id probing);
  admins get 404 `team_not_found` for a team that does not exist.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Response, WebSocket
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection

from app.config import Settings, settings_from
from app.db import get_session
from app.errors import forbidden, not_found, unauthorized
from app.models import Membership, Team, User

COOKIE_NAME = "l10_session"
SESSION_MAX_AGE_S = 14 * 24 * 3600
_SALT = "l10-session-v1"

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(settings_from)]


# --- cookie signing -------------------------------------------------------------------------------------


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=_SALT)


def sign_session(settings: Settings, user_id: str) -> str:
    return _serializer(settings.secret_key).dumps(user_id)


def read_session(settings: Settings, token: str | None) -> str | None:
    """The user id inside a valid cookie value, else None (missing, tampered, or expired)."""
    if not token:
        return None
    try:
        value = _serializer(settings.secret_key).loads(token, max_age=SESSION_MAX_AGE_S)
    except BadSignature:
        return None
    return value if isinstance(value, str) else None


def set_session_cookie(response: Response, settings: Settings, user_id: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        sign_session(settings, user_id),
        max_age=SESSION_MAX_AGE_S,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=settings.cookie_secure
    )


# --- current user ---------------------------------------------------------------------------------------


async def _user_from_cookie(conn: HTTPConnection, session: AsyncSession) -> User | None:
    user_id = read_session(conn.app.state.settings, conn.cookies.get(COOKIE_NAME))
    if user_id is None:
        return None
    return await session.get(User, user_id)


async def current_user(request: HTTPConnection, session: SessionDep) -> User:
    user = await _user_from_cookie(request, session)
    if user is None:
        raise unauthorized("not_authenticated", "Authentication required")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


# --- team authorization ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class TeamContext:
    team: Team
    user: User
    can_write: bool
    is_admin: bool


async def load_team_context(session: AsyncSession, user: User, team_id: str, *, write: bool) -> TeamContext:
    """Authorize `user` on `team_id`. Reusable by routes keyed by another id (e.g. a meeting id).

    Order matters: membership is decided first, so a non-member observer gets `team_forbidden`, never
    `read_only_role`.
    """
    team = (
        await session.execute(select(Team).where(Team.id == team_id, Team.company_id == user.company_id))
    ).scalar_one_or_none()
    is_admin = user.role == "admin"

    if is_admin:
        if team is None:
            raise not_found("team_not_found", "Team not found")
        return TeamContext(team=team, user=user, can_write=True, is_admin=True)

    is_member = team is not None and bool(
        await session.scalar(
            select(exists().where(Membership.user_id == user.id, Membership.team_id == team.id))
        )
    )
    if team is None or not is_member:
        raise forbidden("team_forbidden", "You do not have access to this team")

    can_write = user.role == "team_member"
    if write and not can_write:
        raise forbidden("read_only_role", "Your role is read-only")
    return TeamContext(team=team, user=user, can_write=can_write, is_admin=False)


async def team_reader(team_id: str, user: CurrentUser, session: SessionDep) -> TeamContext:
    return await load_team_context(session, user, team_id, write=False)


async def team_writer(team_id: str, user: CurrentUser, session: SessionDep) -> TeamContext:
    return await load_team_context(session, user, team_id, write=True)


TeamReader = Annotated[TeamContext, Depends(team_reader)]
TeamWriter = Annotated[TeamContext, Depends(team_writer)]


async def eligible_owner_ids(session: AsyncSession, team_id: str) -> set[str]:
    """Users who may own an issue or to-do in `team_id`: its admin/team_member members plus all admins.

    Observers are never eligible. (Admins act in every team of their company.)
    """
    company_id = select(Team.company_id).where(Team.id == team_id).scalar_subquery()
    members = select(Membership.user_id).where(Membership.team_id == team_id)
    rows = await session.scalars(
        select(User.id).where(
            User.company_id == company_id,
            (User.role == "admin") | ((User.role == "team_member") & User.id.in_(members)),
        )
    )
    return set(rows)


# --- websocket ------------------------------------------------------------------------------------------


def origin_allowed(websocket: WebSocket) -> bool:
    """Cross-site WebSocket hijacking guard. Browsers always send Origin; non-browser clients may omit it."""
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    settings: Settings = websocket.app.state.settings
    return origin.rstrip("/") in settings.allowed_origins_list


async def authenticate_websocket(websocket: WebSocket, session: AsyncSession) -> User | None:
    """The user behind the handshake cookie, or None (bad Origin, missing/tampered cookie, unknown user).

    To tell the two failures apart (close 4403 vs 4401) call `origin_allowed(websocket)` first.
    """
    if not origin_allowed(websocket):
        return None
    return await _user_from_cookie(websocket, session)
