# Decision record

### 1. Data model: derive, don't store; let Postgres refuse bad data
- **Computed values are never stored.** Measurable status, completion rate, overdue, Rock status/progress, Issue order
  and rating are pure functions in `backend/app/rules/`, called by both the API and the rules report. One
  implementation, no stale denormalized columns. *Cost:* recomputed per request (trivial at this data size).
- **Time.** Instants are `timestamptz` (UTC); weeks and due dates are company-local `date`s (`week_start` must be a
  Monday: `CHECK isodow = 1`). A week's end is "next Monday 00:00 Chicago" converted **per week**, so DST weeks are
  167/169 h. A missing score is *no row*; "off track" vs "pending" is derived from the clock.
- **Issue → source** uses typed nullable columns (`source_type` + `source_measurable_id, source_week_start` |
  `source_rock_id` | `source_todo_id`), a CHECK that exactly one shape is set, and **composite FKs
  `(source_id, team_id) → (id, team_id)`**, so a source can't come from another team. An Issue links to a
  *Measurable + week*, not a score row, because an empty closed week is off track and can become an Issue.
  **To-Do → Issue** is `source_issue_id` with the same composite FK. *Rejected:* a polymorphic `(type, id)` text pair
  (no FK, no partial unique index).
- **Integrity in the DB, not only the app:** CHECKs (ranges, non-blank text, goal shape, `solved ⇔ note`), partial
  unique indexes (one open Issue per source; one active meeting per team), `rating → attendee` FK, and a trigger that
  rejects Observers as owners, facilitators or raters.

### 2. API: team-scoped REST, explicit commands, OpenAPI as the contract
Resources nest under `/api/teams/{team_id}/…`, so authorization is one dependency per route (`team_reader` /
`team_writer`) and every nested query filters by `team_id` (another team's id → 404). Meetings are addressed as
`/api/meetings/{id}` and authorized through their team. Non-CRUD actions are explicit commands (`…/solve`,
`…/scores/{week}/issue`, `…/advance`), each with its own rules and error codes, instead of PATCHing status fields.
Errors are `{"detail": {"code", "message"}}` with stable codes; non-admins get the same 403 for "not your team" and
"no such team" (no id probing). Default is last-save-wins as specified; an optional `expected_version` gives
lost-update protection (409 `version_conflict`). The frontend's TypeScript types are generated from `/api/openapi.json`.
*Cost:* more endpoints than a generic PATCH, but each is small, typed and testable.

### 3. Realtime: Postgres owns state, the WebSocket only notifies
One WebSocket per team (`/ws/teams/{id}`), server → client only. Every change (advance, rating, a solved Issue) is
a REST call in a transaction (meeting row `FOR UPDATE`). **After commit** the hub pushes a full `MeetingState` or an
`invalidate` hint, and clients refetch through TanStack Query. **Recovery:** on every (re)connect the server sends a
snapshot and the client invalidates all team queries, so late joins, refreshes and reconnects (facilitator included)
converge without replaying events. The **timer is server-owned**: the DB stores `section_started_at`; clients render
`elapsed = now + (server_now - receipt time) - section_started_at`, and overrun never auto-advances. `advance` takes
the section the client saw, so a double-click can't skip a section. *Why not state over the socket / CRDTs:* one
writer (the facilitator) and one code path for validation and authz. *Cost:* the hub is in-process (one API process);
scaling swaps it for `LISTEN/NOTIFY` or Redis behind the same `publish()`. **Clock:** `APP_NOW` + real time elapsed
since an anchor in the DB, so timers tick and restarts keep the timeline; tests and the report use a frozen clock.

### 4. "Make it an Issue" stays duplicate-free under concurrency
A partial unique index `(source_measurable_id, source_week_start) WHERE solved_at IS NULL` plus
`INSERT … ON CONFLICT DO NOTHING RETURNING`; if no row comes back, select the existing unsolved Issue (201 vs 200).
Concurrent requests serialize on the index: the loser waits for the winner's commit, inserts nothing and reads the
winner's row. There is no check-then-insert and no app-level lock, so it holds across processes. Once solved, the
index releases the cell and a new Issue is allowed. Rocks and To-Dos use the same pattern. Tested with concurrent
requests from two users.

### 5. Past meeting summaries never change
When the facilitator ends a meeting, the summary (Measurable statuses for the reviewed week, Issues solved, To-Dos
created, attendees, rating) is computed and stored as JSONB on the meeting row **in the same transaction** that sets
`ended_at`. `GET …/summary` serves the snapshot, so later score fixes or renames can't rewrite history (tested).
Seeded meetings are snapshotted at seed time. *Alternative:* temporal tables and recomputing "as of `ended_at`":
more general but heavier for one screen. *Cost:* the snapshot's JSON shape must be versioned if it ever changes.

### 6. Note: supporting 1,000 companies
`company_id` already roots every row and timezone is per company (the rules take `tz`). **Measured** on one API
vCPU ([docs/LOAD_TEST.md](docs/LOAD_TEST.md)): ~200 req/s at p95 < 300 ms (≈ 175 live 8-person meetings or
750–1,500 browsing users) and 4,000 WebSockets at ~58 KiB each; API CPU is the bottleneck, not Postgres. 1,000
companies × 3 teams × a weekly 90-minute meeting ≈ 110 concurrent meetings in a 40-hour week (~3× at peak), which is
beyond one process. Next steps: ~4 stateless API processes with `LISTEN/NOTIFY` (or Redis) fan-out behind the
hub's `publish()`, tenant scoping in the auth dependency (or Postgres RLS), and indexes that lead with `team_id`.
Per-tenant data is small, so one Postgres cluster goes a long way.
