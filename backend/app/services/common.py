"""Shared service plumbing: the per-request context and small conversion helpers.

Services hold every query and mutation. Read functions take `(session, tc, ...)` so the rules report can call
them with a frozen clock; mutation functions take a `Ctx`, commit, then publish realtime events.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import schemas as sc
from app.clock import Clock
from app.models import User
from app.realtime.hub import Hub
from app.rules import Goal as RuleGoal
from app.rules import GoalType, TimeContext

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class Ctx:
    """Everything a mutation needs. `tc` comes from one clock reading taken at the start of the request."""

    session: AsyncSession
    user: User
    tc: TimeContext
    hub: Hub
    session_factory: async_sessionmaker[AsyncSession]
    clock: Clock

    @property
    def now(self) -> datetime:
        return self.tc.now


def week_label(week_start: date) -> str:
    """`Nov 2` (locale independent), used in generated Issue titles."""
    return f"{_MONTHS[week_start.month - 1]} {week_start.day}"


def user_ref(user: User) -> sc.UserRef:
    return sc.UserRef.model_validate(user)


def to_float(value: Decimal | None) -> float | None:
    """Decimals become JSON numbers only at the API edge."""
    return None if value is None else float(value)


def rule_goal(
    goal_type: str, value: Decimal | None, minimum: Decimal | None, maximum: Decimal | None
) -> RuleGoal:
    return RuleGoal(type=cast(GoalType, goal_type), value=value, min=minimum, max=maximum)


def fit_title(base: str, suffix: str = "", limit: int = 200) -> str:
    """`base + suffix` within the 200 char title limit (the base is shortened with an ellipsis if needed)."""
    room = limit - len(suffix)
    if len(base) > room:
        base = base[: max(room - 1, 0)].rstrip() + "…"
    return base + suffix
