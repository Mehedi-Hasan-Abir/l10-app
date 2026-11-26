# L10 App: Implementation Plan

This is the build spec. Agents implement it. Where this file and the brief disagree, **this file wins**, except for
anything in brief section 2.6 (business rules) or the rules-report format, where the brief wins and this file is a bug.

- Brief: `docs/_private/brief.md` (not committed). Rules report format: `docs/_private/starter-readme.md`.
- Seed: `seed/seed.json`. Keep its ids (`u1`, `m1`, `t12`, …).
- Answer key for the seed at the default clock: `backend/tests/fixtures/expected_rules_report.json`, produced by the
  independent oracle `tools/oracle_report.py`. **The backend report must equal it exactly.**

---

## 1. Stack and layout

| Layer | Choice |
|---|---|
| API | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 **async** + asyncpg, Alembic, uv |
| DB | Postgres 17 |
| Web | Next.js (App Router) + TypeScript strict, Tailwind, shadcn/ui, TanStack Query v5, react-hook-form + zod |
| Realtime | WebSocket per team (`/ws/teams/{team_id}`), server push only |
| Edge | Caddy reverse proxy: `/api/*` and `/ws/*` go to the API, everything else to Next.js. One origin → no CORS, same-origin cookies, WS works |
| Tests | pytest + pytest-asyncio + httpx (API, rules); Playwright (E2E, two browser contexts) |

```
l10-app/
  CLAUDE.md                 agent harness: commands, conventions, invariants
  README.md  DECISIONS.md   deliverables (written last)
  rules_report.json         committed output of the rules report
  docker-compose.yml        db, api, web, proxy (+ test profile)
  infra/                    caddy/Caddyfile, db/init/*.sql
  seed/seed.json
  tools/oracle_report.py    independent reference implementation of 2.6 (test oracle)
  scripts/test.sh           ONE command: full unattended test run in an isolated compose project
  backend/
    pyproject.toml uv.lock alembic.ini Dockerfile
    alembic/versions/0001_initial.py
    app/
      main.py config.py clock.py db.py models.py ids.py errors.py
      auth.py               session cookie + authorization dependencies
      rules/                PURE functions, no I/O (see §3). Only place business math lives.
      services/             DB reads/writes, call rules/, publish realtime events
      routers/              thin HTTP layer: auth, teams, scorecard, issues, todos, rocks, meetings, ws
      realtime/hub.py       in-process pub/sub of team events to WebSocket connections
      schemas.py            Pydantic request/response models (the API contract in code)
      seed.py               `python -m app.seed [--reset]`
      report.py             `python -m app.report [--now ISO] [--live]`
      export_openapi.py     `python -m app.export_openapi > ../frontend/openapi.json`
    tests/
      conftest.py fixtures/ rules/ api/
  frontend/
    src/app/ src/components/ src/lib/
  e2e/                      Playwright package (own package.json, Dockerfile): tests/meeting-sync.spec.ts
```

---

## 2. Time and the clock (read carefully: most rule bugs live here)

- Company time zone `America/Chicago` (from `company.timezone`; use `zoneinfo`). All stored timestamps are
  `timestamptz` in UTC. Dates without time (`due_date`, `week_start`) are company-local calendar dates (`date`).
- **Week** = Monday 00:00:00 to Sunday 23:59:59 company time, named by its Monday. Implement the end as the
  exclusive instant `next Monday 00:00 local`, converted to UTC per-week (DST changes the offset: week of
  2026-10-26 ends `2026-11-02T06:00:00Z`, not 05:00Z).
- `today` = company-local date of `now`. `current_week` = Monday of `today`. `last_closed_week` = `current_week - 7d`.
- Scorecard window = 13 weeks ending with `current_week` (oldest first).
- Quarter = calendar quarter of `today`, formatted `"2026-Q4"`.
- **Rounding** is half up everywhere, using `Decimal` and `ROUND_HALF_UP`. Never `round()` or float math
  (`round(8.25, 1) == 8.2` in Python). Percent = `Decimal(n*100)/Decimal(d)` quantized to `1`. Rating quantized to `0.1`.
- Score values are `NUMERIC` in Postgres → `Decimal` in Python. Load seed JSON with `parse_float=Decimal`.
  Serialize to JSON as numbers (floats) at the API edge only.

### Clock
`APP_NOW` (default `2026-11-11T15:00:00Z`) anchors business time. The system clock is never used for business time.

```python
class Clock(Protocol):
    def now(self) -> datetime: ...          # aware UTC

class FrozenClock:   # tests, rules report: now() == fixed instant
class ManualClock(FrozenClock): def advance(self, seconds): ...   # tests of the meeting timer
class RunningClock:  # the server: now() = APP_NOW + (real_elapsed since anchor)
```

