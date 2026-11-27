# L10: Scorecard, Rocks, To-Dos, Issues and a live Level 10 Meeting

Each team tracks weekly Measurables, quarterly Rocks, To-Dos and Issues, and runs its weekly meeting in sync across
browsers. Stack: Next.js 16 + TypeScript + shadcn/ui + Tailwind · FastAPI · Postgres 17 (Alembic) · Caddy.
Design rationale: **[DECISIONS.md](DECISIONS.md)**. Full build spec: [docs/PLAN.md](docs/PLAN.md).
**Demo video (1:31, captioned):** [docs/demo/l10-demo.mp4](docs/demo/l10-demo.mp4): two browsers in one live meeting,
recorded by a script (`bash demo/record.sh`, see [demo/README.md](demo/README.md)).

## Run it
```bash
docker compose up --build        # → http://localhost:3000   (API docs: http://localhost:3000/api/docs)
```
Migrations and seed run on start. The seed is idempotent: it inserts missing seed rows and never overwrites your
data. Reset to the seed with `docker compose exec api python -m app.seed --reset`. Sign in by picking a seeded user
(no passwords). `APP_NOW=… docker compose up` runs the app at another business date; `APP_PORT` changes the port
(the stack uses host ports 3000 and 15432).

## Test: one command
```bash
bash scripts/test.sh
```
Builds an isolated compose project (`-p l10test`, no host ports, own volume, always torn down) and runs:
**ruff**, **pytest** (rules tests, API tests, report-vs-answer-key tests) and the **Playwright** two-browser
test against the full stack. It prints a pass/fail summary and exits non-zero on failure.
- **Rules** (`backend/tests/rules`, ~290 of the ~830 backend tests): pure functions, every rule in brief §2.6 with its
  boundaries (DST week ends, exactly 90%, due-today, 12.5 → 13, 8.25 → 8.3, None-priority last…).
- **API** (`backend/tests/api`): cross-team reads refused, every Observer write refused, concurrent Make it an
  Issue returns one Issue, the meeting state machine, frozen summaries, WebSocket sync, DB constraints.
- **E2E** (`e2e/tests/meeting-sync.spec.ts`): facilitator and teammate in two browser contexts. Start → join →
  Make it an Issue → advance (the second browser follows within 2 s) → rate → end → both see the summary.
- **Fixtures and clock strategy:** API tests run against a real Postgres database (`l10_test`), truncated and reseeded
  before *each* test (about 100 rows, a few ms), so every test starts from the brief's seed. The app under test is
  built with an injected `ManualClock` frozen at `2026-11-11T15:00:00Z`; meeting-timer tests call `clock.advance()`.
  Business code never reads the system clock. E2E runs on a freshly reseeded stack.
- **Failure artifact:** [docs/artifacts/](docs/artifacts/README.md) has a real Playwright trace from a deliberately
  failing run (`E2E_DEMO_FAILURE=1`).

## Rules report
```bash
bash scripts/report.sh > rules_report.json                  # = docker compose exec api python -m app.report
bash scripts/report.sh --now 2026-11-18T15:00:00Z           # another date (or: docker compose exec -e APP_NOW=… api …)
```
It seeds a fresh database **inside a transaction that is rolled back**, so the output never depends on data entered
while testing. It uses the same `services/` + `rules/` code as the API. `rules_report.json` is committed. As a
differential check, `tools/oracle_report.py` is a deliberately separate implementation computed straight from
`seed.json`. The report matches it at 9 dates (default, both sides of Sunday/Monday midnight, DST, quarter end,
next year), and this is enforced in pytest.

## Hardening beyond the brief ([docs/HARDENING.md](docs/HARDENING.md))
Each item is additive and has its own tests; none touches the business rules or the API contract.
- **Audit trail** (a bonus item): an append-only `audit_events` table (a DB trigger rejects UPDATE/DELETE), written
  in the same transaction as each change. It's served by `GET /api/teams/{id}/audit` and shown in a live
  "Activity" card.
- **Security:** CSP and other security headers set in Caddy; the app refuses to start in `APP_ENV=production` with
  the dev secret or an insecure cookie; login is rate-limited (429 + `Retry-After`).
