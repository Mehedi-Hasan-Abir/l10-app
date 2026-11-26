"""Meetings: the state machine (start, join, advance, rate, end), recap, summary, and realtime notifications.

Postgres owns meeting state. The WebSocket only notifies: every state change is a REST call and a DB
transaction, and the realtime message is built from a fresh DB read after the commit.

Concurrency:
* one active meeting per team is enforced by the partial unique index `uq_meeting_one_active_per_team`
  (a racing start loses with an IntegrityError, mapped to 409 `meeting_already_active`);
* advance and end lock the meeting row `FOR UPDATE`; join and rating take `FOR SHARE`, so an end cannot slip
  between a join/rating check and its write, and the snapshot written at end includes everything committed.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocket

from app import schemas as sc
from app.auth import TeamContext, load_team_context
from app.clock import Clock
from app.errors import conflict, forbidden, not_found
from app.ids import new_id
from app.models import Issue, Meeting, MeetingAttendee, MeetingRating, Todo, User
from app.realtime.hub import Hub
from app.rules import AGENDA, TimeContext, meeting_rating, next_section
from app.services import audit
from app.services import issues as issues_service
from app.services import todos as todos_service
from app.services.common import Ctx, user_ref
from app.services.events import invalidate
from app.services.meeting_summary import build_meeting_summary

AGENDA_ITEMS = [
    sc.AgendaItem(key=key, title=title, duration_seconds=seconds) for key, title, seconds in AGENDA
]  # type: ignore[arg-type]

_ONE_ACTIVE_INDEX = "uq_meeting_one_active_per_team"


# --- loading --------------------------------------------------------------------------------------------


async def load_meeting(
    session: AsyncSession, user: User, meeting_id: str, *, write: bool
) -> tuple[Meeting, TeamContext]:
    """The meeting plus the caller's access to its team.

    A meeting of a team the caller cannot access -> 403 `team_forbidden` (decided by `load_team_context`).
    An unknown id gives non-admins the *same* 403, so meeting ids can't be probed (PLAN §5); admins, who can
    see every team, get 404 `meeting_not_found`.
    """
    meeting = await session.get(Meeting, meeting_id)
    if meeting is None:
        if user.role != "admin":
            raise forbidden("team_forbidden", "You do not have access to this team")
        raise not_found("meeting_not_found", "Meeting not found")
    access = await load_team_context(session, user, meeting.team_id, write=write)
    return meeting, access


async def _lock(session: AsyncSession, meeting_id: str, *, share: bool = False) -> Meeting:
    """Re-read the meeting row under a row lock (`FOR UPDATE`, or `FOR SHARE` when `share`)."""
    meeting = (
        await session.execute(
            select(Meeting)
            .where(Meeting.id == meeting_id)
            .with_for_update(read=share)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if meeting is None:
        raise not_found("meeting_not_found", "Meeting not found")
    return meeting


# --- state ----------------------------------------------------------------------------------------------


async def build_states(
    session: AsyncSession, meetings: Sequence[Meeting], *, hub: Hub, now: datetime
) -> list[sc.MeetingState]:
    """`MeetingState` for each meeting using four queries in total (no per-meeting round trips)."""
    if not meetings:
        return []
    meeting_ids = [m.id for m in meetings]
    facilitators = {
        u.id: u
        for u in await session.scalars(select(User).where(User.id.in_({m.facilitator_id for m in meetings})))
    }
    attendees: dict[str, list[sc.Attendee]] = defaultdict(list)
    online_by_team = {m.team_id: hub.online_user_ids(m.team_id) for m in meetings}
    team_of = {m.id: m.team_id for m in meetings}
    ended = {m.id for m in meetings if m.ended_at is not None}
    attendee_rows = await session.execute(
        select(MeetingAttendee.meeting_id, MeetingAttendee.joined_at, User)
        .join(User, User.id == MeetingAttendee.user_id)
        .where(MeetingAttendee.meeting_id.in_(meeting_ids))
        .order_by(MeetingAttendee.joined_at, User.id)
    )
    for meeting_id, joined_at, user in attendee_rows:
        attendees[meeting_id].append(
            sc.Attendee(
                **user_ref(user).model_dump(),
                joined_at=joined_at,
                online=meeting_id not in ended and user.id in online_by_team[team_of[meeting_id]],
            )
        )
    ratings: dict[str, list[sc.RatingEntry]] = defaultdict(list)
    rating_rows = await session.execute(
        select(MeetingRating.meeting_id, MeetingRating.user_id, MeetingRating.rating, User.name)
        .join(User, User.id == MeetingRating.user_id)
        .where(MeetingRating.meeting_id.in_(meeting_ids))
        .order_by(MeetingRating.user_id)
    )
    for meeting_id, user_id, rating, name in rating_rows:
        ratings[meeting_id].append(sc.RatingEntry(user_id=user_id, name=name, rating=rating))

    states = []
    for m in meetings:
        average = meeting_rating([r.rating for r in ratings[m.id]])
        states.append(
            sc.MeetingState(
                id=m.id,
                team_id=m.team_id,
                status="ended" if m.ended_at is not None else "active",
                facilitator=user_ref(facilitators[m.facilitator_id]),
                started_at=m.started_at,
                ended_at=m.ended_at,
                scorecard_week=m.scorecard_week,
                agenda=AGENDA_ITEMS,
                current_section=m.current_section,  # type: ignore[arg-type]
                section_started_at=m.section_started_at,
                server_now=now,
                attendees=attendees[m.id],
                ratings=ratings[m.id],
                average_rating=None if average is None else float(average),
            )
        )
    return states


async def get_state(session: AsyncSession, meeting_id: str, *, hub: Hub, clock: Clock) -> sc.MeetingState:
    """Fresh state of one meeting (re-reads the row, so it never serves a stale identity-map copy)."""
    meeting = (
        await session.execute(
            select(Meeting).where(Meeting.id == meeting_id).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if meeting is None:
        raise not_found("meeting_not_found", "Meeting not found")
    return (await build_states(session, [meeting], hub=hub, now=clock.now()))[0]


async def active_state(
    session: AsyncSession, team_id: str, *, hub: Hub, clock: Clock
) -> sc.MeetingState | None:
    meeting = (
        await session.execute(select(Meeting).where(Meeting.team_id == team_id, Meeting.ended_at.is_(None)))
    ).scalar_one_or_none()
    if meeting is None:
        return None
    return (await build_states(session, [meeting], hub=hub, now=clock.now()))[0]


async def list_meetings(
    session: AsyncSession,
    team_id: str,
    status: Literal["active", "ended"] | None,
    *,
    hub: Hub,
    clock: Clock,
) -> list[sc.MeetingState]:
    """Meetings of a team: the active one first, then ended ones newest first."""
    query = select(Meeting).where(Meeting.team_id == team_id)
    if status == "active":
        query = query.where(Meeting.ended_at.is_(None))
    elif status == "ended":
        query = query.where(Meeting.ended_at.is_not(None))
    meetings = list(await session.scalars(query))
    meetings.sort(key=lambda m: (m.ended_at is not None, -(m.ended_at or m.started_at).timestamp(), m.id))
    return await build_states(session, meetings, hub=hub, now=clock.now())


# --- realtime -------------------------------------------------------------------------------------------


async def publish_meeting_state(
    hub: Hub,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Clock,
    team_id: str,
    meeting_id: str | None = None,
    *,
    exclude: WebSocket | None = None,
) -> None:
    """Push `{"type":"meeting"}` to the team: that meeting, or the team's active one (presence changes).

    The state is read from the database inside the hub's per-team lock, after the caller's commit.
    `exclude` skips one socket (the one that just connected and already received a fresh snapshot).
    """

    async def build() -> dict[str, Any] | None:
        async with session_factory() as session:
            if meeting_id is None:
                state = await active_state(session, team_id, hub=hub, clock=clock)
            else:
                state = await get_state(session, meeting_id, hub=hub, clock=clock)
        return None if state is None else {"type": "meeting", "meeting": state.model_dump(mode="json")}

    await hub.publish_lazy(team_id, build, exclude=exclude)


async def snapshot_message(
    session_factory: async_sessionmaker[AsyncSession], hub: Hub, clock: Clock, team_id: str
) -> dict[str, Any]:
    """The message a freshly connected socket gets first: the team's active meeting, or null."""
    async with session_factory() as session:
        state = await active_state(session, team_id, hub=hub, clock=clock)
    return {"type": "snapshot", "meeting": None if state is None else state.model_dump(mode="json")}


