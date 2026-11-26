"""Business-time clock (PLAN section 2).

Business logic never reads the system clock. It asks the injected `Clock` for `now()`.

* `FrozenClock`  - tests and the rules report: `now()` is a fixed instant.
* `ManualClock`  - a frozen clock that tests can `advance(seconds)` (meeting timer tests).
* `RunningClock` - the server: `now() = APP_NOW + real time elapsed since an anchor`. The anchor is
  persisted in table `app_clock`, so an API restart with the same `APP_NOW` continues the same timeline.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection


class Clock(Protocol):
    def now(self) -> datetime:
        """Current business time as an aware UTC datetime."""
        ...


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class FrozenClock:
    """`now()` always returns the same instant."""

    def __init__(self, instant: datetime) -> None:
        self._now = _as_utc(instant)

    def now(self) -> datetime:
        return self._now


class ManualClock(FrozenClock):
    """A frozen clock that only moves when a test calls `advance`."""

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


class RunningClock:
    """`now() = app_now + elapsed`, where `elapsed` = elapsed-at-startup + monotonic time since startup.

    `elapsed_at_start` is measured entirely on the database clock (`clock_timestamp() - real_anchor`), so
    host/container clock skew cannot shift business time. After startup only a monotonic timer is used.
    """

    def __init__(
        self,
        app_now: datetime,
        elapsed_at_start: timedelta,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._app_now = _as_utc(app_now)
        self._elapsed_at_start = max(elapsed_at_start, timedelta(0))
        self._monotonic = monotonic
        self._started = monotonic()

    def now(self) -> datetime:
        since_start = timedelta(seconds=self._monotonic() - self._started)
        return self._app_now + self._elapsed_at_start + since_start


async def build_running_clock(
    session_factory: async_sessionmaker[AsyncSession], app_now: datetime
) -> RunningClock:
    """Anchor `app_now` to the real time of first startup (idempotent across restarts).

    The database `now()` is used ONLY as the real-time anchor for elapsed time; it is never business time.
    """
    app_now = _as_utc(app_now)
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO app_clock (app_now, real_anchor) VALUES (:app_now, now()) ON CONFLICT DO NOTHING"
            ),
            {"app_now": app_now},
        )
        row = (
            await session.execute(
                text(
                    "SELECT clock_timestamp() - real_anchor AS elapsed "
                    "FROM app_clock WHERE app_now = :app_now"
                ),
                {"app_now": app_now},
            )
        ).one()
    return RunningClock(app_now, row.elapsed)


def get_clock(conn: HTTPConnection) -> Clock:
    """FastAPI dependency: `app.state.clock` (tests inject one via `create_app(clock=...)`)."""
    return conn.app.state.clock
