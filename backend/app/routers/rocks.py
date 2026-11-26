"""Rocks: the current quarter's Rocks (read-only) and the bonus Make it an Issue."""

from typing import Annotated

from fastapi import APIRouter, Path, Response

from app.auth import SessionDep, TeamReader, TeamWriter
from app.routers.common import CtxDep, TimeDep, errors, operation_id
from app.schemas import MakeIssueResult, RockList
from app.services import rocks as rocks_service

router = APIRouter(prefix="/teams/{team_id}/rocks", tags=["rocks"], generate_unique_id_function=operation_id)

RockId = Annotated[str, Path(description="Rock id, e.g. `r2`")]


@router.get(
    "",
    summary="List Rocks (current quarter)",
    description=(
        "Rocks of the current calendar quarter with computed status and progress, and their Milestones. "
        "Status: complete if seeded complete; off track if seeded off track or an unfinished Milestone is "
        "past due (a Milestone due today is not past due)."
    ),
    response_model=RockList,
    responses=errors({403: "No access to this team (`team_forbidden`)"}),
)
async def list_rocks(team: TeamReader, session: SessionDep, tc: TimeDep) -> RockList:
    return await rocks_service.list_rocks(session, tc, team.team.id)


@router.post(
    "/{rock_id}/issue",
    summary="Make it an Issue (off-track Rock)",
    description=(
        "Bonus. Creates an Issue titled `{rock} (Rock off track)` or returns the existing unsolved one for "
        "this Rock. 201 when created, 200 when it already existed."
    ),
    status_code=201,
    response_model=MakeIssueResult,
    responses={
        200: {"model": MakeIssueResult, "description": "An unsolved Issue for this Rock already existed"},
        **errors(
            {
                403: "No access to this team (`team_forbidden`) or an Observer (`read_only_role`)",
                404: "Unknown Rock in this team's current quarter (`rock_not_found`)",
                409: "The Rock is not off track (`not_off_track`)",
            }
        ),
    },
)
async def make_issue_from_rock(
    team: TeamWriter, ctx: CtxDep, rock_id: RockId, response: Response
) -> MakeIssueResult:
    issue, created = await rocks_service.make_issue(ctx, team.team.id, rock_id)
    if not created:
        response.status_code = 200
    return MakeIssueResult(issue=issue, created=created)
