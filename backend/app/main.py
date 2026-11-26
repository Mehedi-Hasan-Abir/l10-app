"""FastAPI application factory."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from sqlalchemy import text

from app.clock import Clock, FrozenClock, build_running_clock, get_clock
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.errors import AppError, app_error_handler
from app.observability import RequestContextMiddleware, configure_logging, init_sentry
from app.realtime.hub import Hub
from app.routers import api_router, ws
from app.routers.common import validation_error_handler

__all__ = ["app", "create_app", "get_clock"]

log = logging.getLogger(__name__)

READY_TIMEOUT_S = 2.0


class ReadyStatus(BaseModel):
    status: Literal["ready", "unavailable"]


def create_app(*, settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    """Build the app. An injected `clock` wins; else `APP_CLOCK=frozen` -> FrozenClock, else RunningClock.

    The engine, session factory and clock are created in the lifespan and stored on `app.state`
    (`engine`, `session_factory`, `clock`; `settings` is available from creation).
    """
    settings = settings or get_settings()
    configure_logging()  # level from LOG_LEVEL; idempotent
    init_sentry()  # no-op unless SENTRY_DSN is set

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        session_factory = create_session_factory(engine)
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.hub = Hub()
        if clock is not None:
            app.state.clock = clock
        elif settings.app_clock == "frozen":
            app.state.clock = FrozenClock(settings.app_now)
        else:
            app.state.clock = await build_running_clock(session_factory, settings.app_now)
        try:
            yield
        finally:
            await app.state.hub.close_all()
            await engine.dispose()

    app = FastAPI(
        title="L10 API",
        version="0.1.0",
        description="Level 10 meeting app: Scorecard, Rocks, To-Dos, Issues and a live weekly meeting.",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_middleware(
        RequestContextMiddleware
    )  # request id + access log (HTTP only; WebSockets pass through)

    @app.get("/api/health", tags=["health"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        """The process is up and serving. Does not touch the database (see `/api/ready`)."""
        return {"status": "ok"}

    @app.get(
        "/api/ready",
        tags=["health"],
        summary="Readiness probe",
        responses={503: {"model": ReadyStatus, "description": "The database is unreachable or too slow"}},
    )
    async def ready(request: Request, response: Response) -> ReadyStatus:
        """Ready to serve traffic: a `SELECT 1` through the app's session factory succeeds within 2 s."""
        try:
            async with asyncio.timeout(READY_TIMEOUT_S):
                async with request.app.state.session_factory() as session:
                    await session.execute(text("SELECT 1"))
        except Exception:  # unreachable, refused, timed out, or the lifespan has not run
            log.warning("readiness check failed", exc_info=True)
            response.status_code = 503
            return ReadyStatus(status="unavailable")
        return ReadyStatus(status="ready")

    app.include_router(api_router)
    app.include_router(ws.router)  # WS /ws/teams/{team_id}: outside /api, the proxy routes /ws/* here
    return app


app = create_app()
