"""Independent reference implementation of the rules report (test oracle).

Computes every value in brief section 2.6 straight from seed.json, with no
database and no code shared with the backend. The backend's rules report is
checked against this output (differential testing), so a bug has to be made
twice, the same way, to slip through.

Usage:
    uv run --with tzdata python tools/oracle_report.py [--now 2026-11-11T15:00:00Z] [--seed seed/seed.json]
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
WEEKS = 13


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def local_date(ts: datetime) -> date:
    return ts.astimezone(TZ).date()


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_end_exclusive_utc(week_start: date) -> datetime:
    nxt = week_start + timedelta(days=7)
    return datetime(nxt.year, nxt.month, nxt.day, tzinfo=TZ).astimezone(timezone.utc)


def half_up(x: Decimal, places: str) -> Decimal:
    return x.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def quarter(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def status(goal: dict, value: Decimal | None, week: date, current_week: date) -> str:
    if value is None:
        return "pending" if week >= current_week else "off_track"
    t = goal["type"]
    if t == "at_least":
        ok = value >= Decimal(str(goal["value"]))
    elif t == "at_most":
        ok = value <= Decimal(str(goal["value"]))
    elif t == "exactly":
        ok = value == Decimal(str(goal["value"]))
    elif t == "between":
        ok = Decimal(str(goal["min"])) <= value <= Decimal(str(goal["max"]))
    else:
        raise ValueError(t)
    return "on_track" if ok else "off_track"


def build(seed: dict, now: datetime) -> dict:
    today = local_date(now)
    current_week = monday(today)
    weeks = [current_week - timedelta(weeks=WEEKS - 1 - i) for i in range(WEEKS)]
    cur_q = quarter(today)

    scores = {
        (s["measurable_id"], date.fromisoformat(s["week_start"])): Decimal(str(s["value"]))
        for s in seed["scores"]
    }

    out: dict = {"now": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "teams": {}}
    for team in sorted(seed["teams"], key=lambda t: t["id"]):
        tid = team["id"]
        meas = [m for m in seed["measurables"] if m["team_id"] == tid]
        todos = [t for t in seed["todos"] if t["team_id"] == tid]
        issues = [i for i in seed["issues"] if i["team_id"] == tid]
        rocks = [r for r in seed["rocks"] if r["team_id"] == tid and r["quarter"] == cur_q]

        measurable_status = {
            m["id"]: {w.isoformat(): status(m["goal"], scores.get((m["id"], w)), w, current_week) for w in weeks}
            for m in sorted(meas, key=lambda m: m["id"])
        }

        completion = {}
        for w in weeks:
            due = [t for t in todos if w <= date.fromisoformat(t["due_date"]) <= w + timedelta(days=6)]
            if not due:
                completion[w.isoformat()] = None
                continue
            end = week_end_exclusive_utc(w)
            done = sum(1 for t in due if t["done_at"] and parse_ts(t["done_at"]) < end)
            completion[w.isoformat()] = {
                "due": len(due),
                "done": done,
                "rate_percent": int(half_up(Decimal(done * 100) / Decimal(len(due)), "1")),
                "meets_target": done * 10 >= len(due) * 9,
            }

        overdue = sorted(
            t["id"] for t in todos if not t["done_at"] and date.fromisoformat(t["due_date"]) < today
        )

        rock_out = {}
        for r in sorted(rocks, key=lambda r: r["id"]):
            ms = [m for m in seed["milestones"] if m["rock_id"] == r["id"]]
            if r["status"] == "complete":
                st = "complete"
            elif r["status"] == "off_track" or any(
                not m["done"] and date.fromisoformat(m["due_date"]) < today for m in ms
            ):
                st = "off_track"
            else:
                st = "on_track"
            if ms:
                prog = int(half_up(Decimal(sum(m["done"] for m in ms) * 100) / Decimal(len(ms)), "1"))
            else:
                prog = 100 if r["status"] == "complete" else 0
            rock_out[r["id"]] = {"status": st, "progress_percent": prog}

        unsolved = [i for i in issues if i["solved_at"] is None]
        unsolved.sort(
            key=lambda i: (i["priority"] is None, -(i["priority"] or 0), parse_ts(i["created_at"]), i["id"])
        )

        meetings = {}
        for mt in sorted((m for m in seed["meetings"] if m["team_id"] == tid and m["ended_at"]), key=lambda m: m["id"]):
            start, end = parse_ts(mt["started_at"]), parse_ts(mt["ended_at"])
            meeting_week = monday(local_date(start))
            sc_week = meeting_week - timedelta(weeks=1)
            ratings = [r["rating"] for r in mt["ratings"]]
            meetings[mt["id"]] = {
                "scorecard_week": sc_week.isoformat(),
                "measurable_status": {
                    m["id"]: status(m["goal"], scores.get((m["id"], sc_week)), sc_week, meeting_week)
                    for m in sorted(meas, key=lambda m: m["id"])
                },
                "issues_solved": sorted(
                    i["id"] for i in issues if i["solved_at"] and start <= parse_ts(i["solved_at"]) <= end
                ),
                "todos_created": sorted(t["id"] for t in todos if start <= parse_ts(t["created_at"]) <= end),
                "attendee_ids": sorted(mt["attendee_ids"]),
                "rating": float(half_up(Decimal(sum(ratings)) / Decimal(len(ratings)), "0.1")) if ratings else None,
            }

        out["teams"][tid] = {
            "measurable_status": measurable_status,
            "todo_completion": completion,
            "overdue_todos": overdue,
            "rocks": rock_out,
            "issue_order": [i["id"] for i in unsolved],
            "meetings": meetings,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", default="2026-11-11T15:00:00Z")
    ap.add_argument("--seed", default=str(Path(__file__).resolve().parents[1] / "seed" / "seed.json"))
    args = ap.parse_args()
    seed = json.loads(Path(args.seed).read_text(encoding="utf-8"))
    print(json.dumps(build(seed, parse_ts(args.now)), indent=2))


if __name__ == "__main__":
    main()