async def _notify(ctx: Ctx, team_id: str, meeting_id: str) -> None:
    await publish_meeting_state(ctx.hub, ctx.session_factory, ctx.clock, team_id, meeting_id)
    await invalidate(ctx, team_id, "meetings")


# --- state machine --------------------------------------------------------------------------------------


async def start_meeting(ctx: Ctx, team_id: str) -> sc.MeetingState:
    """Caller becomes facilitator and is auto-joined; section `scorecard`; reviews the last closed week."""
    session = ctx.session
    meeting_id, user_id, now = new_id("mt"), ctx.user.id, ctx.now
    try:
        await session.execute(
            pg_insert(Meeting).values(
                id=meeting_id,
                team_id=team_id,
                facilitator_id=user_id,
                started_at=now,
                ended_at=None,
                current_section="scorecard",
                section_started_at=now,
                scorecard_week=ctx.tc.last_closed_week,
                summary=None,
            )
        )
        await session.execute(
            pg_insert(MeetingAttendee).values(meeting_id=meeting_id, user_id=user_id, joined_at=now)
        )
        await audit.record(
            session,
            team_id=team_id,
            actor_id=user_id,
            action="meeting.started",
            entity_type="meeting",
            entity_id=meeting_id,
            at=now,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if _ONE_ACTIVE_INDEX not in str(exc.orig):
            raise
        active_id = await session.scalar(
            select(Meeting.id).where(Meeting.team_id == team_id, Meeting.ended_at.is_(None))
        )
        raise conflict(
            "meeting_already_active", "This team already has an active meeting", meeting_id=active_id
        ) from exc
    await _notify(ctx, team_id, meeting_id)
    return await get_state(session, meeting_id, hub=ctx.hub, clock=ctx.clock)


async def join_meeting(ctx: Ctx, meeting_id: str) -> sc.MeetingState:
    """Idempotent. Any reader of the team may join (observers included); an ended meeting is 409."""
    session = ctx.session
    meeting = await _lock(session, meeting_id, share=True)
    if meeting.ended_at is not None:
        raise conflict("meeting_ended", "This meeting has ended")
    team_id = meeting.team_id
    inserted = await session.execute(
        pg_insert(MeetingAttendee)
        .values(meeting_id=meeting_id, user_id=ctx.user.id, joined_at=ctx.now)
        .on_conflict_do_nothing()
        .returning(MeetingAttendee.user_id)
    )
    newly_joined = inserted.first() is not None
    await session.commit()
    if newly_joined:
        await _notify(ctx, team_id, meeting_id)
    return await get_state(session, meeting_id, hub=ctx.hub, clock=ctx.clock)


def _require_facilitator(meeting: Meeting, user_id: str, action: str) -> None:
    if meeting.facilitator_id != user_id:
        raise forbidden("not_facilitator", f"Only the facilitator can {action} the meeting")


async def advance_meeting(ctx: Ctx, meeting_id: str, from_section: str | None = None) -> sc.MeetingState:
    """scorecard -> ids -> conclude. Facilitator only. Overrunning a section never advances by itself.

    `from_section` is the section the client was looking at. If the locked row has already moved on (a
    double-click, or two tabs), the request is refused with 409 `stale_section` instead of skipping a section.
    """
    session = ctx.session
    meeting = await _lock(session, meeting_id)
    _require_facilitator(meeting, ctx.user.id, "advance")
    if meeting.ended_at is not None:
        raise conflict("meeting_ended", "This meeting has ended")
    if from_section is not None and from_section != meeting.current_section:
        raise conflict(
            "stale_section",
            "The meeting already moved on; refresh to see the current section",
            current_section=meeting.current_section,
        )
    following = next_section(meeting.current_section)
    if following is None:
        raise conflict("no_next_section", "Already in the last section; end the meeting instead")
    team_id = meeting.team_id
    advanced_at = ctx.clock.now()
    await audit.record(
        session,
        team_id=team_id,
        actor_id=ctx.user.id,
        action="meeting.advanced",
        entity_type="meeting",
        entity_id=meeting_id,
        at=advanced_at,
        data={"from": meeting.current_section, "to": following},
    )
    meeting.current_section = following
    meeting.section_started_at = advanced_at
    await session.commit()
    await _notify(ctx, team_id, meeting_id)
    return await get_state(session, meeting_id, hub=ctx.hub, clock=ctx.clock)


async def end_meeting(ctx: Ctx, meeting_id: str) -> sc.MeetingState:
    """Facilitator only, any section. `ended_at` and the frozen `summary` are written in ONE statement."""
    session = ctx.session
    meeting = await _lock(session, meeting_id)
    _require_facilitator(meeting, ctx.user.id, "end")
    if meeting.ended_at is not None:
        raise conflict("meeting_ended", "This meeting has already ended")
    team_id = meeting.team_id
    ended_at = max(ctx.clock.now(), meeting.started_at)
    summary = await build_meeting_summary(session, meeting, until=ended_at)
    await session.execute(
        update(Meeting).where(Meeting.id == meeting_id).values(ended_at=ended_at, summary=summary)
    )
    await audit.record(
        session,
        team_id=team_id,
        actor_id=ctx.user.id,
        action="meeting.ended",
        entity_type="meeting",
        entity_id=meeting_id,
        at=ended_at,
    )
    await session.commit()
    await _notify(ctx, team_id, meeting_id)
    return await get_state(session, meeting_id, hub=ctx.hub, clock=ctx.clock)


async def put_rating(ctx: Ctx, meeting_id: str, body: sc.RatingUpdate) -> sc.MeetingState:
    """Upsert the caller's 1..10 rating. Conclude only, active meeting, caller must have joined."""
    session = ctx.session
    meeting = await _lock(session, meeting_id, share=True)
    if meeting.ended_at is not None:
        raise conflict("meeting_ended", "This meeting has ended; ratings are frozen")
    if meeting.current_section != "conclude":
        raise conflict("not_in_conclude", "Ratings are only accepted in the Conclude section")
    is_attendee = await session.scalar(
        select(
            exists().where(MeetingAttendee.meeting_id == meeting_id, MeetingAttendee.user_id == ctx.user.id)
        )
    )
    if not is_attendee:
        raise conflict("not_attendee", "Join the meeting before rating it")
    team_id = meeting.team_id
    # Audit: the caller's current rating, read under a row lock so re-sending the same rating records nothing.
    old_rating = await session.scalar(
        select(MeetingRating.rating)
        .where(MeetingRating.meeting_id == meeting_id, MeetingRating.user_id == ctx.user.id)
        .with_for_update()
    )
    insert = pg_insert(MeetingRating).values(
        meeting_id=meeting_id, user_id=ctx.user.id, rating=body.rating, updated_at=ctx.now
    )
    await session.execute(
        insert.on_conflict_do_update(
            index_elements=[MeetingRating.meeting_id, MeetingRating.user_id],
            set_={"rating": insert.excluded.rating, "updated_at": insert.excluded.updated_at},
        )
    )
    if old_rating != body.rating:
        await audit.record(
            session,
            team_id=team_id,
            actor_id=ctx.user.id,
            action="meeting.rated",
            entity_type="meeting",
            entity_id=meeting_id,
            at=ctx.now,
            data={"rating": body.rating},
        )
    await session.commit()
    await _notify(ctx, team_id, meeting_id)
    return await get_state(session, meeting_id, hub=ctx.hub, clock=ctx.clock)


# --- recap and summary ----------------------------------------------------------------------------------


async def get_recap(session: AsyncSession, tc: TimeContext, meeting: Meeting) -> sc.MeetingRecap:
    """Live view of what happened during the meeting window `[started_at, ended_at or now]` (inclusive)."""
    window_end = meeting.ended_at or tc.now
    solved = await issues_service.fetch_issues(
        session,
        meeting.team_id,
        Issue.solved_at.is_not(None),
        Issue.solved_at >= meeting.started_at,
        Issue.solved_at <= window_end,
    )
    created = await todos_service.fetch_todos(
        session,
        tc,
        meeting.team_id,
        Todo.created_at >= meeting.started_at,
        Todo.created_at <= window_end,
    )
    return sc.MeetingRecap(
        issues_solved=sorted(solved, key=lambda i: (i.solved_at or window_end, i.id)),
        todos_created=sorted(created, key=lambda t: (t.created_at, t.id)),
    )


def get_summary(meeting: Meeting) -> sc.MeetingSummary:
    """The frozen snapshot written when the meeting ended (never recomputed). Active meetings: 409."""
    if meeting.ended_at is None or meeting.summary is None:
        raise conflict("meeting_active", "The summary is available once the meeting has ended")
    return sc.MeetingSummary.model_validate(meeting.summary)
