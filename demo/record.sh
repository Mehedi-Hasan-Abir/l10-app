#!/usr/bin/env bash
# Records the captioned two-browser demo video of the L10 app. Fully unattended, no host ports, no effect on your
# running dev stack: it uses its own compose project (`l10demo`) with its own database volume, and always tears
# that project (and only that project) down on exit.
#
#   bash demo/record.sh              # build stack -> reseed -> record -> compose -> tear down
#   KEEP=1 bash demo/record.sh       # same, but leave the l10demo stack up afterwards (fast iteration)
#   RERUN=1 bash demo/record.sh      # reuse a running l10demo stack: reseed, rerecord, leave it up
#   DEMO_KEEP_RAW=1 ...              # keep the raw per-browser .webm files in demo/out/raw
#   DEMO_SKIP_COMPOSE=1 ...          # record only (skip the ffmpeg composition), for debugging
#
# Output (demo/out/): l10-demo.mp4, l10-demo.srt, frames/*.png
set -euo pipefail
cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1 # Git Bash: do not rewrite container paths in docker arguments

project="l10demo" # never the default project (l10-app): this script must not touch the dev stack
keep="${KEEP:-0}"
rerun="${RERUN:-0}"
if [ "$rerun" = "1" ]; then keep=1; fi

compose() { docker compose -p "$project" -f docker-compose.yml -f docker-compose.demo.yml --profile demo "$@"; }
cleanup() {
  if [ "$keep" = "1" ]; then
    echo "==> leaving the $project stack up (KEEP/RERUN). Remove it with:"
    echo "    docker compose -p $project -f docker-compose.yml -f docker-compose.demo.yml --profile demo down -v --remove-orphans"
  else
    echo "==> tearing down the $project stack"
    compose down -v --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

mkdir -p demo/out
rm -rf demo/out/review demo/out/frames demo/out/l10-demo.mp4 demo/out/l10-demo.srt demo/out/raw

if [ "$rerun" = "1" ]; then
  if [ -z "$(compose ps --status running -q api)" ]; then
    echo "RERUN=1 needs a running $project stack (run once with KEEP=1 first)" >&2
    exit 1
  fi
else
  echo "==> building and starting the isolated stack (project $project)"
  compose up -d --build --wait db api web proxy
fi

echo "==> reseeding the app database (the demo changes data, so every run starts from the seed)"
compose exec -T api python -m app.seed --reset

echo "==> recording (two headless browsers, then ffmpeg)"
compose run --rm --build demo

echo
echo "==> done"
ls -lh demo/out/l10-demo.mp4 demo/out/l10-demo.srt
ls demo/out/frames
