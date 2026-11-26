#!/usr/bin/env bash
# Rules report (brief section 2.6) as JSON, computed on a freshly seeded database inside a rolled-back
# transaction, so it never depends on (or changes) data entered while testing. Needs the stack up.
#   scripts/report.sh                              # at APP_NOW (default 2026-11-11T15:00:00Z)
#   scripts/report.sh --now 2026-11-18T15:00:00Z   # at another date
#   scripts/report.sh --live                       # report the live database instead of a fresh seed
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T api python -m app.report "$@"
