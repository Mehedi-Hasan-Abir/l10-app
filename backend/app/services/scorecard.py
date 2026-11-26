"""Scorecard: 13-week grid, score upsert/clear with optimistic locking, and "Make it an Issue" for a cell."""

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas as sc
from app.errors import conflict, not_found, unprocessable
from app.models import Issue, Measurable, Score, User
from app.rules import TimeContext, goal_label, measurable_status, scorecard_weeks
from app.services import audit
from app.services import issues as issues_service
from app.services.common import Ctx, fit_title, rule_goal, to_float, user_ref, week_label
from app.services.events import invalidate


def _cell(
    measurable: Measurable,
    value: Decimal | None,
    version: int | None,
    week: date,
    tc: TimeContext,
    open_issue_id: str | None,
) -> sc.ScoreCell:
    goal = rule_goal(measurable.goal_type, measurable.goal_value, measurable.goal_min, measurable.goal_max)
    return sc.ScoreCell(
        week_start=week,
        value=to_float(value),
        status=measurable_status(goal, value, week, tc.current_week),
        version=version,
        open_issue_id=open_issue_id,
    )


async def get_scorecard(session: AsyncSession, tc: TimeContext, team_id: str) -> sc.Scorecard:
    weeks = scorecard_weeks(tc.current_week)
    rows = (
        await session.execute(
            select(Measurable, User)
            .join(User, User.id == Measurable.owner_id)
            .where(Measurable.team_id == team_id)
            .order_by(Measurable.sort_order, Measurable.id)
        )
    ).all()
    measurable_ids = [m.id for m, _ in rows]

    scores: dict[tuple[str, date], Score] = {}
    if measurable_ids:
        for score in await session.scalars(
            select(Score).where(Score.measurable_id.in_(measurable_ids), Score.week_start.in_(weeks))
        ):
            scores[(score.measurable_id, score.week_start)] = score
    open_issues = {
        (row.source_measurable_id, row.source_week_start): row.id
        for row in await session.execute(
            select(Issue.id, Issue.source_measurable_id, Issue.source_week_start).where(
                Issue.team_id == team_id, Issue.source_type == "measurable", Issue.solved_at.is_(None)
            )
        )
    }

    measurables = []
    for measurable, owner in rows:
        goal = rule_goal(
            measurable.goal_type, measurable.goal_value, measurable.goal_min, measurable.goal_max
        )
        cells = []
        for week in weeks:
            score = scores.get((measurable.id, week))
            cells.append(
                _cell(
                    measurable,
                    None if score is None else score.value,
                    None if score is None else score.version,
                    week,
                    tc,
                    open_issues.get((measurable.id, week)),
                )
            )
        measurables.append(
            sc.ScorecardMeasurable(
                id=measurable.id,
                name=measurable.name,
                unit=measurable.unit,
                owner=user_ref(owner),
                goal=sc.Goal(
                    type=goal.type, value=to_float(goal.value), min=to_float(goal.min), max=to_float(goal.max)
                ),
                goal_label=goal_label(goal, measurable.unit),
                cells=cells,
            )
        )
    return sc.Scorecard(
        weeks=weeks,
        current_week=tc.current_week,
        last_closed_week=tc.last_closed_week,
        measurables=measurables,
    )


# --- single cell ----------------------------------------------------------------------------------------


def validate_week(week_start: date, tc: TimeContext) -> None:
    """The week must be a Monday inside the 13-week scorecard window, else 422."""
    if week_start.weekday() != 0:
        raise unprocessable("week_not_monday", "week_start must be a Monday", field="week_start")
    if week_start not in scorecard_weeks(tc.current_week):
        raise unprocessable(
            "week_out_of_window",
            "week_start must be one of the 13 weeks ending with the current week",
            field="week_start",
        )


async def _measurable(session: AsyncSession, team_id: str, measurable_id: str) -> Measurable:
    measurable = (
        await session.execute(
            select(Measurable).where(Measurable.id == measurable_id, Measurable.team_id == team_id)
        )
    ).scalar_one_or_none()
    if measurable is None:
        raise not_found("measurable_not_found", "Measurable not found")
    return measurable


