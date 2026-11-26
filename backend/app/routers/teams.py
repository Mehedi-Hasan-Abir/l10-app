"""Teams: the switcher list and team detail."""

from fastapi import APIRouter

from app.auth import CurrentUser, SessionDep, TeamReader
from app.routers.common import TimeDep, errors, operation_id
from app.schemas import TeamDetail, TeamRef
from app.services import teams as teams_service

router = APIRouter(prefix="/teams", tags=["teams"], generate_unique_id_function=operation_id)


@router.get(
    "",
    summary="List my teams",
    description="Teams the caller can open: their memberships, or every team for an Admin.",
    response_model=list[TeamRef],
    responses=errors(),
)
async def list_teams(user: CurrentUser, session: SessionDep) -> list[TeamRef]:
    return await teams_service.list_teams(session, user)


@router.get(
    "/{team_id}",
    summary="Team detail",
    description=(
        "Members, eligible To-Do owners, the caller's permissions, the business clock (`APP_NOW`) and the "
        "active meeting, if any."
    ),
    response_model=TeamDetail,
    responses=errors(
        {
            403: "Not a member of this team, or the team does not exist (`team_forbidden`)",
            404: "Admins only: unknown team (`team_not_found`)",
        }
    ),
)
async def get_team(team: TeamReader, session: SessionDep, tc: TimeDep) -> TeamDetail:
    return await teams_service.get_team_detail(session, tc, team)