- `APP_CLOCK=running` (default for the server) or `frozen`.
- RunningClock exists because the meeting timer must tick. Elapsed time is measured from an anchor persisted in
  table `app_clock(app_now timestamptz PK, real_anchor timestamptz)`: on startup
  `INSERT (APP_NOW, now()) ON CONFLICT DO NOTHING`, then read `real_anchor`. Restarts with the same `APP_NOW` therefore
  continue the same timeline (meeting timers survive an API restart); a new `APP_NOW` gets a new anchor.
  `seed --reset` clears `app_clock`.
- The clock is a FastAPI dependency (`get_clock`) stored on `app.state.clock`; tests override it.
- The rules report always uses `FrozenClock(APP_NOW or --now)`.

---

## 3. Business rules: `app/rules/` (pure, no I/O, 100% unit-tested)

Signatures are fixed so the API and report can be written in parallel. Inputs are plain dataclasses/primitives,
not ORM objects.

```python
# rules/timeutil.py
COMPANY_TZ: ZoneInfo
def company_date(ts: datetime, tz=COMPANY_TZ) -> date
def week_start_of(d: date) -> date                                   # Monday
def week_bounds_utc(week_start: date, tz=COMPANY_TZ) -> tuple[datetime, datetime]   # [start, end) in UTC
def scorecard_weeks(current_week: date, n: int = 13) -> list[date]  # oldest first
def quarter_of(d: date) -> str                                       # "2026-Q4"
def round_half_up(x: Decimal, places: int = 0) -> Decimal
def percent(numerator: int, denominator: int) -> int                 # half up; denominator > 0

@dataclass(frozen=True)
class TimeContext:            # everything derived from `now`; built once per request
    now: datetime; today: date; current_week: date; last_closed_week: date; quarter: str
    @classmethod
    def at(cls, now: datetime, tz=COMPANY_TZ) -> "TimeContext"

# rules/measurables.py
GoalType = Literal["at_least", "at_most", "between", "exactly"]
@dataclass(frozen=True)
class Goal: type: GoalType; value: Decimal | None = None; min: Decimal | None = None; max: Decimal | None = None
def measurable_status(goal: Goal, value: Decimal | None, week_start: date, current_week: date) -> Literal["on_track","off_track","pending"]
    # value None: "pending" if week_start >= current_week else "off_track". Boundaries are on track.
def goal_label(goal: Goal, unit: str) -> str     # "≥ 10", "≤ 3.0 %", "2 – 4 hours", "= 1"

# rules/todos.py
@dataclass(frozen=True)
class TodoFacts: id: str; due_date: date; done_at: datetime | None
@dataclass(frozen=True)
class Completion: due: int; done: int; rate_percent: int; meets_target: bool
def todo_completion(todos: Iterable[TodoFacts], week_start: date, tz=COMPANY_TZ) -> Completion | None
    # due that week: week_start <= due_date <= week_start+6.  done: done_at < week end (exclusive, UTC per week_bounds_utc)
    # meets_target compares the exact ratio: done*10 >= due*9.  Zero due -> None.
def is_overdue(due_date: date, done_at: datetime | None, today: date) -> bool   # not done and due_date < today

# rules/rocks.py
@dataclass(frozen=True)
class MilestoneFacts: due_date: date; done: bool
def rock_status(seeded_status: Literal["on_track","off_track","complete"], milestones: Sequence[MilestoneFacts], today: date) -> Literal["on_track","off_track","complete"]
def rock_progress(seeded_status: str, milestones: Sequence[MilestoneFacts]) -> int
    # with milestones: percent(done, total). none: 100 if complete else 0
def milestone_past_due(m: MilestoneFacts, today: date) -> bool       # not done and due_date < today

# rules/issues.py
@dataclass(frozen=True)
class IssueFacts: id: str; priority: int | None; created_at: datetime
def issue_sort_key(i: IssueFacts) -> tuple    # (priority is None, -(priority or 0), created_at, id)
def order_issues(issues: Iterable[T], key=...) -> list[T]

# rules/meetings.py
def meeting_rating(ratings: Sequence[int]) -> Decimal | None          # one decimal half up; empty -> None
def scorecard_week_for(started_at: datetime, tz=COMPANY_TZ) -> date   # week_start_of(company_date(started_at)) - 7d
AGENDA = (("scorecard", "Scorecard", 300), ("ids", "IDS", 3600), ("conclude", "Conclude", 600))
def next_section(section: str) -> str | None
def section_timing(section_started_at: datetime, now: datetime, duration_s: int) -> tuple[int, bool]  # (elapsed_s >= 0, over_time)
```

