"""Pydantic request/response models: the API contract in code (PLAN section 6).

Class names here become OpenAPI component names that the frontend imports, so they are fixed.

Conventions:
* Requests forbid unknown fields, strip whitespace and bound every string and number.
* Response fields have no defaults, so the generated TypeScript types are required-but-nullable.
* Timestamps serialize as ISO-8601 UTC with a `Z`; dates as `YYYY-MM-DD`.
* Business math is done with `Decimal` in `app.rules`; JSON floats are produced only here, at the edge.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    WithJsonSchema,
)

Role = Literal["admin", "team_member", "observer"]
Status = Literal["on_track", "off_track", "pending"]
SectionKey = Literal["scorecard", "ids", "conclude"]
GoalType = Literal["at_least", "at_most", "between", "exactly"]
RockStatus = Literal["on_track", "off_track", "complete"]
MeetingStatus = Literal["active", "ended"]
Resource = Literal["scorecard", "issues", "todos", "rocks", "meetings"]
AuditAction = Literal[
    "score.set",
    "score.cleared",
    "issue.created",
    "issue.priority_changed",
    "issue.solved",
    "todo.created",
    "todo.done",
    "todo.reopened",
    "meeting.started",
    "meeting.advanced",
    "meeting.rated",
    "meeting.ended",
]
AuditEntityType = Literal["score", "issue", "todo", "meeting"]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(_as_utc)]
"""An aware datetime normalized to UTC (serializes as `...Z`)."""


# --- request building blocks ----------------------------------------------------------------------------

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
EntityId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Priority = Annotated[int, Field(strict=True, ge=1, le=5, description="5 = most important, 1 = least")]
RatingValue = Annotated[int, Field(strict=True, ge=1, le=10)]
ExpectedVersion = Annotated[
    int,
    Field(
        strict=True,
        ge=1,
        description="Optimistic lock: the `version` last seen; a stale one gives 409 `version_conflict`.",
    ),
]

_SCORE_LIMIT = Decimal(10) ** 12


def _score_input(value: Any) -> Any:
    """JSON numbers only. A float becomes the Decimal of its shortest repr (8.25 -> Decimal('8.25'))."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise ValueError("score must be a JSON number")
    if isinstance(value, float):
        return Decimal(repr(value))
    return value


ScoreValue = Annotated[
    Decimal,
    BeforeValidator(_score_input),
    Field(allow_inf_nan=False, gt=-_SCORE_LIMIT, lt=_SCORE_LIMIT),
    WithJsonSchema(
        {
            "type": "number",
            "exclusiveMinimum": -(10**12),
            "exclusiveMaximum": 10**12,
            "description": "Finite number with absolute value below 1e12.",
        }
    ),
]


