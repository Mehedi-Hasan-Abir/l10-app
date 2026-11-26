"""Session cookie, /api/auth/*, and the team authorization dependencies (PLAN section 5)."""

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    COOKIE_NAME,
    TeamReader,
    TeamWriter,
    authenticate_websocket,
    eligible_owner_ids,
    load_team_context,
    origin_allowed,
    sign_session,
)
from app.errors import AppError
from app.models import User

Login = Callable[[str], Awaitable[httpx.AsyncClient]]


def team_ids(body: dict[str, Any]) -> set[str]:
    return {t["id"] for t in body["teams"]}


# --- login / me / logout ----------------------------------------------------------------------------------


async def test_login_sets_httponly_cookie_and_me_returns_user_and_teams(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"user_id": "u2"})
    assert response.status_code == 200
    assert response.json() == {"user": {"id": "u2", "name": "Blake Brooks", "role": "team_member"}}

    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{COOKIE_NAME}=")
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/" in set_cookie
    assert "Secure" not in set_cookie  # COOKIE_SECURE=false in the test settings

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["user"] == {"id": "u2", "name": "Blake Brooks", "role": "team_member"}
    assert team_ids(body) == {"team_lead", "team_sales"}
    assert {t["name"] for t in body["teams"]} == {"Leadership", "Sales"}


async def test_admin_sees_every_team(login: Login) -> None:
    admin = await login("u1")
    body = (await admin.get("/api/auth/me")).json()
    assert body["user"]["role"] == "admin"
    assert team_ids(body) == {"team_lead", "team_sales", "team_ops"}


async def test_observer_sees_only_their_teams(login: Login) -> None:
    observer = await login("u7")
    body = (await observer.get("/api/auth/me")).json()
    assert body["user"]["role"] == "observer"
    assert team_ids(body) == {"team_sales"}


async def test_unauthenticated_me_is_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "not_authenticated"


async def test_tampered_cookie_is_401(client: httpx.AsyncClient) -> None:
    await client.post("/api/auth/login", json={"user_id": "u2"})
    cookie = client.cookies[COOKIE_NAME]
    assert (await client.get("/api/auth/me")).status_code == 200

    # Change the first payload character (always changes the signed bytes; flipping the last base64
    # character of the signature can decode to the same bytes), and also try forging another user's cookie.
    tampered = ("A" if cookie[0] != "A" else "B") + cookie[1:]
    client.cookies.set(COOKIE_NAME, tampered)
    assert (await client.get("/api/auth/me")).status_code == 401

    client.cookies.set(COOKIE_NAME, "u1")
    assert (await client.get("/api/auth/me")).status_code == 401

    client.cookies.set(COOKIE_NAME, sign_session(_other_secret_settings(), "u1"))
    assert (await client.get("/api/auth/me")).status_code == 401


def _other_secret_settings() -> Any:
    return SimpleNamespace(secret_key="not-the-server-secret")