Expected values for the seed at the default clock (traps): `t11` done `2026-11-02T05:30Z` counts for week
2026-10-26 (DST: Sunday 23:30 CST); `t28` (Sun 23:59 local) counts, `t29` (Mon 00:01 local) does not, so week
2026-11-02 is exactly 9/10 = 90% → meets target; `r2` progress 1/8 = 12.5 → 13; `mt2` rating 33/4 = 8.25 → 8.3;
`ms2` due today does not make `r1` off track; `ms5` due yesterday makes `r2` off track; `m5` has no score for
2026-10-19 and 2026-11-02 (closed → off_track) but has one for the current week (→ evaluated, on_track);
`i4` (no priority) sorts last.

---

## 4. Data model (Alembic migration `0001_initial`)

Text primary keys (seed ids preserved). New rows get `"{prefix}_{token_hex(5)}"` from `app/ids.py`
(`i_`, `t_`, `mt_`). All FKs `ON DELETE RESTRICT` (nothing is deleted by the app).

```sql
companies(id text PK, name text NOT NULL, timezone text NOT NULL)
teams(id text PK, company_id text NOT NULL FK, name text NOT NULL)
users(id text PK, company_id text NOT NULL FK, name text NOT NULL, email text NOT NULL UNIQUE,
      role text NOT NULL CHECK (role IN ('admin','team_member','observer')))
memberships(user_id FK, team_id FK, PRIMARY KEY (user_id, team_id))

measurables(id PK, team_id FK NOT NULL, name NOT NULL, owner_id FK users NOT NULL, unit text NOT NULL,
  goal_type text NOT NULL CHECK (goal_type IN ('at_least','at_most','between','exactly')),
  goal_value numeric, goal_min numeric, goal_max numeric, sort_order int NOT NULL,
  CHECK ((goal_type = 'between' AND goal_min IS NOT NULL AND goal_max IS NOT NULL AND goal_min <= goal_max AND goal_value IS NULL)
      OR (goal_type <> 'between' AND goal_value IS NOT NULL AND goal_min IS NULL AND goal_max IS NULL)),
  UNIQUE (id, team_id))                                   -- target for composite FKs below

scores(measurable_id FK NOT NULL, week_start date NOT NULL, value numeric NOT NULL,
  version int NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL, updated_by text FK users NULL,
  PRIMARY KEY (measurable_id, week_start),
  CHECK (EXTRACT(ISODOW FROM week_start) = 1))           -- weeks are Mondays

rocks(id PK, team_id FK NOT NULL, title NOT NULL, owner_id FK NOT NULL, quarter text NOT NULL CHECK (quarter ~ '^[0-9]{4}-Q[1-4]$'),
  seeded_status text NOT NULL CHECK (seeded_status IN ('on_track','off_track','complete')), sort_order int NOT NULL,
  UNIQUE (id, team_id))
milestones(id PK, rock_id FK NOT NULL, title NOT NULL, due_date date NOT NULL, done boolean NOT NULL DEFAULT false, sort_order int NOT NULL)

issues(id PK, team_id FK NOT NULL, title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
  owner_id FK users NOT NULL, priority smallint NULL CHECK (priority BETWEEN 1 AND 5),
  version int NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL, solved_at timestamptz NULL, resolution_note text NULL,
  source_type text NULL CHECK (source_type IN ('measurable','rock','todo')),
  source_measurable_id text NULL, source_week_start date NULL,
  source_rock_id text NULL, source_todo_id text NULL,
  CHECK ((solved_at IS NULL AND resolution_note IS NULL)
      OR (solved_at IS NOT NULL AND resolution_note IS NOT NULL AND length(btrim(resolution_note)) BETWEEN 1 AND 2000)),
  CHECK ((source_type IS NULL AND source_measurable_id IS NULL AND source_week_start IS NULL AND source_rock_id IS NULL AND source_todo_id IS NULL)
      OR (source_type = 'measurable' AND source_measurable_id IS NOT NULL AND source_week_start IS NOT NULL AND source_rock_id IS NULL AND source_todo_id IS NULL)
      OR (source_type = 'rock' AND source_rock_id IS NOT NULL AND source_measurable_id IS NULL AND source_week_start IS NULL AND source_todo_id IS NULL)
      OR (source_type = 'todo' AND source_todo_id IS NOT NULL AND source_measurable_id IS NULL AND source_week_start IS NULL AND source_rock_id IS NULL)),
  CHECK (source_week_start IS NULL OR EXTRACT(ISODOW FROM source_week_start) = 1),
  FOREIGN KEY (source_measurable_id, team_id) REFERENCES measurables (id, team_id),   -- source is in the same team
  FOREIGN KEY (source_rock_id, team_id) REFERENCES rocks (id, team_id),
  UNIQUE (id, team_id))
-- THE duplicate guard (2.6 "No duplicate Issues"): at most one UNSOLVED issue per source.
CREATE UNIQUE INDEX uq_issue_open_measurable ON issues (source_measurable_id, source_week_start) WHERE solved_at IS NULL AND source_type = 'measurable';
CREATE UNIQUE INDEX uq_issue_open_rock       ON issues (source_rock_id) WHERE solved_at IS NULL AND source_type = 'rock';
CREATE UNIQUE INDEX uq_issue_open_todo       ON issues (source_todo_id) WHERE solved_at IS NULL AND source_type = 'todo';

todos(id PK, team_id FK NOT NULL, title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 200),
  owner_id FK users NOT NULL, due_date date NOT NULL, done_at timestamptz NULL,
  source_issue_id text NULL, created_at timestamptz NOT NULL,
  FOREIGN KEY (source_issue_id, team_id) REFERENCES issues (id, team_id),           -- linked issue is in the same team
  UNIQUE (id, team_id))
-- issues.source_todo_id → todos(id, team_id) composite FK added after todos exists (circular reference).

meetings(id PK, team_id FK NOT NULL, facilitator_id FK users NOT NULL,
  started_at timestamptz NOT NULL, ended_at timestamptz NULL,
  current_section text NOT NULL CHECK (current_section IN ('scorecard','ids','conclude')),
  section_started_at timestamptz NOT NULL, scorecard_week date NOT NULL CHECK (EXTRACT(ISODOW FROM scorecard_week) = 1),
  summary jsonb NULL,                                   -- frozen snapshot written when the meeting ends
  CHECK (ended_at IS NULL OR ended_at >= started_at),
  CHECK ((ended_at IS NULL) = (summary IS NULL)))
CREATE UNIQUE INDEX uq_meeting_one_active_per_team ON meetings (team_id) WHERE ended_at IS NULL;
meeting_attendees(meeting_id FK, user_id FK, joined_at timestamptz NOT NULL, PRIMARY KEY (meeting_id, user_id))
meeting_ratings(meeting_id, user_id, rating smallint NOT NULL CHECK (rating BETWEEN 1 AND 10), updated_at timestamptz NOT NULL,
  PRIMARY KEY (meeting_id, user_id),
  FOREIGN KEY (meeting_id, user_id) REFERENCES meeting_attendees (meeting_id, user_id))  -- only attendees rate

app_clock(app_now timestamptz PRIMARY KEY, real_anchor timestamptz NOT NULL)
```

