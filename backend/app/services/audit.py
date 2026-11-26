"""Audit trail (HARDENING H3): one append-only event per business mutation.

Contract for callers (the mutating services):

* `record` is called inside the mutation's own transaction, BEFORE its `commit()`, and only when state
  actually changed. It never commits, so a rolled-back request leaves no audit row and a committed change
  always has one.
* `at` is the injected clock's time (the same instant the service stores on the entity where it stores one),
  never the system clock.
* The database forbids UPDATE and DELETE on `audit_events` (trigger `audit_events_append_only`).
"""

from datetime import datetime
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas as sc
from app.models import AuditEvent, User
from app.services.common import user_ref


async def record(
    session: AsyncSession,
    *,
    team_id: str,
    actor_id: str,
    action: sc.AuditAction,
    entity_type: sc.AuditEntityType,
    entity_id: str,
    at: datetime,
    data: dict[str, Any] | None = None,
) -> None:
    """Add one audit row to the caller's open transaction. Never commits.

    `data` may hold `Decimal`, `date` and `datetime` values; they are converted to JSON-safe values here
    (a Decimal becomes a JSON number, exactly as the API serializes scores).
    """
    await session.execute(
        pg_insert(AuditEvent).values(
            team_id=team_id,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            at=at,
            data=jsonable_encoder(data or {}),
        )
    )


async def list_events(
    session: AsyncSession, team_id: str, *, limit: int, before_id: int | None
) -> sc.AuditPage:
    """Newest-first keyset page of the team's events: ids strictly below `before_id` (when given).

    One extra row is fetched to know whether another page exists; `next_before_id` is then the id of the last
    (oldest) event on this page.
    """
    query = (
        select(AuditEvent, User)
        .join(User, User.id == AuditEvent.actor_id)
        .where(AuditEvent.team_id == team_id)
        .order_by(AuditEvent.id.desc())
        .limit(limit + 1)
    )
    if before_id is not None:
        query = query.where(AuditEvent.id < before_id)
    rows = (await session.execute(query)).all()
    page = rows[:limit]
    events = [
        sc.AuditEvent.model_validate(
            {
                "id": event.id,
                "team_id": event.team_id,
                "actor": user_ref(actor),
                "action": event.action,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "at": event.at,
                "data": event.data,
            }
        )
        for event, actor in page
    ]
    next_before_id = page[-1][0].id if len(rows) > limit else None
    return sc.AuditPage(events=events, next_before_id=next_before_id)
