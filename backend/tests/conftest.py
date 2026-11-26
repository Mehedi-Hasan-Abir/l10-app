"""Shared fixtures. Nothing here is autouse: pure rules tests never touch Postgres.

DB-backed fixtures (all opt-in; `tests/api/conftest.py` makes `seeded_db` autouse for API tests):

* `migrated_db`   (session)  recreates the `public` schema of the TEST database, then `alembic upgrade head`
* `db_session_factory` (session) async sessionmaker bound to the test database
* `seeded_db`     (function) truncates every table and loads `seed/seed.json`
* `frozen_clock`  (function) ManualClock at 2026-11-11T15:00:00Z (`frozen_clock.advance(seconds)` moves it)
* `app`           (function) `create_app(settings=<test db>, clock=frozen_clock)` with its lifespan running
* `client`        (function) anonymous `httpx.AsyncClient` on `app`
* `login`         (function) `await login("u2")` -> a NEW AsyncClient holding u2's session cookie (call it
                  repeatedly for concurrent clients; all are closed at teardown)
* `db_session`    (function) AsyncSession for direct SQL assertions (use `expire_all()` / `populate_existing`
                  if you reuse ORM objects after the app wrote)
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
import pytest
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alembic import command
from app.clock import ManualClock
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.main import create_app
from app.seed import load_seed

BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_NOW = datetime(2026, 11, 11, 15, 0, 0, tzinfo=UTC)
# Serializes concurrent pytest sessions (e.g. two agents) that share the one `l10_test` database.
_ADVISORY_LOCK_KEY = 7_101_001


def _psycopg_dsn(sqlalchemy_url: str) -> str:
    url = make_url(sqlalchemy_url)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    base = get_settings()
    url = base.test_database_url
    database = make_url(url).database or ""
    assert database.endswith("_test"), f"refusing to run tests against non-test database {database!r}"
    return Settings(
        database_url=url,
        app_clock="frozen",
        app_now=APP_NOW,
        cookie_secure=False,
        seed_path=base.seed_path,
        allowed_origins="http://localhost:3000",
        login_rate_limit_per_minute=0,  # off: every test signs in from one address
    )


@pytest.fixture(scope="session")
def migrated_db(test_settings: Settings) -> Iterator[None]:
    """Fresh schema at alembic head. Holds an advisory lock for the whole session (see _ADVISORY_LOCK_KEY)."""
    dsn = _psycopg_dsn(test_settings.database_url)
    with psycopg.connect(dsn, autocommit=True) as lock_conn:
        deadline = time.monotonic() + 180
        while not lock_conn.execute("SELECT pg_try_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,)).fetchone()[0]:
            if time.monotonic() > deadline:
                raise RuntimeError("another pytest session has been holding the l10_test lock for 180s")
            time.sleep(0.5)
        lock_conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
        lock_conn.execute("CREATE SCHEMA public")
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", test_settings.database_url)
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")
        yield
        lock_conn.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))


@pytest.fixture(scope="session")
async def db_session_factory(
    test_settings: Settings, migrated_db: None
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(test_settings.database_url)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def seeded_db(
    db_session_factory: async_sessionmaker[AsyncSession], test_settings: Settings
) -> AsyncIterator[None]:
    """Every test that asks for this starts from exactly the seed."""
    async with db_session_factory() as session, session.begin():
        await load_seed(session, test_settings.seed_path, reset=True)
    yield


@pytest.fixture
def frozen_clock() -> ManualClock:
    return ManualClock(APP_NOW)


@pytest.fixture
async def app(
    test_settings: Settings, frozen_clock: ManualClock, migrated_db: None
) -> AsyncIterator[FastAPI]:
    application = create_app(settings=test_settings, clock=frozen_clock)
    async with application.router.lifespan_context(application):
        yield application


def _new_client(application: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=application), base_url="http://test")


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with _new_client(app) as c:
        yield c


@pytest.fixture
async def login(app: FastAPI) -> AsyncIterator[Callable[[str], Awaitable[httpx.AsyncClient]]]:
    opened: list[httpx.AsyncClient] = []

    async def _login(user_id: str) -> httpx.AsyncClient:
        c = _new_client(app)
        opened.append(c)
        response = await c.post("/api/auth/login", json={"user_id": user_id})
        assert response.status_code == 200, response.text
        return c

    yield _login
    for c in opened:
        await c.aclose()


@pytest.fixture
async def db_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with db_session_factory() as session:
        yield session
        await session.rollback()