**Role trigger** (DB-level "Observers cannot own, facilitate or rate"): one plpgsql function
`assert_not_observer()` used by `BEFORE INSERT OR UPDATE` triggers on `measurables.owner_id`, `rocks.owner_id`,
`issues.owner_id`, `todos.owner_id`, `meetings.facilitator_id`, `meeting_ratings.user_id`; raises
`check_violation` if the referenced user's role is `observer`.

Indexes beyond PK/unique: `issues (team_id) WHERE solved_at IS NULL`, `todos (team_id, due_date)`, `scores (measurable_id, week_start)` is the PK.

**Seed mapping**: rock `status` → `seeded_status`; measurable `goal` → goal columns; issue `source` →
`source_type='measurable'` + columns; meeting `ratings` → `meeting_ratings`, `attendee_ids` → `meeting_attendees`
(joined_at = started_at). Seeded meetings get `current_section='conclude'`, `section_started_at=started_at`,
`scorecard_week=scorecard_week_for(started_at)`, and `summary` computed by the same function used at meeting end.
`sort_order` = position in the seed array.

**Seed script** `python -m app.seed`: runs inside one transaction; inserts every seed row with
`ON CONFLICT DO NOTHING` (rerun-safe, never clobbers app-created data). `--reset` truncates all tables
(`TRUNCATE … RESTART IDENTITY CASCADE`) first, then loads. Entrypoint: `alembic upgrade head && python -m app.seed`.

---

## 5. Authorization (server-side; hiding buttons is not authorization)

| Action | admin | team_member (member of team) | observer (member of team) | non-member |
|---|---|---|---|---|
| Read team data, join meeting, open WS | ✅ any team | ✅ | ✅ | ❌ 403 |
| Edit score, make issue, create/prioritize/solve issue, add/complete to-do | ✅ any team | ✅ | ❌ 403 | ❌ 403 |
| Start meeting (becomes facilitator) | ✅ | ✅ | ❌ | ❌ |
| Advance / end meeting | facilitator only (403 for everyone else) | | | |
| Rate (Conclude only, meeting active, must have joined) | ✅ | ✅ | ❌ | ❌ |
| Be an owner (issue/to-do owner) | ✅ | ✅ | ❌ (also DB trigger) | ❌ |

- `auth.py` dependencies: `current_user` (401 `not_authenticated`), `team_reader(team_id)` and
  `team_writer(team_id)` returning `TeamContext(team, user, can_write)`.