class RequestModel(BaseModel):
    """Base of every request body: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


# --- shared refs ----------------------------------------------------------------------------------------


class UserRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    role: Role


class TeamRef(BaseModel):
    id: str
    name: str


class Permissions(BaseModel):
    """What the caller may do in this team (UX hint only: the server enforces every rule)."""

    can_write: bool
    can_facilitate: bool
    can_rate: bool


class ClockInfo(BaseModel):
    """Business time derived from the injected clock (`APP_NOW`), in company time zone terms."""

    now: UtcDateTime
    today: date
    current_week: date
    last_closed_week: date
    quarter: str


class ActiveMeetingRef(BaseModel):
    id: str
    facilitator: UserRef
    current_section: SectionKey


class TeamDetail(BaseModel):
    id: str
    name: str
    members: list[UserRef]
    eligible_owners: list[UserRef]
    permissions: Permissions
    clock: ClockInfo
    active_meeting: ActiveMeetingRef | None


# --- scorecard ------------------------------------------------------------------------------------------


class Goal(BaseModel):
    type: GoalType
    value: float | None
    min: float | None
    max: float | None


class ScoreCell(BaseModel):
    week_start: date
    value: float | None
    status: Status
    version: int | None = Field(description="Null while the cell has no score")
    open_issue_id: str | None = Field(description="The unsolved Issue created from this cell, if any")


class ScorecardMeasurable(BaseModel):
    id: str
    name: str
    unit: str
    owner: UserRef
    goal: Goal
    goal_label: str
    cells: list[ScoreCell] = Field(description="Aligned with `Scorecard.weeks`")


class Scorecard(BaseModel):
    weeks: list[date] = Field(description="The 13 weeks ending with the current week, oldest first")
    current_week: date
    last_closed_week: date
    measurables: list[ScorecardMeasurable]


class ScoreUpdate(RequestModel):
    value: ScoreValue | None = Field(description="The score; null clears it")
    expected_version: ExpectedVersion | None = None


# --- issues ---------------------------------------------------------------------------------------------


class MeasurableIssueSource(BaseModel):
    type: Literal["measurable"]
    measurable_id: str
    measurable_name: str
    week_start: date


class RockIssueSource(BaseModel):
    type: Literal["rock"]
    rock_id: str
    rock_title: str


class TodoIssueSource(BaseModel):
    type: Literal["todo"]
    todo_id: str
    todo_title: str


IssueSource = Annotated[
    MeasurableIssueSource | RockIssueSource | TodoIssueSource, Field(discriminator="type")
]


class Issue(BaseModel):
    id: str
    team_id: str
    title: str
    owner: UserRef
    priority: int | None
    created_at: UtcDateTime
    solved_at: UtcDateTime | None
    resolution_note: str | None
    source: IssueSource | None
    version: int
    todo_count: int = Field(description="To-Dos created from this Issue")


class IssueCreate(RequestModel):
    title: Title
    priority: Priority | None = None


class IssueUpdate(RequestModel):
    priority: Priority | None = Field(description="1..5, or null to clear the priority")
    expected_version: ExpectedVersion | None = None


class IssueSolve(RequestModel):
    resolution_note: Note


class MakeIssueResult(BaseModel):
    issue: Issue
    created: bool = Field(description="False when an unsolved Issue for the same source already existed")


class IssueTodoCreate(RequestModel):
    title: Title
    owner_id: EntityId


# --- to-dos ---------------------------------------------------------------------------------------------


class IssueRef(BaseModel):
    id: str
    title: str


class Todo(BaseModel):
    id: str
    team_id: str
    title: str
    owner: UserRef
    due_date: date
    done_at: UtcDateTime | None
    done: bool
    overdue: bool
    source_issue: IssueRef | None
    created_at: UtcDateTime


class TodoUpdate(RequestModel):
    done: StrictBool


class Completion(BaseModel):
    week_start: date
    due: int
    done: int
    rate_percent: int
    meets_target: bool


class TodoList(BaseModel):
    todos: list[Todo] = Field(description="Open first (due date ascending), then done (done_at descending)")
    last_closed_week: date
    completion: Completion | None = Field(description="Null when nothing was due in the last closed week")


# --- rocks ----------------------------------------------------------------------------------------------


class Milestone(BaseModel):
    id: str
    title: str
    due_date: date
    done: bool
    past_due: bool


class Rock(BaseModel):
    id: str
    title: str
    owner: UserRef
    seeded_status: RockStatus
    status: RockStatus
    progress_percent: int
    milestones: list[Milestone]


class RockList(BaseModel):
    quarter: str
    rocks: list[Rock]


# --- meetings -------------------------------------------------------------------------------------------


class AgendaItem(BaseModel):
    key: SectionKey
    title: str
    duration_seconds: int


class Attendee(UserRef):
    joined_at: UtcDateTime
    online: bool = Field(description="Has an open WebSocket to this team right now")


class RatingEntry(BaseModel):
    user_id: str
    name: str
    rating: int


class MeetingState(BaseModel):
    """Everything a client needs to render a meeting. Postgres owns it; the WebSocket only notifies."""

    id: str
    team_id: str
    status: MeetingStatus
    facilitator: UserRef
    started_at: UtcDateTime
    ended_at: UtcDateTime | None
    scorecard_week: date
    agenda: list[AgendaItem]
    current_section: SectionKey
    section_started_at: UtcDateTime
    server_now: UtcDateTime = Field(
        description="Clock time at serialization; elapsed = (now + offset) - section_started_at"
    )
    attendees: list[Attendee]
    ratings: list[RatingEntry]
    average_rating: float | None


class RatingUpdate(RequestModel):
    rating: RatingValue


class MeetingAdvance(RequestModel):
    """Optional body of `POST /meetings/{id}/advance`: the section the facilitator was looking at."""

    from_section: SectionKey | None = Field(
        default=None,
        description="If set and the meeting is no longer in this section, the call fails with 409 "
        "`stale_section` instead of skipping a section (double-click protection).",
    )


class MeetingRecap(BaseModel):
    issues_solved: list[Issue]
    todos_created: list[Todo]


class MeasurableStatusEntry(BaseModel):
    measurable_id: str
    name: str
    value: float | None
    status: Status


class SummaryIssue(BaseModel):
    id: str
    title: str


class SummaryTodo(BaseModel):
    id: str
    title: str
    owner: UserRef
    due_date: date


class MeetingSummary(BaseModel):
    """Frozen snapshot written when the meeting ends; later edits never change it."""

    meeting_id: str
    team_id: str
    scorecard_week: date
    started_at: UtcDateTime
    ended_at: UtcDateTime
    measurable_status: list[MeasurableStatusEntry]
    issues_solved: list[SummaryIssue]
    todos_created: list[SummaryTodo]
    attendees: list[UserRef]
    rating: float | None


# --- audit trail ----------------------------------------------------------------------------------------


class AuditEvent(BaseModel):
    """One business mutation, recorded in the same transaction as the change. Append-only."""

    id: int = Field(description="Strictly increasing; newest events have the highest id")
    team_id: str
    actor: UserRef = Field(description="Who made the change")
    action: AuditAction
    entity_type: AuditEntityType
    entity_id: str = Field(
        description="Id of the changed entity; a score is `{measurable_id}:{week_start}` (`m1:2026-11-02`)"
    )
    at: UtcDateTime = Field(description="Business time (the injected clock) of the change")
    data: dict[str, Any] = Field(
        description=(
            "Action-specific details. score.set/score.cleared: measurable_id, measurable_name, week_start, "
            "old, new (null when cleared). issue.created: title, source "
            "{type: manual|measurable|rock|todo, ...}. "
            "issue.priority_changed: title, old, new. issue.solved: title, resolution_note. "
            "todo.created: title, source_issue_id, owner_id, due_date. todo.done/todo.reopened: title. "
            "meeting.advanced: from, to. meeting.rated: rating. meeting.started/meeting.ended: empty."
        )
    )


class AuditPage(BaseModel):
    events: list[AuditEvent] = Field(description="Newest first")
    next_before_id: int | None = Field(
        description="Pass as `before_id` to fetch the next (older) page; null when this is the last page"
    )


# --- errors (documentation only; raised through app.errors) -----------------------------------------------


class ErrorDetail(BaseModel):
    """The `detail` object of an error response. Which extra keys are present depends on `code`."""

    model_config = ConfigDict(extra="allow")

    code: str = Field(description="Stable snake_case error code")
    message: str = Field(description="Human readable message")
    meeting_id: str | None = Field(
        default=None, description="`meeting_already_active`: the id of the meeting that is already running"
    )
    current: ScoreCell | Issue | None = Field(
        default=None,
        description="`version_conflict`: the current ScoreCell (scores) or Issue (priority)",
    )
    status: Status | None = Field(default=None, description="`not_off_track`: the cell's actual status")
    field: str | None = Field(default=None, description="422 business errors: the offending field")
    current_section: SectionKey | None = Field(
        default=None, description="`stale_section`: the section the meeting is actually in"
    )


class ErrorBody(BaseModel):
    detail: ErrorDetail
