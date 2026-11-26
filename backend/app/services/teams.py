"""Teams: the switcher list and the team detail (members, permissions, business clock, live meeting)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas as sc
from app.auth import TeamContext, eligible_owner_ids
from app.models import Meeting, Membership, Team, User
from app.rules import TimeContext
from app.services.common import user_ref


async def list_teams(session: AsyncSession, user: User) -> list[sc.TeamRef]:
    """Teams the user may open: their memberships, or every team of the company for an admin."""
    query = select(Team.id, Team.name).where(Team.company_id == user.company_id).order_by(Team.name, Team.id)
    if user.role != "admin":
        query = query.join(Membership, Membership.team_id == Team.id).where(Membership.user_id == user.id)
    return [sc.TeamRef(id=row.id, name=row.name) for row in await session.execute(query)]


def clock_info(tc: TimeContext) -> sc.ClockInfo:
    return sc.ClockInfo(
        now=tc.now,
        today=tc.today,
        current_week=tc.current_week,
        last_closed_week=tc.last_closed_week,
        quarter=tc.quarter,
    )


async def get_team_detail(session: AsyncSession, tc: TimeContext, access: TeamContext) -> sc.TeamDetail:
    team_id = access.team.id
    members = await session.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.team_id == team_id)
        .order_by(User.name, User.id)
    )
    owner_ids = await eligible_owner_ids(session, team_id)
    owners = await session.scalars(select(User).where(User.id.in_(owner_ids)).order_by(User.name, User.id))

    active = (
        await session.execute(
            select(Meeting, User)
            .join(User, User.id == Meeting.facilitator_id)
            .where(Meeting.team_id == team_id, Meeting.ended_at.is_(None))
        )
    ).first()
    active_ref = None
    if active is not None:
        meeting, facilitator = active
        active_ref = sc.ActiveMeetingRef(
            id=meeting.id,
            facilitator=user_ref(facilitator),
            current_section=meeting.current_section,  # type: ignore[arg-type]
        )

    return sc.TeamDetail(
        id=access.team.id,
        name=access.team.name,
        members=[user_ref(u) for u in members],
        eligible_owners=[user_ref(u) for u in owners],
        permissions=sc.Permissions(
            can_write=access.can_write, can_facilitate=access.can_write, can_rate=access.can_write
        ),
        clock=clock_info(tc),
        active_meeting=active_ref,
    )
