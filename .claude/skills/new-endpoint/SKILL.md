---
name: new-endpoint
description: Checklist for adding or changing a REST endpoint end to end (schema, service, router, authz, realtime event, tests, OpenAPI export, frontend types and hooks).
---

1. Contract first: add or adjust the shape in `docs/PLAN.md` §6 and the Pydantic model in `backend/app/schemas.py`
   (requests `extra="forbid"`, validated ranges/lengths).
2. Service in `backend/app/services/<area>.py`: all queries filtered by `team_id`; business math via `app.rules`;
   commit, then `await hub.publish(team_id, {"type": "invalidate", "resources": [...]})`.
3. Router in `backend/app/routers/<area>.py`: authorization dependency (`TeamReader` / `TeamWriter` /
   `load_team_context`), `response_model`, `summary`, documented error responses.
4. Tests in `backend/tests/api/`: happy path, a 403 for the wrong role/team, a 422 for bad input, and the realtime
   event if one is published.
5. `cd backend && uv run python -m app.export_openapi` then `cd frontend && npm run gen:api`; add the hook to
   `src/lib/api/queries.ts` or `mutations.ts` with its query keys from `keys.ts`, and map the realtime resource in
   `src/lib/realtime/use-team-channel.ts`.
6. Run the `verify` skill.