- **Non-admins get 403 `team_forbidden` for both "not a member" and "team does not exist"** (no team-id probing).
  The same holds for meeting ids (`/api/meetings/{id}`): unknown id → 403 for non-admins, 404 `meeting_not_found` for admins.
  Admins get 404 for a nonexistent team.
- Nested resources are always loaded with `team_id` in the WHERE clause: an issue id from another team → 404.
- Eligible owners for team T (to-do owner dropdown and validation): users with role `admin` or `team_member` who are
  members of T, plus all admins (admins act in every team). Assumption, documented in README.
- Session: `POST /api/auth/login {user_id}` sets cookie `l10_session` = itsdangerous-signed user id;
  `HttpOnly; SameSite=Lax; Path=/`. No passwords (brief allows a seeded user switcher). The WS handshake reads the
  same cookie and checks `Origin` against `ALLOWED_ORIGINS` (blocks cross-site WebSocket hijacking).
- Input validation in Pydantic: strip strings; titles 1–200 chars; notes 1–2000; priority int 1–5 or null; rating
  int 1–10; score value finite with |value| < 1e12; `week_start` must be a Monday in the scorecard window; unknown
  fields rejected (`extra="forbid"`).

---

## 6. REST API (all under `/api`, JSON, OpenAPI at `/api/docs` and `/api/openapi.json`)

Errors: `{"detail": {"code": "snake_case", "message": "Human readable"}}` with 401/403/404/409; 422 keeps
FastAPI's validation format. Timestamps ISO-8601 UTC with `Z`. Dates `YYYY-MM-DD`. Every mutation commits, then
publishes realtime events (§7), then returns.

Shared shapes (TypeScript notation, mirrored by Pydantic in `schemas.py`):
```ts
type UserRef = { id: string; name: string; role: "admin" | "team_member" | "observer" }
type Status = "on_track" | "off_track" | "pending"
type IssueSource =
  | { type: "measurable"; measurable_id: string; measurable_name: string; week_start: string }
  | { type: "rock"; rock_id: string; rock_title: string }
  | { type: "todo"; todo_id: string; todo_title: string }
type Issue = { id; team_id; title; owner: UserRef; priority: number | null; created_at; solved_at: string | null;
               resolution_note: string | null; source: IssueSource | null; version: number; todo_count: number }
type Todo = { id; team_id; title; owner: UserRef; due_date; done_at: string | null; done: boolean; overdue: boolean;
              source_issue: { id: string; title: string } | null; created_at }
type Completion = { week_start: string; due: number; done: number; rate_percent: number; meets_target: boolean }
type ClockInfo = { now; today; current_week; last_closed_week; quarter }
```

### Auth
- `GET /api/auth/users` → `UserRef[]` (public; feeds the user switcher)
- `POST /api/auth/login {user_id}` → `{user: UserRef}` + cookie. 404 unknown user.
- `POST /api/auth/logout` → 204
- `GET /api/auth/me` → `{user: UserRef, teams: {id, name}[]}` (admins: every team)

### Teams
- `GET /api/teams` → `{id, name}[]`
- `GET /api/teams/{team_id}` → `{id, name, members: UserRef[], eligible_owners: UserRef[], permissions: {can_write, can_facilitate, can_rate}, clock: ClockInfo, active_meeting: {id, facilitator: UserRef, current_section} | null}`

### Scorecard
- `GET /api/teams/{team_id}/scorecard` →
  `{weeks: string[13], current_week, last_closed_week, measurables: [{id, name, unit, owner: UserRef, goal: {type, value, min, max}, goal_label, cells: [{week_start, value: number|null, status: Status, version: number|null, open_issue_id: string|null}]}]}`
  (`cells` aligned to `weeks`; `open_issue_id` = unsolved issue for that cell, if any)
- `PUT /api/teams/{team_id}/measurables/{measurable_id}/scores/{week_start}` body `{value: number|null, expected_version?: number}` → the cell.
  `null` clears the score. Last save wins; if `expected_version` is sent and stale → 409 `version_conflict` with current cell in `detail.current` (bonus: lost-update protection). Week outside window / not Monday → 422.
- `POST /api/teams/{team_id}/measurables/{measurable_id}/scores/{week_start}/issue` → **201** `{issue, created: true}` or **200** `{issue, created: false}`. 409 `not_off_track` if the cell is not off track.
  Title: `"{measurable name}, week of {Mon D}"` e.g. `"New deals closed, week of Nov 2"` (matches seed). Owner = caller. Priority null.

