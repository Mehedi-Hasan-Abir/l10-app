"""Rock status and progress (brief 2.6 "Rock status" and "Rock progress")."""

from __future__ import annotations

from datetime import date

import pytest

from app.rules.rocks import MilestoneFacts, milestone_past_due, rock_progress, rock_status

TODAY = date(2026, 11, 11)  # the default clock's company-local date


def ms(due: str, done: bool = False) -> MilestoneFacts:
    return MilestoneFacts(date.fromisoformat(due), done)


# Seed r1 (Launch partner portal): ms1 done, ms2 due today, ms3 due later.
R1 = [ms("2026-10-15", True), ms("2026-11-11"), ms("2026-12-01")]
# Seed r2 (Hire VP of Sales): 1 of 8 done, ms5 due yesterday.
R2 = [
    ms("2026-10-10", True),
    ms("2026-11-10"),
    ms("2026-11-20"),
    ms("2026-11-27"),
    ms("2026-12-04"),
    ms("2026-12-08"),
    ms("2026-12-15"),
    ms("2026-12-20"),
]


# --- milestone_past_due ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("milestone", "expected"),
    [
        (ms("2026-11-10"), True),  # due yesterday, unfinished
        (ms("2026-11-11"), False),  # due today is not past due
        (ms("2026-11-12"), False),
        (ms("2026-10-15", True), False),  # done milestones are never past due
        (ms("2026-11-10", True), False),
    ],
)
def test_milestone_is_past_due_only_when_unfinished_and_due_before_today(
    milestone: MilestoneFacts, expected: bool
) -> None:
    assert milestone_past_due(milestone, TODAY) is expected


# --- rock_status -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seeded", "milestones", "expected"),
    [
        ("complete", [], "complete"),
        ("complete", [ms("2026-11-01")], "complete"),  # complete wins over a past-due milestone
        ("complete", [ms("2026-12-01")], "complete"),  # and over unfinished future ones
        ("off_track", [], "off_track"),  # seeded off_track with no milestones (seed r4)
        ("off_track", [ms("2026-10-01", True)], "off_track"),  # seeded status alone is enough
        ("off_track", [ms("2026-12-01")], "off_track"),
        ("on_track", [ms("2026-11-10")], "off_track"),  # unfinished milestone due yesterday (seed r2 / ms5)
        ("on_track", [ms("2026-11-11")], "on_track"),  # due today is not past due (seed r1 / ms2)
        ("on_track", [ms("2026-11-12")], "on_track"),
        ("on_track", [ms("2026-10-15", True)], "on_track"),  # a done milestone in the past is fine
        ("on_track", [ms("2026-10-01", True), ms("2026-12-01")], "on_track"),
        ("on_track", [ms("2026-10-01", True), ms("2026-11-10")], "off_track"),  # any one past due is enough
        ("on_track", [], "on_track"),  # no milestones: the seeded status alone (seed r5, r6, r7)
    ],
)
def test_rock_status_rules(seeded: str, milestones: list[MilestoneFacts], expected: str) -> None:
    assert rock_status(seeded, milestones, TODAY) == expected  # type: ignore[arg-type]


def test_seed_rocks_r1_and_r2() -> None:
    assert rock_status("on_track", R1, TODAY) == "on_track"
    assert rock_status("on_track", R2, TODAY) == "off_track"


def test_the_status_depends_on_today_not_on_the_system_clock() -> None:
    milestones = [ms("2026-11-11")]
    assert rock_status("on_track", milestones, date(2026, 11, 11)) == "on_track"
    assert rock_status("on_track", milestones, date(2026, 11, 12)) == "off_track"
    assert rock_status("on_track", milestones, date(2026, 11, 10)) == "on_track"


def test_unknown_seeded_status_is_rejected() -> None:
    with pytest.raises(ValueError):
        rock_status("late", [], TODAY)  # type: ignore[arg-type]


# --- rock_progress -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seeded", "milestones", "expected"),
    [
        ("on_track", R2, 13),  # 1/8 = 12.5 -> 13 (seed r2)
        ("on_track", R1, 33),  # 1/3 = 33.3 -> 33 (seed r1)
        ("on_track", [ms("2026-11-01", True), ms("2026-12-01", True), ms("2026-12-02")], 67),  # 2/3
        ("on_track", [ms("2026-12-01"), ms("2026-12-02")], 0),
        ("on_track", [ms("2026-12-01", True), ms("2026-12-02", True)], 100),
        ("on_track", [ms("2026-12-01", True), ms("2026-12-02")], 50),
        ("on_track", [ms("2026-12-01", True), ms("2026-12-02"), ms("2026-12-03"), ms("2026-12-04")], 25),
        ("complete", [], 100),  # no milestones: 100 once complete (seed r3)
        ("on_track", [], 0),  # seed r5, r6, r7
        ("off_track", [], 0),  # seed r4
        ("complete", [ms("2026-12-01", True), ms("2026-12-02")], 50),  # with milestones the ratio is used
    ],
)
def test_rock_progress_is_whole_percent_half_up(
    seeded: str, milestones: list[MilestoneFacts], expected: int
) -> None:
    assert rock_progress(seeded, milestones) == expected
