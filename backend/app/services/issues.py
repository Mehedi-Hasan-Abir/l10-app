"""Issues: list/order, create, prioritize, solve, and the duplicate-free "make it an Issue" core."""

from collections.abc import Sequence
from typing import Any, Literal

from sqlalchemy import ColumnElement, and_, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app import schemas as sc
from app.errors import conflict, not_found
from app.ids import new_id
from app.models import Issue, Measurable, Rock, Todo, User
from app.rules import IssueFacts, issue_sort_key, order_issues
from app.services import audit
from app.services.common import Ctx, user_ref
from app.services.events import invalidate

SourceType = Literal["measurable", "rock", "todo"]

# (unique index columns, index predicate): exactly the three partial unique indexes of PLAN section 4.
_OPEN_ISSUE_INDEX: dict[SourceType, tuple[tuple[str, ...], str]] = {
    "measurable": (
        ("source_measurable_id", "source_week_start"),
        "solved_at IS NULL AND source_type = 'measurable'",
    ),
    "rock": (("source_rock_id",), "solved_at IS NULL AND source_type = 'rock'"),
    "todo": (("source_todo_id",), "solved_at IS NULL AND source_type = 'todo'"),
}


# --- reads ----------------------------------------------------------------------------------------------


def _source(
    issue: Issue, measurable_name: str | None, rock_title: str | None, todo_title: str | None
) -> sc.MeasurableIssueSource | sc.RockIssueSource | sc.TodoIssueSource | None:
    match issue.source_type:
        case "measurable" if issue.source_measurable_id and issue.source_week_start:
            return sc.MeasurableIssueSource(
                type="measurable",
                measurable_id=issue.source_measurable_id,
                measurable_name=measurable_name or issue.source_measurable_id,
                week_start=issue.source_week_start,
            )
        case "rock" if issue.source_rock_id:
            return sc.RockIssueSource(
                type="rock", rock_id=issue.source_rock_id, rock_title=rock_title or issue.source_rock_id
            )
        case "todo" if issue.source_todo_id:
            return sc.TodoIssueSource(
                type="todo", todo_id=issue.source_todo_id, todo_title=todo_title or issue.source_todo_id
            )
    return None


async def fetch_issues(
    session: AsyncSession, team_id: str, *conditions: ColumnElement[bool]
) -> list[sc.Issue]:
    """Issues of the team matching `conditions`, one query (owner, source names and To-Do count joined in).

    The result is NOT ordered; callers apply Issue order or their own order.
    """
    source_measurable = aliased(Measurable)
    source_rock = aliased(Rock)
    source_todo = aliased(Todo)
    todo_count = (
        select(func.count())
        .select_from(Todo)
        .where(Todo.source_issue_id == Issue.id, Todo.team_id == Issue.team_id)
        .correlate(Issue)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(Issue, User, source_measurable.name, source_rock.title, source_todo.title, todo_count)
        .join(User, User.id == Issue.owner_id)
        .outerjoin(source_measurable, source_measurable.id == Issue.source_measurable_id)
        .outerjoin(source_rock, source_rock.id == Issue.source_rock_id)
        .outerjoin(source_todo, source_todo.id == Issue.source_todo_id)
        .where(Issue.team_id == team_id, *conditions)
    )
    return [
        sc.Issue(
            id=issue.id,
            team_id=issue.team_id,
            title=issue.title,
            owner=user_ref(owner),
            priority=issue.priority,
            created_at=issue.created_at,
            solved_at=issue.solved_at,
            resolution_note=issue.resolution_note,
            source=_source(issue, measurable_name, rock_title, todo_title),
            version=issue.version,
            todo_count=count,
        )
        for issue, owner, measurable_name, rock_title, todo_title, count in rows
    ]


def in_issue_order(issues: Sequence[sc.Issue]) -> list[sc.Issue]:
    """Priority 5..1, oldest first within a priority, unprioritised last (rules.order_issues)."""
    return order_issues(issues, key=lambda i: issue_sort_key(IssueFacts(i.id, i.priority, i.created_at)))


async def get_issue(session: AsyncSession, team_id: str, issue_id: str) -> sc.Issue:
    """One issue of this team; an id from another team is a 404 (nested resources always filter by team)."""
    found = await fetch_issues(session, team_id, Issue.id == issue_id)
    if not found:
        raise not_found("issue_not_found", "Issue not found")
    return found[0]


async def list_issues(
    session: AsyncSession, team_id: str, status: Literal["unsolved", "solved"] = "unsolved"
) -> list[sc.Issue]:
    if status == "solved":
        solved = await fetch_issues(session, team_id, Issue.solved_at.is_not(None))
        return sorted(solved, key=lambda i: (i.solved_at is None, i.solved_at, i.id), reverse=True)
    return in_issue_order(await fetch_issues(session, team_id, Issue.solved_at.is_(None)))


# --- mutations ------------------------------------------------------------------------------------------


async def create_issue(ctx: Ctx, team_id: str, body: sc.IssueCreate) -> sc.Issue:
    issue_id = new_id("i")
    await ctx.session.execute(
        pg_insert(Issue).values(
            id=issue_id,
            team_id=team_id,
            title=body.title,
            owner_id=ctx.user.id,
            priority=body.priority,
            version=1,
            created_at=ctx.now,
        )
    )
    await audit.record(
        ctx.session,
        team_id=team_id,
        actor_id=ctx.user.id,
        action="issue.created",
        entity_type="issue",
        entity_id=issue_id,
        at=ctx.now,
        data={"title": body.title, "source": {"type": "manual"}},
    )
    await ctx.session.commit()
    await invalidate(ctx, team_id, "issues")
    return await get_issue(ctx.session, team_id, issue_id)