### Issues
- `GET /api/teams/{team_id}/issues?status=unsolved|solved` (default unsolved) → `Issue[]`; unsolved in Issue order, solved by `solved_at` desc.
- `POST /api/teams/{team_id}/issues {title, priority?: 1..5|null}` → 201 Issue (owner = caller, `created_at = now`)
- `PATCH /api/teams/{team_id}/issues/{issue_id} {priority: 1..5|null, expected_version?}` → Issue. 409 `issue_solved` if solved; 409 `version_conflict` if stale.
- `POST /api/teams/{team_id}/issues/{issue_id}/solve {resolution_note}` → Issue. 409 `issue_solved` if already solved.
- `POST /api/teams/{team_id}/issues/{issue_id}/todos {title, owner_id}` → 201 Todo (`due_date = today + 7`, `source_issue_id = issue`). 422 `owner_not_eligible`.
- Bonus: `POST /api/teams/{team_id}/rocks/{rock_id}/issue` (rock must be off_track) and `POST /api/teams/{team_id}/todos/{todo_id}/issue` (to-do must be overdue), same 201/200 semantics.

### To-Dos
- `GET /api/teams/{team_id}/todos` → `{todos: Todo[], last_closed_week: string, completion: Completion | null}`; open first (due date asc), then done (done_at desc).
- `PATCH /api/teams/{team_id}/todos/{todo_id} {done: boolean}` → Todo (`done_at = now` or null; idempotent).

### Rocks
- `GET /api/teams/{team_id}/rocks` → `{quarter, rocks: [{id, title, owner: UserRef, seeded_status, status, progress_percent, milestones: [{id, title, due_date, done, past_due}]}]}` (current quarter only)

### Meetings
```ts
type MeetingState = {
  id; team_id; status: "active" | "ended"; facilitator: UserRef;
  started_at; ended_at: string | null; scorecard_week: string;
  agenda: { key: "scorecard" | "ids" | "conclude"; title: string; duration_seconds: number }[];
  current_section: "scorecard" | "ids" | "conclude"; section_started_at: string;
  server_now: string;                          // clock.now() at serialization; client derives offset
  attendees: (UserRef & { joined_at: string; online: boolean })[];
  ratings: { user_id: string; name: string; rating: number }[];
  average_rating: number | null;
}
type MeetingSummary = { meeting_id; team_id; scorecard_week; started_at; ended_at;
  measurable_status: { measurable_id: string; name: string; value: number | null; status: Status }[];
  issues_solved: { id: string; title: string }[]; todos_created: { id: string; title: string; owner: UserRef; due_date: string }[];
  attendees: UserRef[]; rating: number | null }
```
- `GET /api/teams/{team_id}/meetings?status=active|ended` → `MeetingState[]` (ended: newest first)
- `POST /api/teams/{team_id}/meetings` → 201 MeetingState (caller = facilitator, auto-joined, section `scorecard`, `scorecard_week = last_closed_week`). 409 `meeting_already_active` with `detail.meeting_id` (race-safe via the partial unique index).
- `GET /api/meetings/{meeting_id}` → MeetingState (team read access required)
- `POST /api/meetings/{meeting_id}/join` → MeetingState (idempotent; any reader; 409 `meeting_ended`)
- `POST /api/meetings/{meeting_id}/advance` optional body `{from_section}` → MeetingState (facilitator only; `SELECT … FOR UPDATE`; scorecard→ids→conclude; 409 `no_next_section` at conclude; 409 `stale_section` with `detail.current_section` if `from_section` is no longer current, so a double-click never skips a section). Overrun never auto-advances.
- `POST /api/meetings/{meeting_id}/end` → MeetingState (facilitator only, any section; writes `ended_at` and `summary` snapshot in the same transaction)
- `PUT /api/meetings/{meeting_id}/rating {rating: 1..10}` → MeetingState (upsert; Conclude only else 409 `not_in_conclude`; must be attendee with role admin/team_member; 409 `meeting_ended` after end)
- `GET /api/meetings/{meeting_id}/recap` → `{issues_solved: Issue[], todos_created: Todo[]}` live view of the meeting window `[started_at, ended_at or now]` (Conclude uses it)
- `GET /api/meetings/{meeting_id}/summary` → MeetingSummary (ended meetings only; returns the stored snapshot; 409 `meeting_active`)

**"During the meeting"** = team's issues with `solved_at` and to-dos with `created_at` in `[started_at, ended_at]`
inclusive. With one active meeting per team this equals "while the meeting was running" and works identically for
seeded and live meetings. Summary `measurable_status` = status of each team measurable for `scorecard_week`
evaluated with `current_week = week of started_at` (so the reviewed week is always "closed").

---

## 7. Realtime protocol (`WS /ws/teams/{team_id}`)

- Handshake: cookie auth + Origin check + team read access; else close with code 4401 / 4403.
- Server → client messages (JSON):
  - `{"type":"snapshot","meeting": MeetingState|null}`: sent immediately on connect (late join/refresh/reconnect gets current state).
  - `{"type":"meeting","meeting": MeetingState}`: after every meeting change (start, join, advance, rate, end, presence change).
  - `{"type":"invalidate","resources": ("scorecard"|"issues"|"todos"|"rocks"|"meetings")[]}`: after any data mutation in the team.
  - `{"type":"ping"}` every 25 s (keeps proxies from idling the socket). Client may ignore it.