async def test_cookie_for_a_deleted_user_is_401(client: httpx.AsyncClient, app: FastAPI) -> None:
    settings = app.state.settings
    client.cookies.set(COOKIE_NAME, sign_session(settings, "u_does_not_exist"))
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_unknown_user_is_404(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"user_id": "nobody"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "user_not_found"
    assert "set-cookie" not in response.headers


async def test_login_rejects_unknown_fields(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/auth/login", json={"user_id": "u1", "role": "admin"})
    assert response.status_code == 422


async def test_logout_clears_the_session(client: httpx.AsyncClient) -> None:
    await client.post("/api/auth/login", json={"user_id": "u3"})
    assert (await client.get("/api/auth/me")).status_code == 200

    response = await client.post("/api/auth/logout")
    assert response.status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_two_logged_in_clients_coexist(login: Login) -> None:
    first, second = await login("u2"), await login("u6")
    assert (await first.get("/api/auth/me")).json()["user"]["id"] == "u2"
    assert (await second.get("/api/auth/me")).json()["user"]["id"] == "u6"


async def test_user_switcher_lists_everyone_with_their_teams(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/auth/users")  # public: feeds the login screen
    assert response.status_code == 200
    users = {u["id"]: u for u in response.json()}
    assert set(users) == {f"u{n}" for n in range(1, 10)}
    assert users["u1"]["role"] == "admin"
    assert {t["id"] for t in users["u1"]["teams"]} == {"team_lead", "team_sales", "team_ops"}
    assert {t["id"] for t in users["u4"]["teams"]} == {"team_lead", "team_ops"}
    assert users["u5"]["role"] == "observer"
    assert "email" not in users["u5"]


async def test_health_and_docs_paths(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/health")).json() == {"status": "ok"}
    assert (await client.get("/api/openapi.json")).status_code == 200
    assert (await client.get("/api/docs")).status_code == 200


# --- team authorization (throwaway routes that use the real dependencies) -----------------------------------


@pytest.fixture
def authz_routes(app: FastAPI) -> FastAPI:
    async def read(ctx: TeamReader) -> dict[str, Any]:
        return {"team": ctx.team.id, "can_write": ctx.can_write, "is_admin": ctx.is_admin}

    async def write(ctx: TeamWriter) -> dict[str, Any]:
        return {"team": ctx.team.id, "can_write": ctx.can_write, "is_admin": ctx.is_admin}

    app.add_api_route("/t/teams/{team_id}/read", read, methods=["GET"])
    app.add_api_route("/t/teams/{team_id}/write", write, methods=["POST"])
    return app


async def test_authz_requires_login(client: httpx.AsyncClient, authz_routes: FastAPI) -> None:
    response = await client.get("/t/teams/team_lead/read")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "not_authenticated"


async def test_member_reads_and_writes_own_team(login: Login, authz_routes: FastAPI) -> None:
    u2 = await login("u2")
    read = await u2.get("/t/teams/team_sales/read")
    assert read.status_code == 200
    assert read.json() == {"team": "team_sales", "can_write": True, "is_admin": False}
    assert (await u2.post("/t/teams/team_sales/write")).status_code == 200


async def test_member_of_other_team_is_403_team_forbidden(login: Login, authz_routes: FastAPI) -> None:
    u2 = await login("u2")  # member of team_lead and team_sales, not team_ops
    for method, path in (("GET", "read"), ("POST", "write")):
        response = await u2.request(method, f"/t/teams/team_ops/{path}")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "team_forbidden"


async def test_nonexistent_team_is_indistinguishable_from_forbidden_for_non_admins(
    login: Login, authz_routes: FastAPI
) -> None:
    u2 = await login("u2")
    forbidden = await u2.get("/t/teams/team_ops/read")
    missing = await u2.get("/t/teams/no_such_team/read")
    assert missing.status_code == forbidden.status_code == 403
    assert missing.json() == forbidden.json()


async def test_admin_gets_404_for_nonexistent_team_and_access_to_every_team(
    login: Login, authz_routes: FastAPI
) -> None:
    admin = await login("u1")
    missing = await admin.get("/t/teams/no_such_team/read")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "team_not_found"
    for team in ("team_lead", "team_sales", "team_ops"):
        response = await admin.post(f"/t/teams/{team}/write")
        assert response.status_code == 200
        assert response.json()["is_admin"] is True


async def test_observer_reads_but_cannot_write(login: Login, authz_routes: FastAPI) -> None:
    u5 = await login("u5")  # observer in team_lead
    read = await u5.get("/t/teams/team_lead/read")
    assert read.status_code == 200
    assert read.json()["can_write"] is False

    write = await u5.post("/t/teams/team_lead/write")
    assert write.status_code == 403
    assert write.json()["detail"]["code"] == "read_only_role"


async def test_observer_outside_their_team_gets_team_forbidden_not_read_only(
    login: Login, authz_routes: FastAPI
) -> None:
    u5 = await login("u5")  # observer in team_lead only
    response = await u5.post("/t/teams/team_sales/write")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "team_forbidden"


# --- load_team_context / eligible_owner_ids directly --------------------------------------------------------


async def test_load_team_context_matrix(db_session: AsyncSession) -> None:
    async def ctx(user_id: str, team_id: str, *, write: bool) -> Any:
        user = await db_session.get(User, user_id)
        assert user is not None
        return await load_team_context(db_session, user, team_id, write=write)

    ok = await ctx("u4", "team_ops", write=True)
    assert (ok.team.id, ok.can_write, ok.is_admin) == ("team_ops", True, False)

    with pytest.raises(AppError) as excinfo:
        await ctx("u4", "team_sales", write=False)
    assert (excinfo.value.status, excinfo.value.code) == (403, "team_forbidden")

    with pytest.raises(AppError) as excinfo:
        await ctx("u1", "nope", write=False)
    assert (excinfo.value.status, excinfo.value.code) == (404, "team_not_found")


async def test_eligible_owner_ids(db_session: AsyncSession) -> None:
    # admins (u1) everywhere + team_member members; observers (u5, u7) never.
    assert await eligible_owner_ids(db_session, "team_lead") == {"u1", "u2", "u3", "u4"}
    assert await eligible_owner_ids(db_session, "team_sales") == {"u1", "u2", "u6"}
    assert await eligible_owner_ids(db_session, "team_ops") == {"u1", "u4", "u8", "u9"}
    assert await eligible_owner_ids(db_session, "no_such_team") == set()


# --- websocket handshake helper ---------------------------------------------------------------------------


def _fake_websocket(app: FastAPI, *, origin: str | None, cookie: str | None) -> WebSocket:
    headers = {} if origin is None else {"origin": origin}
    cookies = {} if cookie is None else {COOKIE_NAME: cookie}
    return cast(WebSocket, SimpleNamespace(app=app, headers=headers, cookies=cookies))


async def test_authenticate_websocket(app: FastAPI, db_session: AsyncSession) -> None:
    good = sign_session(app.state.settings, "u3")

    ws = _fake_websocket(app, origin="http://localhost:3000", cookie=good)
    user = await authenticate_websocket(ws, db_session)
    assert user is not None and user.id == "u3"

    # Non-browser clients send no Origin header.
    user = await authenticate_websocket(_fake_websocket(app, origin=None, cookie=good), db_session)
    assert user is not None and user.id == "u3"

    evil = _fake_websocket(app, origin="https://evil.example", cookie=good)
    assert origin_allowed(evil) is False
    assert await authenticate_websocket(evil, db_session) is None

    assert await authenticate_websocket(_fake_websocket(app, origin=None, cookie=None), db_session) is None
    tampered = _fake_websocket(app, origin=None, cookie=good[:-1] + ("A" if good[-1] != "A" else "B"))
    assert await authenticate_websocket(tampered, db_session) is None
