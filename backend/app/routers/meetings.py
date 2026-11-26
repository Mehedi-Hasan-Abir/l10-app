"""Meetings: the Level 10 state machine, recap and frozen summary.

Two routers: team-scoped (`/teams/{team_id}/meetings`) and meeting-scoped (`/meetings/{meeting_id}`). The
meeting-scoped dependencies load the meeting and then authorize against its team, so a meeting of another team
is a 403 `team_forbidden`, exactly like the team itself.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Path, Query

from app.auth import CurrentUser, SessionDep, TeamReader, TeamWriter
from app.clock import Clock, get_clock
from app.models import Meeting
from app.routers.common import CtxDep, HubDep, TimeDep, errors, operation_id
from app.schemas import MeetingAdvance, MeetingRecap, MeetingState, MeetingSummary, RatingUpdate
from app.services import meetings as meetings_service

team_router = APIRouter(
    prefix="/teams/{team_id}/meetings", tags=["meetings"], generate_unique_id_function=operation_id
)
router = APIRouter(prefix="/meetings", tags=["meetings"], generate_unique_id_function=operation_id)

MeetingId = Annotated[str, Path(description="Meeting id, e.g. `mt2`")]

_WRITE_403 = "No access to this team (`team_forbidden`) or an Observer (`read_only_role`)"
_READ_403 = "No access to the meeting's team (`team_forbidden`)"
_MEETING_404 = "Unknown meeting (`meeting_not_found`)"
_FACILITATOR_403 = "Only the facilitator may do this (`not_facilitator`), or no access to the team"


async def meeting_reader(meeting_id: MeetingId, user: CurrentUser, session: SessionDep) -> Meeting:
    meeting, _ = await meetings_service.load_meeting(session, user, meeting_id, write=False)
    return meeting


async def meeting_writer(meeting_id: MeetingId, user: CurrentUser, session: SessionDep) -> Meeting:
    meeting, _ = await meetings_service.load_meeting(session, user, meeting_id, write=True)
    return meeting


MeetingReader = Annotated[Meeting, Depends(meeting_reader)]
MeetingWriter = Annotated[Meeting, Depends(meeting_writer)]
ClockDep = Annotated[Clock, Depends(get_clock)]


# --- team-scoped ----------------------------------------------------------------------------------------


@team_router.get(
    "",
    summary="List a team's meetings",
    description="The active meeting first, then ended meetings newest first. Filter with `status`.",
    response_model=list[MeetingState],
    responses=errors({403: _READ_403}),
)
async def list_meetings(
    team: TeamReader,
    session: SessionDep,
    hub: HubDep,
    clock: ClockDep,
    status: Annotated[
        Literal["active", "ended"] | None, Query(description="Only active or only ended")
    ] = None,
) -> list[MeetingState]:
    return await meetings_service.list_meetings(session, team.team.id, status, hub=hub, clock=clock)


@team_router.post(
    "",
    summary="Start a meeting",
    description=(
        "The caller becomes the facilitator and is auto-joined. Starts in the Scorecard section reviewing "
        "the last closed week. One active meeting per team, enforced by a partial unique index."
    ),
    status_code=201,
    response_model=MeetingState,
    responses=errors(
        {
            403: _WRITE_403,
            409: "The team already has an active meeting (`meeting_already_active`, `detail.meeting_id`)",
        }
    ),
)
async def start_meeting(team: TeamWriter, ctx: CtxDep) -> MeetingState:
    return await meetings_service.start_meeting(ctx, team.team.id)


# --- meeting-scoped -------------------------------------------------------------------------------------


@router.get(
    "/{meeting_id}",
    summary="Get a meeting",
    description=(
        "Full meeting state. `server_now` is the clock time at serialization: the client derives its offset "
        "and renders `elapsed = (now + offset) - section_started_at`."
    ),
    response_model=MeetingState,
    responses=errors({403: _READ_403, 404: _MEETING_404}),
)
async def get_meeting(
    meeting: MeetingReader, session: SessionDep, hub: HubDep, clock: ClockDep
) -> MeetingState:
    return await meetings_service.get_state(session, meeting.id, hub=hub, clock=clock)


@router.post(
    "/{meeting_id}/join",
    summary="Join a meeting",
    description="Idempotent. Anyone who can read the team may join, Observers included.",
    response_model=MeetingState,
    responses=errors({403: _READ_403, 404: _MEETING_404, 409: "The meeting has ended (`meeting_ended`)"}),
)
async def join_meeting(meeting: MeetingReader, ctx: CtxDep) -> MeetingState:
    return await meetings_service.join_meeting(ctx, meeting.id)


@router.post(
    "/{meeting_id}/advance",
    summary="Advance to the next section",
    description=(
        "Facilitator only. scorecard -> ids -> conclude. A section that overruns never advances by itself."
    ),
    response_model=MeetingState,
    responses=errors(
        {
            403: _FACILITATOR_403,
            404: _MEETING_404,
            409: "Already in Conclude (`no_next_section`), ended (`meeting_ended`), or the meeting already "
            "left `from_section` (`stale_section`)",
        }
    ),
)
async def advance_meeting(
    meeting: MeetingReader, ctx: CtxDep, body: Annotated[MeetingAdvance | None, Body()] = None
) -> MeetingState:
    from_section = body.from_section if body is not None else None
    return await meetings_service.advance_meeting(ctx, meeting.id, from_section)


@router.post(
    "/{meeting_id}/end",
    summary="End the meeting",
    description=(
        "Facilitator only, from any section. Writes `ended_at` and the frozen summary snapshot in one "
        "transaction."
    ),
    response_model=MeetingState,
    responses=errors({403: _FACILITATOR_403, 404: _MEETING_404, 409: "Already ended (`meeting_ended`)"}),
)
async def end_meeting(meeting: MeetingReader, ctx: CtxDep) -> MeetingState:
    return await meetings_service.end_meeting(ctx, meeting.id)


@router.put(
    "/{meeting_id}/rating",
    summary="Rate the meeting",
    description=(
        "Upsert the caller's 1-10 rating; changeable until the meeting ends. Only in the Conclude "
        "section, and only for attendees who are Admins or Team Members."
    ),
    response_model=MeetingState,
    responses=errors(
        {
            403: _WRITE_403,
            404: _MEETING_404,
            409: "`not_in_conclude`, `not_attendee` (join first) or `meeting_ended`",
        }
    ),
)
async def rate_meeting(meeting: MeetingWriter, ctx: CtxDep, body: RatingUpdate) -> MeetingState:
    return await meetings_service.put_rating(ctx, meeting.id, body)


@router.get(
    "/{meeting_id}/recap",
    summary="Meeting recap (live)",
    description=(
        "Issues solved and To-Dos created during the meeting window `[started_at, ended_at or now]`, as full "
        "objects. Used by the Conclude section."
    ),
    response_model=MeetingRecap,
    responses=errors({403: _READ_403, 404: _MEETING_404}),
)
async def get_recap(meeting: MeetingReader, session: SessionDep, tc: TimeDep) -> MeetingRecap:
    return await meetings_service.get_recap(session, tc, meeting)


@router.get(
    "/{meeting_id}/summary",
    summary="Meeting summary (frozen)",
    description=(
        "The snapshot written when the meeting ended: Measurable statuses for the reviewed week, Issues "
        "solved, To-Dos created, attendees and rating. Never recomputed, so later edits do not change it."
    ),
    response_model=MeetingSummary,
    responses=errors(
        {403: _READ_403, 404: _MEETING_404, 409: "The meeting is still active (`meeting_active`)"}
    ),
)
async def get_summary(meeting: MeetingReader) -> MeetingSummary:
    return meetings_service.get_summary(meeting)