- Client → server: nothing is required; the server ignores incoming messages.
- `realtime/hub.py`: `Hub.connect(team_id, user, ws)`, `disconnect(...)`, `publish(team_id, message)`, `online_user_ids(team_id)`.
  In-process (single API process). Publish happens **after** the DB commit. Scale path (Postgres LISTEN/NOTIFY or
  Redis fan-out) goes in the decision record, not the code.
- **Ownership**: Postgres owns meeting state (section, `section_started_at`, attendees, ratings). The WebSocket only
  carries notifications; every state change goes through REST + a DB transaction. Recovery = reconnect → snapshot +
  client invalidates all team queries. The server owns the timer: clients render
  `elapsed = (Date.now() + offset) - section_started_at`, where `offset = server_now - Date.now()` captured on receipt.

---

## 8. Rules report (`python -m app.report`)

- Prints the JSON in `docs/_private/starter-readme.md` format to stdout, `indent=2`, keys in the order of that format.
- Default mode **fresh**: opens one transaction, truncates, seeds, computes, prints, **rolls back** → always reflects
  a freshly seeded DB without touching live data. `--live` reports the current DB instead. `--now ISO` overrides `APP_NOW`.
- Computes through the same `services/` + `rules/` code the API uses (no duplicate logic). Meeting entries use the
  live summary computation (same function that writes the snapshot).
- Output for default `APP_NOW` must equal `backend/tests/fixtures/expected_rules_report.json` (as parsed JSON).
- The test suite checks this, and also diffs the report against `tools/oracle_report.py` at extra dates
  (e.g. `2026-11-18T15:00:00Z`, `2026-12-02T05:59:00Z`, `2026-10-01T12:00:00Z`).

---

## 9. Tests: fixture and clock strategy

- `tests/rules/`: pure unit tests, no DB. At least one boundary case per 2.6 rule (see the traps in §3), plus DST week bounds.
- `tests/api/`: real Postgres database `l10_test` (`TEST_DATABASE_URL`). Session fixture runs `alembic upgrade head`
  once; a per-test fixture truncates and reseeds (~100 rows, fast) so each test starts from the seed. App built with
  `create_app(clock=FrozenClock(2026-11-11T15:00Z))`; `httpx.AsyncClient(transport=ASGITransport(app))`; a `login(user_id)`
  helper returns a client with the session cookie.
  Required: Team Member reading another team → 403; Observer write → 403; **concurrent Make it an Issue**
  (`asyncio.gather` of ≥ 5 requests from 2 users with separate clients/sessions) → all return the same issue id, exactly
  one row in DB; also the meeting state machine, rating rules, and the report-vs-fixture test.
- WebSocket test: two clients connected via `httpx-ws` or Starlette `TestClient` see `meeting` messages after advance.
- E2E (Playwright): two browser contexts (facilitator `u2` + teammate `u3`); facilitator starts the meeting and
  advances; the second context sees the section change within 2 s. Trace `retain-on-failure`; one committed failure
  trace at `docs/artifacts/`.

---

## 10. Frontend

**Principles**: server state lives in TanStack Query (no duplicated client stores); realtime messages either set the
meeting query data directly or invalidate query keys; forms use react-hook-form + zod; every component is typed;
API types come from the OpenAPI schema (`openapi-typescript` → `src/lib/api/schema.d.ts`, regenerate with
`npm run gen:api`); one fetch wrapper (`src/lib/api/client.ts`) that throws a typed `ApiError {status, code, message}`.

```
src/app/layout.tsx                 providers (QueryClientProvider, Toaster), fonts
src/app/page.tsx                   → /teams/{first team} or /login
src/app/login/page.tsx             seeded-user switcher (name, role badge, teams)
src/app/teams/[teamId]/page.tsx    Team page
src/app/teams/[teamId]/meeting/page.tsx   Meeting page (active meeting or summary of the one that just ended)
src/components/app-header.tsx      team switcher, live meeting pill (Start / Join), company clock, user menu
src/components/scorecard/*         ScorecardTable, ScoreCell (inline edit, status color, Make it an Issue)
src/components/issues/*            IssuesPanel (unsolved/solved tabs), IssueRow, CreateIssueForm, PriorityPicker, SolveIssueDialog, AddTodoDialog
src/components/todos/*             TodosPanel (completion rate badge, overdue flags, done toggle)
src/components/rocks/*             RocksPanel (expandable milestones)
src/components/meeting/*           Agenda, SectionTimer, AttendeeList, FacilitatorControls, ConcludePanel, RatingPicker, MeetingSummary
src/lib/api/{client.ts,schema.d.ts,queries.ts,mutations.ts,keys.ts}
src/lib/realtime/use-team-channel.ts   WS connect, backoff reconnect, snapshot handling, invalidation map
src/lib/time.ts                    format dates/times in America/Chicago; week labels ("Nov 2")
src/lib/permissions.ts             derive UI affordances from GET /teams/{id}.permissions (UX only)
```

