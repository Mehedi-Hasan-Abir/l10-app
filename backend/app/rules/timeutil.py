"""Company time, week math and half-up rounding (pure, no I/O).

Business time is always derived from an injected "now"; nothing here reads the system clock.
Weeks are named by their Monday in company time (America/Chicago) and end at the *exclusive*
instant "next Monday 00:00 local", converted to UTC per week so DST changes are honoured.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from decimal import ROUND_HALF_UP, Decimal, localcontext
from zoneinfo import ZoneInfo

COMPANY_TZ = ZoneInfo("America/Chicago")

_ROUNDING_PRECISION = 60  # digits; far above anything a score or percentage needs


def require_aware(ts: datetime, name: str = "timestamp") -> datetime:
    """Return `ts` unchanged, or raise ValueError if it is a naive datetime."""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware (UTC), got naive datetime {ts!r}")
    return ts


def to_decimal(value: Decimal | int) -> Decimal:
    """Normalize a score or goal value to a finite Decimal. Floats and bools are rejected (TypeError)."""
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise TypeError(f"expected Decimal or int, got {type(value).__name__}")
    result = value if isinstance(value, Decimal) else Decimal(value)
    if not result.is_finite():
        raise ValueError(f"value must be finite, got {result}")
    return result


def company_date(ts: datetime, tz: tzinfo = COMPANY_TZ) -> date:
    """Company-local calendar date of an aware instant (e.g. 2026-11-11T04:59Z is still Nov 10 in Chicago)."""
    return require_aware(ts).astimezone(tz).date()


def week_start_of(d: date) -> date:
    """The Monday of the week containing `d` (Monday and Sunday both map into their own Mon-Sun week)."""
    return d - timedelta(days=d.weekday())


def _local_midnight_utc(d: date, tz: tzinfo) -> datetime:
    return datetime.combine(d, time.min, tzinfo=tz).astimezone(UTC)


def week_bounds_utc(week_start: date, tz: tzinfo = COMPANY_TZ) -> tuple[datetime, datetime]:
    """`[start, end)` of the week in UTC: Monday 00:00 local to the *next* Monday 00:00 local.

    The offset is resolved separately for each boundary, so a week containing a DST change is 167 or 169
    hours long (week of 2026-10-26 ends 2026-11-02T06:00Z, not 05:00Z).
    """
    if week_start.weekday() != 0:
        raise ValueError(f"week_start must be a Monday, got {week_start.isoformat()}")
    return _local_midnight_utc(week_start, tz), _local_midnight_utc(week_start + timedelta(days=7), tz)


def scorecard_weeks(current_week: date, n: int = 13) -> list[date]:
    """The `n` week starts of the scorecard window, oldest first, ending with `current_week`."""
    if n < 0:
        raise ValueError("n must be >= 0")
    return [current_week - timedelta(weeks=n - 1 - i) for i in range(n)]


def quarter_of(d: date) -> str:
    """Calendar quarter label such as "2026-Q4"."""
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def round_half_up(x: Decimal | int, places: int = 0) -> Decimal:
    """Round to `places` decimal places, ties away from zero (12.5 -> 13, 8.25 -> 8.3). Never uses float."""
    value = to_decimal(x)
    with localcontext() as ctx:
        ctx.prec = _ROUNDING_PRECISION
        return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def percent(numerator: int, denominator: int) -> int:
    """`numerator / denominator` as a whole percent, half up (1/8 -> 13, 2/3 -> 67). Denominator > 0."""
    if denominator <= 0:
        raise ValueError("denominator must be > 0")
    if numerator < 0:
        raise ValueError("numerator must be >= 0")
    with localcontext() as ctx:
        ctx.prec = _ROUNDING_PRECISION
        ratio = Decimal(numerator * 100) / Decimal(denominator)
    return int(round_half_up(ratio))


@dataclass(frozen=True)
class TimeContext:
    """Everything derived from one `now`; build once per request via `TimeContext.at`."""

    now: datetime
    today: date
    current_week: date
    last_closed_week: date
    quarter: str

    @classmethod
    def at(cls, now: datetime, tz: tzinfo = COMPANY_TZ) -> TimeContext:
        """Derive today (company-local date), current week, last closed week (current - 7d) and quarter."""
        now_utc = require_aware(now, "now").astimezone(UTC)
        today = company_date(now_utc, tz)
        current_week = week_start_of(today)
        return cls(
            now=now_utc,
            today=today,
            current_week=current_week,
            last_closed_week=current_week - timedelta(days=7),
            quarter=quarter_of(today),
        )
