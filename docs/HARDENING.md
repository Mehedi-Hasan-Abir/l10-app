# Hardening plan: low-risk steps toward enterprise grade

Goal: improve security, operability and evidence **without touching the core**. The business rules
(`backend/app/rules/`), existing tables, the existing API contract and the realtime protocol stay as they are.
Every package is additive, ships its own tests, and must leave the existing suite green
(`bash scripts/test.sh`: ruff, all pytest, Playwright E2E).

Read `CLAUDE.md` and `docs/PLAN.md` first; their invariants still apply.

## Concurrency rules for agents working in parallel
- Only edit the files your package owns (table below). If you truly need another file, make the smallest edit and
  report it.
- Running `scripts/test.sh`: set your own `L10_TEST_PROJECT` (e.g. `L10_TEST_PROJECT=l10h2 bash scripts/test.sh`)
  so suites don't collide. Never stop or `down -v` the default compose project (`l10-app`) or its `db` volume.
  Host ports 3000, 8000 and 15432 belong to the default stack; ports 5358–5457 and 8080 are unavailable on this machine.
- pytest against `l10_test` is serialized by an advisory lock, so parallel runs wait rather than collide.
- Don't regenerate `frontend/openapi.json` / `schema.d.ts` unless you're H3 (the orchestrator regenerates at the end).

## Packages

| ID | Package | Owns | Done when |
|---|---|---|---|
| H1 | CI + supply-chain scanning | `.github/**` | Workflow YAML validated with actionlint (docker `rhysd/actionlint`); local dry runs of each scan command pass or have a documented, justified allowlist |
| H2 | Security headers, production config guard, login rate limit | `infra/caddy/Caddyfile`, `backend/app/config.py`, `backend/app/routers/auth.py`, `backend/app/ratelimit.py` (new), `backend/tests/api/test_security_hardening.py` (new), `backend/tests/conftest.py` (only to disable the limiter in test settings) | Headers present through Caddy; `/api/docs` still renders; prod guard + 429 tested; full suite + E2E green |
| H3 | Audit trail (brief bonus item) | `backend/alembic/versions/0002_audit_events.py`, `backend/app/models.py` (add model), `backend/app/services/audit.py` (new) + one-line calls in existing services, `backend/app/schemas.py` (add models), `backend/app/routers/audit.py` (new), `backend/app/routers/__init__.py`, `backend/tests/api/test_audit.py` (new), `frontend/src/components/audit/**` (new), `frontend/src/lib/api/{keys,queries,types}.ts` (additions), `frontend/src/components/team-page.tsx` (mount panel), `frontend/src/lib/realtime/use-team-channel.ts` (invalidate audit) | Every mutation writes exactly one event in its own transaction; no-ops/refusals write none; DB forbids UPDATE/DELETE; endpoint authz + pagination tested; panel renders; full suite + E2E green |
| H4 | Observability basics | `backend/app/observability.py` (new), `backend/app/main.py`, `backend/pyproject.toml` + `uv.lock` (sentry-sdk), `backend/Dockerfile` (healthcheck → `/api/ready`), `backend/tests/api/test_observability.py` (new) | JSON logs with request id; `X-Request-ID` echoed; `/api/ready` 200/503; Sentry off unless `SENTRY_DSN`; full suite + E2E green |
| H5 | Load test with real numbers (runs after H1–H4) | `loadtest/**` (new), `docker-compose.loadtest.yml` (new), `docs/LOAD_TEST.md` (new) | k6 results with CPU-limited API recorded; numbers feed DECISIONS §6 |

## Design decisions (fixed; implement as written)

**H1 CI** (`.github/workflows/ci.yml`, `.github/dependabot.yml`): triggers `push` + `pull_request`; `concurrency`
cancels superseded runs; least-privilege `permissions: contents: read`; actions pinned to major versions.
Jobs: (1) `frontend` on Node 24: `npm ci`, `npm run lint`, `npx tsc --noEmit`, `npm run format:check`, `npm run build`;
(2) `test`: `bash scripts/test.sh` (docker is available on `ubuntu-latest`); upload `e2e/test-results` and
`e2e/playwright-report` as artifacts `if: failure()`; (3) `audit`: `pip-audit` on `uv export --frozen --no-dev`
output (blocking), `npm audit --omit=dev --audit-level=high` in `frontend/` (blocking), Trivy image scan of the
built api and web images with `severity: CRITICAL,HIGH`, `ignore-unfixed: true`, report-only (`exit-code: 0`)
because base-image CVEs are outside our control. Dependabot: weekly for `pip` (backend), `npm` (frontend, e2e),
`docker` (backend, frontend), `github-actions`.

