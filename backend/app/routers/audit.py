"""Audit trail: the team's append-only history of business changes (read-only)."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.auth import SessionDep, TeamReader
from app.routers.common import errors, operation_id
from app.schemas import AuditPage
from app.services import audit as audit_service

router = APIRouter(prefix="/teams/{team_id}/audit", tags=["audit"], generate_unique_id_function=operation_id)


@router.get(
    "",
    summary="Audit trail",
    description=(
        "Who changed what and when: score edits, issues (created, prioritized, solved), To-Dos (created, "
        "done, reopened) and meeting steps (started, advanced, rated, ended). Newest first, keyset "
        "paginated: pass "
        "`next_before_id` of one page as `before_id` to get the next older page. Changes that did not alter "
        "anything (an idempotent done toggle, Make it an Issue returning an existing Issue) and refused "
        "requests are not recorded. Events are never updated or deleted."
    ),
    response_model=AuditPage,
    responses=errors(
        {
            403: "No access to this team (`team_forbidden`)",
            422: "`limit` outside 1..200 or `before_id` below 1",
        }
    ),
)
async def list_audit_events(
    team: TeamReader,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200, description="Page size")] = 50,
    before_id: Annotated[
        int | None,
        Query(ge=1, description="Only events with an id below this one (the previous page's cursor)"),
    ] = None,
) -> AuditPage:
    return await audit_service.list_events(session, team.team.id, limit=limit, before_id=before_id)
