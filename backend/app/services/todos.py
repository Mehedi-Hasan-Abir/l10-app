"""To-Dos: list with overdue flags and last-closed-week completion, done toggle, create from an Issue."""

from collections.abc import Iterable
from datetime import date, timedelta

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas as sc
from app.auth import eligible_owner_ids
from app.errors import conflict, not_found, unprocessable
from app.ids import new_id
from app.models import Issue, Todo, User
from app.rules import TimeContext, TodoFacts, is_overdue, scorecard_weeks, todo_completion
from app.services import audit
from app.services import issues as issues_service
from app.services.common import Ctx, fit_title, user_ref
from app.services.events import invalidate

# --- reads ----------------------------------------------------------------------------------------------


async def fetch_todos(
    session: AsyncSession, tc: TimeContext, team_id: str, *conditions: ColumnElement[bool]
) -> list[sc.Todo]:
    """To-Dos of the team matching `conditions` (unordered), with owner and source Issue joined in."""
    rows = await session.execute(
        select(Todo, User, Issue.title)
        .join(User, User.id == Todo.owner_id)
        .outerjoin(Issue, (Issue.id == Todo.source_issue_id) & (Issue.team_id == Todo.team_id))
        .where(Todo.team_id == team_id, *conditions)
    )
    return [
        sc.Todo(
            id=todo.id,
            team_id=todo.team_id,
            title=todo.title,
            owner=user_ref(owner),
            due_date=todo.due_date,
            done_at=todo.done_at,
            done=todo.done_at is not None,
            overdue=is_overdue(todo.due_date, todo.done_at, tc.today),
            source_issue=(
                sc.IssueRef(id=todo.source_issue_id, title=issue_title)
                if todo.source_issue_id is not None and issue_title is not None
                else None
            ),
            created_at=todo.created_at,
        )
        for todo, owner, issue_title in rows
    ]


def in_todo_order(todos: Iterable[sc.Todo]) -> list[sc.Todo]:
    """Open first (due date ascending), then done (done_at descending); the id breaks ties."""
    items = list(todos)
    open_todos = sorted((t for t in items if t.done_at is None), key=lambda t: (t.due_date, t.id))
    done_todos = sorted(
        (t for t in items if t.done_at is not None),
        key=lambda t: (t.done_at, t.id),  # type: ignore[arg-type,return-value]  # done_at is set here
        reverse=True,
    )
    return open_todos + done_todos


async def get_todo(session: AsyncSession, tc: TimeContext, team_id: str, todo_id: str) -> sc.Todo:
    found = await fetch_todos(session, tc, team_id, Todo.id == todo_id)
    if not found:
        raise not_found("todo_not_found", "To-Do not found")
    return found[0]


async def completion_by_week(
    session: AsyncSession, team_id: str, weeks: Iterable[date]
) -> dict[date, sc.Completion | None]:
    """Completion rate for each week (None when nothing was due); the report and the API share this."""
    facts = [
        TodoFacts(id=row.id, due_date=row.due_date, done_at=row.done_at)
        for row in await session.execute(
            select(Todo.id, Todo.due_date, Todo.done_at).where(Todo.team_id == team_id)
        )
    ]
    result: dict[date, sc.Completion | None] = {}
    for week in weeks:
        rate = todo_completion(facts, week)
        result[week] = (
            None
            if rate is None
            else sc.Completion(
                week_start=week,
                due=rate.due,
                done=rate.done,
                rate_percent=rate.rate_percent,
                meets_target=rate.meets_target,
            )
        )
    return result


async def completion_window(
    session: AsyncSession, tc: TimeContext, team_id: str
) -> dict[date, sc.Completion | None]:
    """`completion_by_week` over the 13-week scorecard window (what the rules report prints)."""
    return await completion_by_week(session, team_id, scorecard_weeks(tc.current_week))