**H2 Security headers** (Caddy, all responses): `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`,
`Cross-Origin-Opener-Policy: same-origin`, remove `Server`. CSP for the app:
`default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;
font-src 'self' data:; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'`
(`'unsafe-inline'` scripts are required by Next.js hydration without nonces; documented trade-off). `/api/docs*`
and `/api/redoc*` get a CSP that also allows `https://cdn.jsdelivr.net` (Swagger UI assets) and
`https://fastapi.tiangolo.com` images. No HSTS (no TLS in this stack; note where it goes).
**Production guard**: `APP_ENV` = `development` (default) | `test` | `production`. In `production`, `Settings`
refuses to load if `SECRET_KEY` is the dev default or shorter than 32 chars, or `COOKIE_SECURE` is false
(pydantic `model_validator`, clear error). **Login rate limit**: in-process sliding window per client IP on
`POST /api/auth/login`, `LOGIN_RATE_LIMIT_PER_MINUTE` (default 10, `0` disables) → 429
`{"detail":{"code":"rate_limited","message":…}}` + `Retry-After`. Limiter lives on `app.state`, created lazily from
settings (no `main.py` edit). Client IP = `request.client.host` (uvicorn already trusts Caddy's
`X-Forwarded-For`). Test settings disable it; a dedicated test builds an app with limit 3.

**H3 Audit trail**: table `audit_events(id bigserial PK, team_id FK teams NOT NULL, actor_id FK users NOT NULL,
action text NOT NULL, entity_type text NOT NULL CHECK IN ('score','issue','todo','meeting'), entity_id text NOT NULL,
at timestamptz NOT NULL, data jsonb NOT NULL DEFAULT '{}')`, index `(team_id, id DESC)`, plus a trigger that
raises on `UPDATE` or `DELETE` (append-only at the DB level; `TRUNCATE` for `seed --reset` still works). Actions:
`score.set` {measurable_id, week_start, old, new}, `score.cleared`, `issue.created` {source}, `issue.priority_changed`
{old, new}, `issue.solved` {resolution_note}, `todo.created` {source_issue_id, owner_id, due_date}, `todo.done`,
`todo.reopened`, `meeting.started`, `meeting.advanced` {from, to}, `meeting.rated` {rating}, `meeting.ended`.
(Joins are not audited: noise.) Written via `audit.record(session, …)` **inside the same transaction, before the
commit** of the change, only when something changed (Make-it-an-Issue returning an existing issue, an idempotent
done toggle, and refused/failed requests write nothing). `at = clock.now()`. Endpoint
`GET /api/teams/{team_id}/audit?limit=50&before_id=` (team readers; limit 1–200) →
`{events: AuditEvent[], next_before_id: int|null}` newest first; `AuditEvent` includes `actor: UserRef`.
Realtime: no new message type; the frontend invalidates the audit query on any `invalidate` or `meeting` message.
Frontend: a compact "Activity" card on the Team page (last 20 events, "Load more"), human-readable lines
("Blake Brooks solved 'Two key accounts at risk'", relative company-time timestamps).

**H4 Observability**: stdlib `logging` with a JSON formatter (one line per record: ts, level, logger, msg,
request_id, method, path, status, duration_ms, user_id when known); a pure ASGI middleware that reads or creates
`X-Request-ID` (uuid4 hex; accept a client-supplied id only if it matches `^[A-Za-z0-9._-]{1,64}$`), stores it in a
`contextvars.ContextVar`, logs one access line per HTTP request, and echoes the header. It must not break WebSockets
(pass-through for `scope["type"] != "http"`). `GET /api/ready`: `SELECT 1` with a 2 s timeout → 200
`{"status":"ready"}` or 503 `{"status":"unavailable"}`; `/api/health` stays a liveness probe. Sentry: only if
`SENTRY_DSN` is set (`sentry-sdk[fastapi]`, `send_default_pii=False`, `traces_sample_rate` from env, default 0).
`LOG_LEVEL` env (default INFO). Uvicorn access log disabled in the Docker command only if duplicated (keep it simple).

**H5 Load test**: k6 via the `grafana/k6` image on the compose network. `docker-compose.loadtest.yml` limits `api`
to `cpus: "1.0"`, `mem_limit: 1g` and sets `LOGIN_RATE_LIMIT_PER_MINUTE=0`. Scenarios: browsing (log in as seeded
users, load the Team page's 7 GETs, think time 5–15 s), writers (score edit / to-do toggle mix), WebSocket
listeners (N idle sockets per team receiving invalidations). Ramp until p95 > 300 ms or errors > 1%; record max
sustained req/s, p50/p95/p99, socket count, CPU/memory (`docker stats`). Write `docs/LOAD_TEST.md` with the method,
the raw k6 summary, the machine spec, and caveats (Docker Desktop on Windows/WSL2 ≠ a Linux VM).
