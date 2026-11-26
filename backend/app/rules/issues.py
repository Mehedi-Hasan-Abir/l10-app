"""Issue ordering: priority 5 first down to 1, oldest first within a priority, no priority last."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.rules.timeutil import require_aware


@dataclass(frozen=True)
class IssueFacts:
    id: str
    priority: int | None
    created_at: datetime


def issue_sort_key(i: IssueFacts) -> tuple[Any, ...]:
    """Sort key `(priority is None, -priority, created_at, id)`.

    Prioritised issues come first (5 down to 1), unprioritised last; within a priority the oldest comes
    first, and the id breaks exact ties so the order is deterministic. Works on any object with
    `id`, `priority` and `created_at` attributes.
    """
    require_aware(i.created_at, "created_at")
    return (i.priority is None, -(i.priority or 0), i.created_at, i.id)


def order_issues[T](issues: Iterable[T], key: Callable[[T], tuple[Any, ...]] | None = None) -> list[T]:
    """Return the issues in Issue order. `key` defaults to `issue_sort_key` (duck-typed on the items)."""
    sort_key: Callable[[Any], tuple[Any, ...]] = key if key is not None else issue_sort_key
    return sorted(issues, key=sort_key)
