"""Shared router helpers: stable operation ids, documented error responses, request-scoped service context."""

from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.requests import HTTPConnection

from app.auth import CurrentUser, SessionDep
from app.clock import Clock, get_clock
from app.realtime.hub import Hub
from app.rules import TimeContext
from app.schemas import ErrorBody
from app.services.common import Ctx


def operation_id(route: APIRoute) -> str:
    """`{tag}_{function name}`: readable generated client method names (`issues_solve_issue`)."""
    return f"{route.tags[0]}_{route.name}" if route.tags else route.name


_DEFAULT_DESCRIPTIONS = {
    401: "Not signed in (`not_authenticated`)",
    403: "No access to this team (`team_forbidden`) or the role is read-only (`read_only_role`)",
    404: "The resource does not exist in this team",
    409: "The request conflicts with the current state",
    422: "Validation failed",
}


def errors(extra: dict[int, str] | None = None) -> dict[int | str, dict[str, Any]]:
    """`responses=` mapping that documents the error envelope. 401 is always included (every route needs a
    session); `extra` maps status -> what that status means for the route."""
    descriptions = {401: _DEFAULT_DESCRIPTIONS[401], **(extra or {})}
    return {
        status: {"model": ErrorBody, "description": text or _DEFAULT_DESCRIPTIONS.get(status, "Error")}
        for status, text in descriptions.items()
    }


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's 422 format (`detail: [{type, loc, msg, ctx}]`) minus the echoed `input`.

    The default handler echoes the offending input; a body such as `{"value": NaN}` then fails to serialize
    (not valid JSON) and surfaces as a 500. Dropping `input` also avoids reflecting client data back.
    """
    assert isinstance(exc, RequestValidationError)
    errors = [{k: v for k, v in error.items() if k != "input"} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


# --- request-scoped dependencies ------------------------------------------------------------------------


def get_hub(conn: HTTPConnection) -> Hub:
    return conn.app.state.hub


HubDep = Annotated[Hub, Depends(get_hub)]


def get_time(clock: Annotated[Clock, Depends(get_clock)]) -> TimeContext:
    """Business time for this request: derived once, shared by every dependency and the service."""
    return TimeContext.at(clock.now())


TimeDep = Annotated[TimeContext, Depends(get_time)]


def get_ctx(
    conn: HTTPConnection,
    session: SessionDep,
    user: CurrentUser,
    tc: TimeDep,
    hub: HubDep,
    clock: Annotated[Clock, Depends(get_clock)],
) -> Ctx:
    return Ctx(
        session=session,
        user=user,
        tc=tc,
        hub=hub,
        session_factory=conn.app.state.session_factory,
        clock=clock,
    )


CtxDep = Annotated[Ctx, Depends(get_ctx)]