**Team page**: header + a strip showing "Today: Wed Nov 11 · Week of Nov 9" (the business clock differs from the real
date, so show it). Scorecard full width (13 week columns, last closed week column highlighted, current week marked;
cell colors: on track green-ish, off track red-ish, pending neutral; click to edit, Enter/blur saves, Esc cancels;
off-track cell shows a "Make it an Issue" action, or a link chip if `open_issue_id`). Below: Issues (left, wider)
and To-Dos + Rocks (right). Observers see the same layout with no edit affordances and a "View only" badge.

**Meeting page**: readable under time pressure. Large section timer (`mm:ss / 05:00`, turns red with
`+mm:ss over` when overrun), agenda rail (3 sections with planned minutes, current highlighted, done ones checked),
attendee list with online dots, facilitator controls (`Next: IDS →`, `End meeting` with confirm) visible only to
the facilitator; others see "Facilitated by …". Body renders the reused ScorecardTable (focused on last closed week)
/ IssuesPanel / ConcludePanel (new To-Dos, rating 1–10 buttons for eligible attendees, everyone's ratings + average).
When the meeting ends every attendee's page switches to MeetingSummary. Opening the page auto-joins.

Use the `frontend-design` and `shadcn` skills. Accessible: real buttons, labels, focus states, keyboard editing.

---

## 11. Ops

- `docker compose up` → `db` (postgres:17-alpine, host port **15432**), `api` (runs migrations + seed, then uvicorn),
  `web` (Next.js standalone), `proxy` (caddy:2, host port **3000** → app at http://localhost:3000, API docs at
  http://localhost:3000/api/docs). Healthchecks + `depends_on: condition: service_healthy`.
- Env: `DATABASE_URL`, `TEST_DATABASE_URL`, `APP_NOW`, `APP_CLOCK`, `SECRET_KEY`, `SEED_PATH`, `ALLOWED_ORIGINS`.
- `scripts/test.sh`: one unattended command. Uses an isolated compose project (`-p l10test`, no host ports):
  builds, starts db+api+web+proxy, runs `pytest` in the api image against `l10_test`, resets the seed, runs Playwright
  (official Playwright image) against `http://proxy`, copies reports/traces out, tears down, exits non-zero on failure.
- Rules report: `docker compose exec api python -m app.report > rules_report.json`; other dates:
  `docker compose exec -e APP_NOW=2026-11-18T15:00:00Z api python -m app.report`.

---

## 12. Work packages (Sonnet agents; strict file ownership so they can run in parallel)

| WP | Owner files | Depends on | Done when |
|---|---|---|---|
| **A: DB, seed, auth, app skeleton** | `backend/app/{main,config,clock,db,models,ids,errors,auth,seed}.py`, `alembic*`, `backend/tests/conftest.py`, `backend/tests/api/test_seed_and_constraints.py`, `backend/tests/api/test_auth.py` | none | migration applies on empty DB; seed loads, rerun is a no-op, `--reset` works; constraint tests show DB rejects bad rows (observer owner, bad priority, duplicate open issue, non-Monday week, cross-team source); login/me works; ruff clean |
| **B: rules** | `backend/app/rules/**`, `backend/tests/rules/**` | none | every function in §3, boundary tests for every 2.6 rule, ruff clean, no I/O imports |
| **C: API, realtime, report** | `backend/app/{schemas.py,services/**,routers/**,realtime/**,report.py,export_openapi.py}`, `backend/tests/api/**` (except A's files), `frontend/openapi.json` | A, B | all endpoints in §6, WS in §7, report equals fixture and oracle at extra dates, required API tests pass |
| **D: frontend** | `frontend/**` except `e2e/` | C's contract (§6); integrates with real API once C lands | all §10 screens working against the real API; `npm run build`, `tsc --noEmit`, `eslint` clean |
| **E: docker, e2e, test script** | `docker-compose.yml`, `infra/**`, `*/Dockerfile`, `.dockerignore`, `scripts/**`, `e2e/**`, `docs/artifacts/**` | C, D | `docker compose up` from clean clone works; `scripts/test.sh` green; failure trace committed |

The orchestrator (Opus) owns this plan, `CLAUDE.md`, `tools/`, fixtures, `README.md`, `DECISIONS.md`, reviews every
WP, and runs the final verification.