async def list_todos(session: AsyncSession, tc: TimeContext, team_id: str) -> sc.TodoList:
    todos = in_todo_order(await fetch_todos(session, tc, team_id))
    completion = (await completion_by_week(session, team_id, [tc.last_closed_week]))[tc.last_closed_week]
    return sc.TodoList(todos=todos, last_closed_week=tc.last_closed_week, completion=completion)


# --- mutations ------------------------------------------------------------------------------------------


async def set_done(ctx: Ctx, team_id: str, todo_id: str, body: sc.TodoUpdate) -> sc.Todo:
    """Mark done (`done_at = now`, kept if already done) or undo (`done_at = null`). Idempotent."""
    # COALESCE keeps the original done time when the To-Do is already done.
    new_done_at = func.coalesce(Todo.done_at, ctx.now) if body.done else None
    # Audit: whether the To-Do is done right now, read under a row lock so only a real flip is recorded.
    before = (
        await ctx.session.execute(
            select(Todo.done_at, Todo.title)
            .where(Todo.id == todo_id, Todo.team_id == team_id)
            .with_for_update()
        )
    ).first()
    updated = await ctx.session.execute(
        update(Todo)
        .where(Todo.id == todo_id, Todo.team_id == team_id)
        .values(done_at=new_done_at)
        .returning(Todo.id)
    )
    if updated.first() is None:
        raise not_found("todo_not_found", "To-Do not found")
    if before is not None and (before.done_at is None) == body.done:  # an idempotent repeat changes nothing
        await audit.record(
            ctx.session,
            team_id=team_id,
            actor_id=ctx.user.id,
            action="todo.done" if body.done else "todo.reopened",
            entity_type="todo",
            entity_id=todo_id,
            at=ctx.now,
            data={"title": before.title},
        )
    await ctx.session.commit()
    await invalidate(ctx, team_id, "todos")
    return await get_todo(ctx.session, ctx.tc, team_id, todo_id)


async def create_from_issue(ctx: Ctx, team_id: str, issue_id: str, body: sc.IssueTodoCreate) -> sc.Todo:
    """New To-Do linked to an Issue: due in 7 days (company calendar), created now."""
    await issues_service.get_issue(ctx.session, team_id, issue_id)  # 404 for another team's issue
    if body.owner_id not in await eligible_owner_ids(ctx.session, team_id):
        raise unprocessable(
            "owner_not_eligible", "The owner must be an Admin or Team Member of this team", field="owner_id"
        )
    todo_id = new_id("t")
    due_date = ctx.tc.today + timedelta(days=7)
    await ctx.session.execute(
        pg_insert(Todo).values(
            id=todo_id,
            team_id=team_id,
            title=body.title,
            owner_id=body.owner_id,
            due_date=due_date,
            done_at=None,
            source_issue_id=issue_id,
            created_at=ctx.now,
        )
    )
    await audit.record(
        ctx.session,
        team_id=team_id,
        actor_id=ctx.user.id,
        action="todo.created",
        entity_type="todo",
        entity_id=todo_id,
        at=ctx.now,
        data={
            "title": body.title,
            "source_issue_id": issue_id,
            "owner_id": body.owner_id,
            "due_date": due_date,
        },
    )
    await ctx.session.commit()
    await invalidate(ctx, team_id, "todos", "issues")
    return await get_todo(ctx.session, ctx.tc, team_id, todo_id)


async def make_issue(ctx: Ctx, team_id: str, todo_id: str) -> tuple[sc.Issue, bool]:
    """Bonus: Make it an Issue from an overdue To-Do (same duplicate-free rule as scorecard cells)."""
    todo = await get_todo(ctx.session, ctx.tc, team_id, todo_id)
    if not todo.overdue:
        raise conflict("not_overdue", "Only an overdue To-Do can become an Issue")
    return await issues_service.make_issue_from_source(
        ctx,
        team_id=team_id,
        source_type="todo",
        source={"source_todo_id": todo_id},
        title=fit_title(todo.title, " (To-Do overdue)"),
        resources=("issues",),
    )
