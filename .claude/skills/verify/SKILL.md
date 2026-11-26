---
name: verify
description: Run the project's full Definition of Done (backend lint + tests, rules report vs answer key and oracle, frontend lint/types/build, and optionally the dockerized E2E suite). Use before declaring any change finished.
---

Run each step from the repo root and stop at the first failure, showing its output.

1. `docker compose up -d db` (Postgres on localhost:15432)
2. `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
3. Rules report equals the committed answer key:
   `cd backend && uv run python -m app.report > ../.verify-report.json` then compare it as parsed JSON with
   `backend/tests/fixtures/expected_rules_report.json` and with the committed `rules_report.json`. Delete the temp file.
4. `cd frontend && npm run lint && npx tsc --noEmit && npm run build`
5. If the change touched realtime, meetings, Docker or the UI flow: `bash scripts/test.sh` (full unattended suite with
   Playwright, two browsers).

Report a table of step → pass/fail with the summary line of each command. Never mark a step passed that you did not run.