async def set_priority(ctx: Ctx, team_id: str, issue_id: str, body: sc.IssueUpdate) -> sc.Issue:
    """Atomic compare-and-set on the issue row, so concurrent edits can never be lost silently."""
    conditions = [Issue.id == issue_id, Issue.team_id == team_id, Issue.solved_at.is_(None)]
    if body.expected_version is not None:
        conditions.append(Issue.version == body.expected_version)
    # Audit: the priority being replaced, read under a row lock so the event's `old` is exact.
    before = (
        await ctx.session.execute(
            select(Issue.priority, Issue.title)
            .where(Issue.id == issue_id, Issue.team_id == team_id)
            .with_for_update()
        )
    ).first()
    updated = await ctx.session.execute(
        update(Issue)
        .where(*conditions)
        .values(priority=body.priority, version=Issue.version + 1)
        .returning(Issue.id)
    )
    if updated.first() is None:
        current = await get_issue(ctx.session, team_id, issue_id)  # 404 if it does not exist in this team
        if current.solved_at is not None:
            raise conflict("issue_solved", "Solved issues can no longer be changed")
        raise conflict(
            "version_conflict",
            "The issue was changed by someone else; reload and try again",
            current=current.model_dump(mode="json"),
        )
    if before is not None and before.priority != body.priority:  # same priority again: nothing changed
        await audit.record(
            ctx.session,
            team_id=team_id,
            actor_id=ctx.user.id,
            action="issue.priority_changed",
            entity_type="issue",
            entity_id=issue_id,
            at=ctx.now,
            data={"title": before.title, "old": before.priority, "new": body.priority},
        )
    await ctx.session.commit()
    await invalidate(ctx, team_id, "issues")
    return await get_issue(ctx.session, team_id, issue_id)


async def solve_issue(ctx: Ctx, team_id: str, issue_id: str, body: sc.IssueSolve) -> sc.Issue:
    solved = await ctx.session.execute(
        update(Issue)
        .where(Issue.id == issue_id, Issue.team_id == team_id, Issue.solved_at.is_(None))
        .values(solved_at=ctx.now, resolution_note=body.resolution_note, version=Issue.version + 1)
        .returning(Issue.id, Issue.title)
    )
    row = solved.first()
    if row is None:
        await get_issue(ctx.session, team_id, issue_id)  # 404 if it does not exist in this team
        raise conflict("issue_solved", "This issue is already solved")
    await audit.record(
        ctx.session,
        team_id=team_id,
        actor_id=ctx.user.id,
        action="issue.solved",
        entity_type="issue",
        entity_id=issue_id,
        at=ctx.now,
        data={"title": row.title, "resolution_note": body.resolution_note},
    )
    await ctx.session.commit()
    await invalidate(ctx, team_id, "issues", "scorecard")
    return await get_issue(ctx.session, team_id, issue_id)


async def make_issue_from_source(
    ctx: Ctx,
    *,
    team_id: str,
    source_type: SourceType,
    source: dict[str, Any],
    title: str,
    resources: Sequence[sc.Resource],
) -> tuple[sc.Issue, bool]:
    """Create the unsolved Issue for a source, or return the one that already exists. Race-free.

    `INSERT ... ON CONFLICT DO NOTHING` targets the partial unique index "one unsolved issue per source", so
    two simultaneous callers cannot both insert. The loser's statement waits for the winner's commit, inserts
    nothing, and then selects the winner's row. There is never a check-then-insert.
    Returns `(issue, created)`.
    """
    index_columns, index_predicate = _OPEN_ISSUE_INDEX[source_type]
    issue_id: str | None = None
    created = False
    for _ in range(3):
        inserted = await ctx.session.execute(
            pg_insert(Issue)
            .values(
                id=new_id("i"),
                team_id=team_id,
                title=title,
                owner_id=ctx.user.id,
                priority=None,
                version=1,
                created_at=ctx.now,
                source_type=source_type,
                **source,
            )
            .on_conflict_do_nothing(
                index_elements=[getattr(Issue, name) for name in index_columns],
                index_where=text(index_predicate),
            )
            .returning(Issue.id)
        )
        row = inserted.first()
        if row is not None:
            issue_id, created = row.id, True
            break
        existing = await ctx.session.scalar(
            select(Issue.id).where(
                Issue.team_id == team_id,
                Issue.source_type == source_type,
                Issue.solved_at.is_(None),
                and_(*(getattr(Issue, name) == value for name, value in source.items())),
            )
        )
        if existing is not None:
            issue_id = existing
            break
        # The conflicting issue was solved between the two statements: the slot is free again, so retry.
    if issue_id is None:  # pragma: no cover - would need three solves in a row within microseconds
        raise conflict("issue_busy", "Could not create the issue, please retry")
    if created:  # an existing unsolved issue returned to the caller is not a change
        await audit.record(
            ctx.session,
            team_id=team_id,
            actor_id=ctx.user.id,
            action="issue.created",
            entity_type="issue",
            entity_id=issue_id,
            at=ctx.now,
            data={
                "title": title,
                "source": {"type": source_type, **{k.removeprefix("source_"): v for k, v in source.items()}},
            },
        )
    await ctx.session.commit()
    if created:
        await invalidate(ctx, team_id, *resources)
    return await get_issue(ctx.session, team_id, issue_id), created
