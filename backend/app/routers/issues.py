"""Issues: list, create, prioritize, solve, and add a To-Do from an Issue."""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query

from app.auth import SessionDep, TeamReader, TeamWriter
from app.routers.common import CtxDep, errors, operation_id
from app.schemas import Issue, IssueCreate, IssueSolve, IssueTodoCreate, IssueUpdate, Todo
from app.services import issues as issues_service
from app.services import todos as todos_service

router = APIRouter(
    prefix="/teams/{team_id}/issues", tags=["issues"], generate_unique_id_function=operation_id
)

IssueId = Annotated[str, Path(description="Issue id, e.g. `i1`")]

_WRITE_403 = "No access to this team (`team_forbidden`) or an Observer (`read_only_role`)"
_ISSUE_404 = "Unknown issue in this team (`issue_not_found`)"


@router.get(
    "",
    summary="List Issues",
    description=(
        "`unsolved` (default) in Issue order: priority 5 down to 1, oldest first within a priority, no "
        "priority last. `solved` newest-solved first."
    ),
    response_model=list[Issue],
    responses=errors({403: "No access to this team (`team_forbidden`)"}),
)
async def list_issues(
    team: TeamReader,
    session: SessionDep,
    status: Annotated[Literal["unsolved", "solved"], Query(description="Which issues to list")] = "unsolved",
) -> list[Issue]:
    return await issues_service.list_issues(session, team.team.id, status)


@router.post(
    "",
    summary="Create an Issue",
    description="Owner is the caller; priority is optional.",
    status_code=201,
    response_model=Issue,
    responses=errors({403: _WRITE_403}),
)
async def create_issue(team: TeamWriter, ctx: CtxDep, body: IssueCreate) -> Issue:
    return await issues_service.create_issue(ctx, team.team.id, body)


@router.patch(
    "/{issue_id}",
    summary="Set an Issue's priority",
    description="`priority` 1..5 or null. Send `expected_version` for lost-update protection.",
    response_model=Issue,
    responses=errors(
        {
            403: _WRITE_403,
            404: _ISSUE_404,
            409: "Already solved (`issue_solved`) or stale `expected_version` (`version_conflict`)",
        }
    ),
)
async def set_priority(team: TeamWriter, ctx: CtxDep, issue_id: IssueId, body: IssueUpdate) -> Issue:
    return await issues_service.set_priority(ctx, team.team.id, issue_id, body)


@router.post(
    "/{issue_id}/solve",
    summary="Solve an Issue",
    description="A resolution note is required (1-2000 characters).",
    response_model=Issue,
    responses=errors({403: _WRITE_403, 404: _ISSUE_404, 409: "Already solved (`issue_solved`)"}),
)
async def solve_issue(team: TeamWriter, ctx: CtxDep, issue_id: IssueId, body: IssueSolve) -> Issue:
    return await issues_service.solve_issue(ctx, team.team.id, issue_id, body)


@router.post(
    "/{issue_id}/todos",
    summary="Add a To-Do from an Issue",
    description=(
        "Creates a To-Do linked to the Issue, due in 7 days (company calendar). The owner must be an "
        "Admin or Team Member of the team (or any Admin)."
    ),
    status_code=201,
    response_model=Todo,
    responses=errors(
        {403: _WRITE_403, 404: _ISSUE_404, 422: "Owner not eligible (`owner_not_eligible`) or invalid body"}
    ),
)
async def add_todo(team: TeamWriter, ctx: CtxDep, issue_id: IssueId, body: IssueTodoCreate) -> Todo:
    return await todos_service.create_from_issue(ctx, team.team.id, issue_id, body)
