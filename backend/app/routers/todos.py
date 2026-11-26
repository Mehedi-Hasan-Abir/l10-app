"""To-Dos: list with last-closed-week completion, done toggle, Make it an Issue from an overdue To-Do."""

from typing import Annotated

from fastapi import APIRouter, Path, Response

from app.auth import SessionDep, TeamReader, TeamWriter
from app.routers.common import CtxDep, TimeDep, errors, operation_id
from app.schemas import MakeIssueResult, Todo, TodoList, TodoUpdate
from app.services import todos as todos_service

router = APIRouter(prefix="/teams/{team_id}/todos", tags=["todos"], generate_unique_id_function=operation_id)

TodoId = Annotated[str, Path(description="To-Do id, e.g. `t12`")]

_WRITE_403 = "No access to this team (`team_forbidden`) or an Observer (`read_only_role`)"
_TODO_404 = "Unknown To-Do in this team (`todo_not_found`)"


@router.get(
    "",
    summary="List To-Dos",
    description=(
        "Open To-Dos first (due date ascending), then done ones (done time descending). `completion` is the "
        "last closed week's rate (null when nothing was due): done by the week's end / due that week, "
        "target 90%."
    ),
    response_model=TodoList,
    responses=errors({403: "No access to this team (`team_forbidden`)"}),
)
async def list_todos(team: TeamReader, session: SessionDep, tc: TimeDep) -> TodoList:
    return await todos_service.list_todos(session, tc, team.team.id)


@router.patch(
    "/{todo_id}",
    summary="Mark a To-Do done or undo it",
    description="`done: true` sets `done_at` to now (kept if already done); `false` clears it. Idempotent.",
    response_model=Todo,
    responses=errors({403: _WRITE_403, 404: _TODO_404}),
)
async def set_todo_done(team: TeamWriter, ctx: CtxDep, todo_id: TodoId, body: TodoUpdate) -> Todo:
    return await todos_service.set_done(ctx, team.team.id, todo_id, body)


@router.post(
    "/{todo_id}/issue",
    summary="Make it an Issue (overdue To-Do)",
    description=(
        "Bonus. Creates an Issue titled `{to-do} (To-Do overdue)` or returns the existing unsolved one for "
        "this To-Do. 201 when created, 200 when it already existed."
    ),
    status_code=201,
    response_model=MakeIssueResult,
    responses={
        200: {"model": MakeIssueResult, "description": "An unsolved Issue for this To-Do already existed"},
        **errors({403: _WRITE_403, 404: _TODO_404, 409: "The To-Do is not overdue (`not_overdue`)"}),
    },
)
async def make_issue_from_todo(
    team: TeamWriter, ctx: CtxDep, todo_id: TodoId, response: Response
) -> MakeIssueResult:
    issue, created = await todos_service.make_issue(ctx, team.team.id, todo_id)
    if not created:
        response.status_code = 200
    return MakeIssueResult(issue=issue, created=created)