async def _current_cell(
    session: AsyncSession, tc: TimeContext, measurable: Measurable, week: date
) -> sc.ScoreCell:
    score = (
        await session.execute(
            select(Score).where(Score.measurable_id == measurable.id, Score.week_start == week)
        )
    ).scalar_one_or_none()
    open_issue_id = await session.scalar(
        select(Issue.id).where(
            Issue.team_id == measurable.team_id,
            Issue.source_type == "measurable",
            Issue.source_measurable_id == measurable.id,
            Issue.source_week_start == week,
            Issue.solved_at.is_(None),
        )
    )
    return _cell(
        measurable,
        None if score is None else score.value,
        None if score is None else score.version,
        week,
        tc,
        open_issue_id,
    )


async def put_score(
    ctx: Ctx, team_id: str, measurable_id: str, week_start: date, body: sc.ScoreUpdate
) -> sc.ScoreCell:
    """Set or clear one cell. Last save wins unless `expected_version` is sent (then compare-and-set).

    Every variant is a single atomic statement, so concurrent editors can never produce a torn update.
    """
    validate_week(week_start, ctx.tc)
    measurable = await _measurable(ctx.session, team_id, measurable_id)
    session = ctx.session
    expected = body.expected_version
    cell_filter = (Score.measurable_id == measurable_id, Score.week_start == week_start)
    # Audit: the value being replaced, read under a row lock so the event's `old` is exact.
    old_value = await session.scalar(select(Score.value).where(*cell_filter).with_for_update())

    if body.value is None:
        stmt = delete(Score).where(*cell_filter)
        if expected is not None:
            stmt = stmt.where(Score.version == expected)
        applied = (await session.execute(stmt.returning(Score.version))).first() is not None
        # Clearing an already empty cell is a no-op, unless the client expected a specific version.
        ok = applied or expected is None
    elif expected is None:
        insert = pg_insert(Score).values(
            measurable_id=measurable_id,
            week_start=week_start,
            value=body.value,
            version=1,
            updated_at=ctx.now,
            updated_by=ctx.user.id,
        )
        await session.execute(
            insert.on_conflict_do_update(
                index_elements=[Score.measurable_id, Score.week_start],
                set_={
                    "value": insert.excluded.value,
                    "version": Score.version + 1,
                    "updated_at": insert.excluded.updated_at,
                    "updated_by": insert.excluded.updated_by,
                },
            )
        )
        ok = True
    else:
        applied_row = await session.execute(
            update(Score)
            .where(*cell_filter, Score.version == expected)
            .values(value=body.value, version=Score.version + 1, updated_at=ctx.now, updated_by=ctx.user.id)
            .returning(Score.version)
        )
        ok = applied_row.first() is not None

    if not ok:
        current = await _current_cell(session, ctx.tc, measurable, week_start)
        raise conflict(
            "version_conflict",
            "This score was changed by someone else; reload and try again",
            current=current.model_dump(mode="json"),
        )
    if old_value != body.value:  # same value again, or clearing an empty cell: nothing changed
        await audit.record(
            session,
            team_id=team_id,
            actor_id=ctx.user.id,
            action="score.set" if body.value is not None else "score.cleared",
            entity_type="score",
            entity_id=f"{measurable_id}:{week_start}",
            at=ctx.now,
            data={
                "measurable_id": measurable_id,
                "measurable_name": measurable.name,
                "week_start": week_start,
                "old": old_value,
                "new": body.value,
            },
        )
    await session.commit()
    await invalidate(ctx, team_id, "scorecard")
    return await _current_cell(session, ctx.tc, measurable, week_start)


async def make_issue(ctx: Ctx, team_id: str, measurable_id: str, week_start: date) -> tuple[sc.Issue, bool]:
    """Make it an Issue from an off-track cell: returns the unsolved Issue for that cell (created or not)."""
    validate_week(week_start, ctx.tc)
    measurable = await _measurable(ctx.session, team_id, measurable_id)
    cell = await _current_cell(ctx.session, ctx.tc, measurable, week_start)
    if cell.status != "off_track":
        raise conflict("not_off_track", "Only an off-track score can become an Issue", status=cell.status)
    return await issues_service.make_issue_from_source(
        ctx,
        team_id=team_id,
        source_type="measurable",
        source={"source_measurable_id": measurable_id, "source_week_start": week_start},
        title=fit_title(f"{measurable.name}, week of {week_label(week_start)}"),
        resources=("scorecard", "issues"),
    )
