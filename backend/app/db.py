"""Async SQLAlchemy engine/session plumbing. The engine lives on `app.state` (created in the app lifespan)."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import HTTPConnection


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(conn: HTTPConnection) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one AsyncSession per request (shared by every dependency of that request).

    Nothing is committed implicitly: services call `await session.commit()`. An uncommitted session is rolled
    back when it closes.
    """
    async with conn.app.state.session_factory() as session:
        yield session
