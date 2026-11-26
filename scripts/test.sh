#!/usr/bin/env bash
# The ONE test command. Fully unattended, against the seed data and a fixed clock:
#   1. backend lint + rules tests + API tests (pytest; frozen clock at APP_NOW, fresh seed per test)
#   2. the two-browser Playwright E2E test against the whole stack (Caddy -> Next.js + FastAPI -> Postgres)
# Runs in an isolated compose project (no host ports, its own volume) that is always torn down.
set -euo pipefail
cd "$(dirname "$0")/.."

project="${L10_TEST_PROJECT:-l10test}" # override to run several isolated suites side by side
compose() { docker compose -p "$project" -f docker-compose.yml -f docker-compose.test.yml --profile e2e "$@"; }
cleanup() { compose down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

names=("backend lint" "backend tests (rules + API)" "e2e (Playwright, two browsers)")
results=(skipped skipped skipped)
fail=0
run() { # run <index> <command...>
  local i=$1
  shift
  echo
  echo "==> ${names[$i]}"
  if "$@"; then results[$i]=pass; else results[$i]=FAIL; fail=1; fi
}

echo "==> building and starting the isolated stack (project $project)"
compose up -d --build --wait db api web proxy

run 0 compose exec -T api ruff check .
run 1 compose exec -T api pytest -q
echo
echo "==> reseeding the app database for the E2E run"
compose exec -T api python -m app.seed --reset
rm -rf e2e/test-results e2e/playwright-report
run 2 compose run --rm --build e2e

echo
echo "==> summary"
for i in 0 1 2; do printf '  %-34s %s\n' "${names[$i]}" "${results[$i]}"; done
if [ "$fail" -eq 0 ]; then
  echo "ALL GREEN"
else
  echo "FAILED (Playwright traces: e2e/test-results, HTML report: e2e/playwright-report)"
fi
exit "$fail"
