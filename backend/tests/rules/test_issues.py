"""Issue order (brief 2.6 "Issue order")."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.rules.issues import IssueFacts, issue_sort_key, order_issues


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def issue(issue_id: str, priority: int | None, created: str) -> IssueFacts:
    return IssueFacts(issue_id, priority, utc(created))


def ids(issues: list[IssueFacts]) -> list[str]:
    return [i.id for i in issues]


# The unsolved team_lead issues in the seed.
SEED_TEAM_LEAD = [
    issue("i1", 5, "2026-10-20T14:00:00Z"),
    issue("i2", 5, "2026-10-14T16:00:00Z"),
    issue("i3", 3, "2026-10-05T15:00:00Z"),
    issue("i4", None, "2026-09-01T15:00:00Z"),
    issue("i5", 4, "2026-11-09T16:00:00Z"),
]


def test_seed_team_lead_order() -> None:
    assert ids(order_issues(SEED_TEAM_LEAD)) == ["i2", "i1", "i5", "i3", "i4"]


def test_seed_order_does_not_depend_on_input_order() -> None:
    rng = random.Random(7)
    for _ in range(20):
        shuffled = SEED_TEAM_LEAD[:]
        rng.shuffle(shuffled)
        assert ids(order_issues(shuffled)) == ["i2", "i1", "i5", "i3", "i4"]


def test_priority_5_comes_before_priority_1_even_when_newer() -> None:
    older_low = issue("low", 1, "2026-01-01T00:00:00Z")
    newer_high = issue("high", 5, "2026-11-01T00:00:00Z")
    assert ids(order_issues([older_low, newer_high])) == ["high", "low"]


def test_priorities_descend_from_5_to_1() -> None:
    same_time = "2026-10-01T12:00:00Z"
    issues = [issue(f"p{p}", p, same_time) for p in (2, 4, 1, 5, 3)]
    assert ids(order_issues(issues)) == ["p5", "p4", "p3", "p2", "p1"]


def test_within_a_priority_the_oldest_comes_first() -> None:
    issues = [
        issue("newest", 3, "2026-10-03T00:00:00Z"),
        issue("oldest", 3, "2026-10-01T00:00:00Z"),
        issue("middle", 3, "2026-10-02T00:00:00Z"),
    ]
    assert ids(order_issues(issues)) == ["oldest", "middle", "newest"]


def test_one_second_older_wins() -> None:
    a = issue("a", 2, "2026-10-01T12:00:01Z")
    b = issue("b", 2, "2026-10-01T12:00:00Z")
    assert ids(order_issues([a, b])) == ["b", "a"]


def test_no_priority_comes_last_even_when_it_is_the_oldest() -> None:
    issues = [issue("none_old", None, "2020-01-01T00:00:00Z"), issue("p1_new", 1, "2026-11-10T00:00:00Z")]
    assert ids(order_issues(issues)) == ["p1_new", "none_old"]


def test_unprioritised_issues_are_ordered_oldest_first_among_themselves() -> None:
    issues = [issue("n2", None, "2026-10-02T00:00:00Z"), issue("n1", None, "2026-10-01T00:00:00Z")]
    assert ids(order_issues(issues)) == ["n1", "n2"]


def test_id_breaks_an_exact_tie_deterministically() -> None:
    same = "2026-10-01T12:00:00Z"
    forward = [issue("a", 3, same), issue("b", 3, same)]
    assert ids(order_issues(forward)) == ["a", "b"]
    assert ids(order_issues(forward[::-1])) == ["a", "b"]


def test_created_at_is_compared_as_an_instant_across_offsets() -> None:
    plus_two = timezone(timedelta(hours=2))
    earlier = IssueFacts("earlier", 3, datetime(2026, 10, 1, 13, 0, tzinfo=plus_two))  # 11:00Z
    later = IssueFacts("later", 3, datetime(2026, 10, 1, 12, 0, tzinfo=UTC))  # 12:00Z
    assert ids(order_issues([later, earlier])) == ["earlier", "later"]


def test_sort_key_shape() -> None:
    created = utc("2026-10-01T12:00:00Z")
    assert issue_sort_key(IssueFacts("x", 5, created)) == (False, -5, created, "x")
    assert issue_sort_key(IssueFacts("y", None, created)) == (True, 0, created, "y")


def test_order_issues_accepts_iterables_and_returns_a_new_list() -> None:
    result = order_issues(i for i in SEED_TEAM_LEAD)
    assert isinstance(result, list)
    assert ids(result)[0] == "i2"
    original = SEED_TEAM_LEAD[:]
    order_issues(original)
    assert original == SEED_TEAM_LEAD


def test_order_issues_works_on_any_object_with_the_issue_fields() -> None:
    @dataclass
    class Row:
        id: str
        priority: int | None
        created_at: datetime
        title: str

    rows = [Row("b", None, utc("2026-01-01T00:00:00Z"), "x"), Row("a", 2, utc("2026-02-01T00:00:00Z"), "y")]
    assert [r.id for r in order_issues(rows)] == ["a", "b"]


def test_order_issues_accepts_a_custom_key() -> None:
    wrapped = [("row2", SEED_TEAM_LEAD[3]), ("row1", SEED_TEAM_LEAD[1])]
    ordered = order_issues(wrapped, key=lambda pair: issue_sort_key(pair[1]))
    assert [name for name, _ in ordered] == ["row1", "row2"]


def test_naive_created_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        issue_sort_key(IssueFacts("x", 1, datetime(2026, 10, 1, 12, 0)))
