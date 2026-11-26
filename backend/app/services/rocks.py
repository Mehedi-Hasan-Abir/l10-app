"""Rocks: the current quarter's Rocks with computed status/progress, and the bonus "Make it an Issue"."""

from collections import defaultdict
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas as sc
from app.errors import conflict, not_found
from app.models import Milestone, Rock, User
from app.rules import (
    MilestoneFacts,
    RockStatus,
    TimeContext,
    milestone_past_due,
    rock_progress,
    rock_status,
)
from app.services import issues as issues_service
from app.services.common import Ctx, fit_title, user_ref


def _build_rock(rock: Rock, owner: User, milestones: list[Milestone], tc: TimeContext) -> sc.Rock:
    seeded = cast(RockStatus, rock.seeded_status)
    facts = [MilestoneFacts(due_date=m.due_date, done=m.done) for m in milestones]
    return sc.Rock(
        id=rock.id,
        title=rock.title,
        owner=user_ref(owner),
        seeded_status=seeded,
        status=rock_status(seeded, facts, tc.today),
        progress_percent=rock_progress(seeded, facts),
        milestones=[
            sc.Milestone(
                id=m.id,
                title=m.title,
                due_date=m.due_date,
                done=m.done,
                past_due=milestone_past_due(fact, tc.today),
            )
            for m, fact in zip(milestones, facts, strict=True)
        ],
    )


async def _load(
    session: AsyncSession, tc: TimeContext, team_id: str, rock_id: str | None = None
) -> list[sc.Rock]:
    query = (
        select(Rock, User)
        .join(User, User.id == Rock.owner_id)
        .where(Rock.team_id == team_id, Rock.quarter == tc.quarter)
        .order_by(Rock.sort_order, Rock.id)
    )
    if rock_id is not None:
        query = query.where(Rock.id == rock_id)
    rocks = (await session.execute(query)).all()
    by_rock: dict[str, list[Milestone]] = defaultdict(list)
    if rocks:
        milestones = await session.scalars(
            select(Milestone)
            .where(Milestone.rock_id.in_([rock.id for rock, _ in rocks]))
            .order_by(Milestone.sort_order, Milestone.id)
        )
        for milestone in milestones:
            by_rock[milestone.rock_id].append(milestone)
    return [_build_rock(rock, owner, by_rock[rock.id], tc) for rock, owner in rocks]


async def list_rocks(session: AsyncSession, tc: TimeContext, team_id: str) -> sc.RockList:
    """Rocks of the current quarter (company time), in seed order."""
    return sc.RockList(quarter=tc.quarter, rocks=await _load(session, tc, team_id))


async def make_issue(ctx: Ctx, team_id: str, rock_id: str) -> tuple[sc.Issue, bool]:
    """Bonus: Make it an Issue from an off-track Rock (same duplicate-free rule as scorecard cells)."""
    found = await _load(ctx.session, ctx.tc, team_id, rock_id)
    if not found:
        raise not_found("rock_not_found", "Rock not found in this team's current quarter")
    rock = found[0]
    if rock.status != "off_track":
        raise conflict("not_off_track", "Only an off-track Rock can become an Issue")
    return await issues_service.make_issue_from_source(
        ctx,
        team_id=team_id,
        source_type="rock",
        source={"source_rock_id": rock_id},
        title=fit_title(rock.title, " (Rock off track)"),
        resources=("issues",),
    )
