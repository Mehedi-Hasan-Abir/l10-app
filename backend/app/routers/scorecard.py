"""Scorecard: the 13-week grid, score edits, and Make it an Issue for an off-track cell."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Path, Response

from app.auth import SessionDep, TeamReader, TeamWriter
from app.routers.common import CtxDep, TimeDep, errors, operation_id
from app.schemas import MakeIssueResult, Scorecard, ScoreCell, ScoreUpdate
from app.services import scorecard as scorecard_service

router = APIRouter(prefix="/teams/{team_id}", tags=["scorecard"], generate_unique_id_function=operation_id)

MeasurableId = Annotated[str, Path(description="Measurable id, e.g. `m1`")]
WeekStart = Annotated[date, Path(description="Monday of the week (company time), `YYYY-MM-DD`")]

_WRITE_403 = "No access to this team (`team_forbidden`) or an Observer (`read_only_role`)"
_MEASURABLE_404 = "Unknown measurable in this team (`measurable_not_found`)"


@router.get(
    "/scorecard",
    summary="Scorecard (13 weeks)",
    description=(
        "Measurables by week for the 13 weeks ending with the current week, oldest first. `cells` align with "
        "`weeks`. Status is computed server-side: a closed week without a score is `off_track`, the current "
        "week without a score is `pending`."
    ),
    response_model=Scorecard,
    responses=errors({403: "No access to this team (`team_forbidden`)"}),
)
async def get_scorecard(team: TeamReader, session: SessionDep, tc: TimeDep) -> Scorecard:
    return await scorecard_service.get_scorecard(session, tc, team.team.id)


@router.put(
    "/measurables/{measurable_id}/scores/{week_start}",
    summary="Set or clear a score",
    description=(
        "Last save wins. Send `value: null` to clear the cell. Send `expected_version` (from the cell) for "
        "lost-update protection: a stale version returns 409 `version_conflict` with the current cell in "
        "`detail.current`."
    ),
    response_model=ScoreCell,
    responses=errors(
        {
            403: _WRITE_403,
            404: _MEASURABLE_404,
            409: "Stale `expected_version` (`version_conflict`, current cell in `detail.current`)",
            422: "`week_not_monday`, `week_out_of_window`, or an invalid value",
        }
    ),
)
async def put_score(
    team: TeamWriter,
    ctx: CtxDep,
    measurable_id: MeasurableId,
    week_start: WeekStart,
    body: ScoreUpdate,
) -> ScoreCell:
    return await scorecard_service.put_score(ctx, team.team.id, measurable_id, week_start, body)


@router.post(
    "/measurables/{measurable_id}/scores/{week_start}/issue",
    summary="Make it an Issue (off-track score)",
    description=(
        "Creates an Issue titled `{measurable}, week of {Mon D}`, owned by the caller, or returns the "
        "existing unsolved one for that cell. Duplicate-free under concurrency (partial unique index + "
        "`ON CONFLICT DO NOTHING`). 201 when created, 200 when it already existed."
    ),
    status_code=201,
    response_model=MakeIssueResult,
    responses={
        200: {"model": MakeIssueResult, "description": "An unsolved Issue for this cell already existed"},
        **errors(
            {
                403: _WRITE_403,
                404: _MEASURABLE_404,
                409: "The cell is not off track (`not_off_track`)",
                422: "Not a Monday or outside the 13-week window",
            }
        ),
    },
)
async def make_issue_from_score(
    team: TeamWriter,
    ctx: CtxDep,
    measurable_id: MeasurableId,
    week_start: WeekStart,
    response: Response,
) -> MakeIssueResult:
    issue, created = await scorecard_service.make_issue(ctx, team.team.id, measurable_id, week_start)
    if not created:
        response.status_code = 200
    return MakeIssueResult(issue=issue, created=created)
