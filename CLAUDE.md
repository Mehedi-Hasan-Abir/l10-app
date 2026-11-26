# CLAUDE.md: L10 App

Level 10 meeting app for EOS-style teams (Scorecard, Rocks, To-Dos, Issues + a live weekly meeting).
**Read `docs/PLAN.md` before changing anything.** It holds the data model, API contract, realtime protocol,
rules, and work-package file ownership. The assessment brief is in `docs/_private/brief.md`.

## Commands

Local dev on this machine (Windows; PowerShell or Git Bash; use forward slashes in paths):

```bash
docker compose up -d db                         # Postgres on localhost:15432 (dbs: l10, l10_test)

cd backend
uv run alembic upgrade head                     # migrate dev DB
uv run python -m app.seed --reset               # load seed/seed.json (rerun without --reset is a no-op)
uv run uvicorn app.main:app --reload --port 8000
uv run pytest -q                                # rules + API tests (uses l10_test)
uv run ruff check . && uv run ruff format --check .
uv run python -m app.report                     # rules report (fresh seed in a rolled-back txn)
uv run python -m app.report --now 2026-11-18T15:00:00Z

cd frontend
npm run dev                                     # http://localhost:3000, proxies /api to :8000
npm run gen:api                                 # regenerate src/lib/api/schema.d.ts from openapi.json
npm run lint && npx tsc --noEmit && npm run build

bash scripts/test.sh                            # FULL unattended suite in docker (pytest + Playwright)
bash scripts/report.sh [--now ISO]              # rules report from the running docker stack
cd e2e && E2E_BASE_URL=http://localhost:3000 npx playwright test   # E2E against a running stack
docker compose up --build                       # whole stack at http://localhost:3000 (API docs /api/docs)
```

Oracle check (the report must match an independent implementation):
```bash
uv run --no-project --with tzdata python tools/oracle_report.py --now 2026-11-11T15:00:00Z
```

Ports: 5358–5457 and 8080 are unavailable on this machine. Use 15432 (db), 8000 (api), 3000 (web/proxy).

## Invariants (never break these)

- **Business time comes from the injected `Clock`**, never `datetime.now()`/`Date.now()` for business logic.
  Default `APP_NOW=2026-11-11T15:00:00Z`. The frontend uses `server_now` offsets only for the meeting timer.
- **All business math lives in `backend/app/rules/`** as pure functions. Services and the report call it, never re-implement it.
- Weeks are Monday-named, company time `America/Chicago`; week end is the exclusive instant next Monday 00:00
  local, converted per week (DST!). Due dates are local calendar dates.
- **Round half up with `Decimal`**. Never `round()` and never float arithmetic for percentages, ratings, or score comparisons.
- **Authorization is server-side** via `auth.py` dependencies on every route. Query nested resources with `team_id`.
  Non-admins get 403 `team_forbidden` for inaccessible *and* nonexistent teams and meetings.
- **Duplicate-free "Make it an Issue"** relies on the partial unique index + `INSERT … ON CONFLICT DO NOTHING`,
  then select the existing row. Never "check then insert".
- **Publish realtime events after commit.** Postgres owns meeting state; the WebSocket only notifies.
- Keep seed ids. New ids come from `app/ids.py`.
- Migrations are append-only: never edit a migration that has been applied in a shared environment; add a new one.

## Conventions

- A PostToolUse hook (`.claude/settings.json` → `.claude/hooks/format-edited-file.sh`) formats every edited file:
  ruff for `backend/**/*.py`, Prettier (120 cols) for `frontend/src/**/*.ts(x)`. Don't fight it; don't hand-format.

- Python: async SQLAlchemy 2.0 style (`select()`), Pydantic v2 models in `schemas.py` with `extra="forbid"` on
  requests, typed everywhere, ruff-formatted, line length 110. Errors via `app/errors.py` helpers →
  `{"detail": {"code", "message"}}`.
- Routers stay thin: parse → authorize (dependency) → call service → return schema.
- TypeScript strict, no `any`. Server state only in TanStack Query; query keys in `src/lib/api/keys.ts`.
  Forms: react-hook-form + zod. UI from shadcn/ui components in `src/components/ui`.
- Tests: rules tests are pure. API tests run against real Postgres (`l10_test`), each test starts from a fresh seed,
  and the clock is frozen at the default `APP_NOW`.

## Definition of done (run before you say you're finished)

1. `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q` is green.
2. Rules report equals `backend/tests/fixtures/expected_rules_report.json` (a pytest test asserts this).
3. Frontend: `npm run lint && npx tsc --noEmit && npm run build` is green.
4. You touched only the files your work package owns (`docs/PLAN.md` §12), or you said why.
5. Report what you verified and what you didn't. Don't claim a check passed if you didn't run it.