- **Operations:** JSON logs with a request ID on every line (`X-Request-ID`); `/api/ready` checks the DB and is
  the container healthcheck; Sentry turns on when `SENTRY_DSN` is set.
- **CI:** `.github/workflows/ci.yml` runs the frontend checks, `scripts/test.sh`, `pip-audit`/`npm audit` and a Trivy
  image scan; Dependabot keeps dependencies current. **Load test:** see [docs/LOAD_TEST.md](docs/LOAD_TEST.md).

## Assumptions (calls I made where the brief was open)
- **Clock:** business time = `APP_NOW` + real time elapsed since an anchor stored in the DB. Without that, the
  meeting timer couldn't tick. Restarts keep the same timeline; tests and the report use a frozen clock.
- The report treats the data as-is at any `APP_NOW` (it doesn't hide records timestamped after it). The current
  week's To-Do completion uses the same rule (done before the week's end), and Rocks are the current quarter's.
- "During the meeting" (summary/recap) = the team's Issues solved and To-Dos created in `[started_at, ended_at]`.
  With one active meeting per team this equals "while it ran". The reviewed week is the week before the meeting's week.
- Eligible To-Do owners = the team's Admins and Team Members, plus all Admins (Admins act in every team).
- Non-admins get the same 403 for "not your team" and "doesn't exist" (teams and meetings), so ids can't be probed.
- Make it an Issue works on any off-track cell in the 13-week window (an empty closed week is off track). The
  title format matches the seed: "New deals closed, week of Nov 2". Scores can be edited or cleared in any
  window week. Ratings are only collected in Conclude. Observers can join (they appear as attendees) but not rate.

## Known limitations
- The realtime hub, the login rate limiter and the WebSocket presence are in-process, so the API runs as **one**
  process. The scale-out path is in DECISIONS §3/§6. Behind Docker Desktop every local browser shares one client
  IP, so the login limit (10/min) applies to the whole machine.
- Sign-in is a seeded-user switcher with a signed, HttpOnly, SameSite=Lax cookie: no passwords, SSO or CSRF token
  (mutations are JSON-only). The CSP allows `'unsafe-inline'` scripts, which Next.js hydration needs without nonces.
- The `python:3.12-slim` base image still has one fixable HIGH CVE (`libzstd1`); Trivy reports it, and it clears
  when the base image is refreshed.
- The frontend has no unit tests; it relies on strict generated API types, lint, and the E2E test.
- Layout is desktop-first (comfortable from ~1024 px wide); phones work but aren't optimized.
- Running the report against a busy live DB briefly locks tables (it truncates inside its rolled-back transaction).

## AI tools: what was used and where
Built with **Claude Code** (VS Code extension) in a planner/implementer setup that I chose up front:
- **Claude Opus 5.5 (planner, reviewer, integrator)** wrote the build spec ([docs/PLAN.md](docs/PLAN.md)), the
  project harness ([CLAUDE.md](CLAUDE.md)), the independent rules oracle and answer key (`tools/`,
  `backend/tests/fixtures/`), and [DECISIONS.md](DECISIONS.md). It reviewed every work package and ran all
  verification. When the Sonnet agents hit a usage limit, it also did the final integration: generated API types,
  the advance double-click guard, the Rock/To-Do "Make it an Issue" UI, Docker, the E2E test and `scripts/`.
- **Claude Sonnet 5.5 sub-agents** implemented the work packages in parallel, each limited to the files it owned:
  business rules + tests, DB/migrations/seed/auth, the REST API/WebSocket/report + API tests, and the frontend.
  Sonnet 5.5 also built the whole hardening round from Opus's plan in [docs/HARDENING.md](docs/HARDENING.md): CI,
  security headers and rate limiting, the audit trail, observability, polish and the k6 load test. Opus reviewed and
  re-verified each package (and made three small config edits: a SHA-pinned Trivy action, CI on `main` pushes, and
  stripping `X-Powered-By`).
- **Skills used:** `fastapi`, `shadcn`, `frontend-design`, `vercel-react-best-practices`, `webapp-testing`.
- **The harness is committed** so it can be reused: [CLAUDE.md](CLAUDE.md) (commands, invariants, definition of done),
  `.claude/agents/` (`implementer`, `reviewer`) and `.claude/skills/` (`verify`, `rules-check`, `new-endpoint`).
