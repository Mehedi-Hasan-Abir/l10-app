"""Pure business rules (brief section 2.6). No database, no framework, no I/O, no system clock.

Services, routers and the rules report import from here and never re-implement this math.
"""

from app.rules.issues import IssueFacts, issue_sort_key, order_issues
from app.rules.measurables import Goal, GoalType, MeasurableStatus, goal_label, measurable_status
from app.rules.meetings import (
    AGENDA,
    meeting_rating,
    next_section,
    scorecard_week_for,
    section_duration_s,
    section_timing,
)
from app.rules.rocks import (
    MilestoneFacts,
    RockStatus,
    milestone_past_due,
    rock_progress,
    rock_status,
)
from app.rules.timeutil import (
    COMPANY_TZ,
    TimeContext,
    company_date,
    percent,
    quarter_of,
    require_aware,
    round_half_up,
    scorecard_weeks,
    to_decimal,
    week_bounds_utc,
    week_start_of,
)
from app.rules.todos import Completion, TodoFacts, is_overdue, todo_completion

__all__ = [
    "AGENDA",
    "COMPANY_TZ",
    "Completion",
    "Goal",
    "GoalType",
    "IssueFacts",
    "MeasurableStatus",
    "MilestoneFacts",
    "RockStatus",
    "TimeContext",
    "TodoFacts",
    "company_date",
    "goal_label",
    "is_overdue",
    "issue_sort_key",
    "measurable_status",
    "meeting_rating",
    "milestone_past_due",
    "next_section",
    "order_issues",
    "percent",
    "quarter_of",
    "require_aware",
    "rock_progress",
    "rock_status",
    "round_half_up",
    "scorecard_week_for",
    "scorecard_weeks",
    "section_duration_s",
    "section_timing",
    "to_decimal",
    "todo_completion",
    "week_bounds_utc",
    "week_start_of",
]
