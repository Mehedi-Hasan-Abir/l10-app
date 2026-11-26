#!/usr/bin/env bash
# The ONE load-test command (HARDENING H5; method and results in docs/LOAD_TEST.md):
#
#   bash loadtest/run.sh              # capacity steps + WebSocket steps (about 15-20 minutes)
#   bash loadtest/run.sh capacity     # only the HTTP capacity steps
#   bash loadtest/run.sh ws           # only the WebSocket steps
#
# What it does
#   1. Brings up an isolated copy of the stack as compose project `l10load` (own network and DB volume, no host
#      ports, API capped at 1 vCPU / 1 GiB) with `up --build --wait`, then reseeds. The default project `l10-app`
#      (port 3000) is never touched. A trap tears the stack down with `down -v` on every exit path.
#   2. capacity: runs loadtest/capacity.js at increasing Team-page rates (STEPS, bundles/s), one k6 run per
#      step, with `docker stats` sampled in the background; stops after two consecutive failing steps.
#   3. ws: for each socket count (SOCKET_STEPS) restarts the API (same idle memory baseline every time) and runs
#      loadtest/websockets.js with `docker stats` sampled alongside.
#   4. Leaves everything in loadtest/results/: k6 `--summary-export` JSON (capacity-<rate>.json, ws-<n>.json), the
#      k6 console logs, stats-*.txt (timestamp, container, CPU %, memory: one line per container per sample),
#      cgroup-*.txt (API CPU quota counters before/after each step) and summary-*.md (the tables).
#
# Overridable environment: STEPS, SOCKET_STEPS, HOLD (measured seconds per step), WARMUP, WRITE_MIX, TEAMS_ONLY,
# STOP_AFTER_FAILS, L10_LOAD_PROJECT. Needs Docker with compose v2 and internet access for the k6 image.
set -euo pipefail
cd "$(dirname "$0")/.."

export MSYS_NO_PATHCONV=1 # Git Bash on Windows must not rewrite container paths such as /loadtest/...

mode="${1:-all}"
case "$mode" in all | capacity | ws) ;; *) echo "usage: bash loadtest/run.sh [all|capacity|ws]" >&2; exit 2 ;; esac

PROJECT="${L10_LOAD_PROJECT:-l10load}"
if [ "$PROJECT" = "l10-app" ]; then
  echo "refusing to use the default compose project (l10-app) for a load test" >&2
  exit 2
fi
RESULTS=loadtest/results
STEPS="${STEPS:-5 10 15 20 25 27 30 35 40 50 60}"           # Team-page bundles per second (8 GETs each)
SOCKET_STEPS="${SOCKET_STEPS:-100 500 1000 2000 4000}"        # concurrent WebSockets
HOLD="${HOLD:-45}"                                       # measured seconds per step
WARMUP="${WARMUP:-10}"                                   # unmeasured seconds before each capacity step
WRITE_MIX="${WRITE_MIX:-0.10}"
TEAMS_ONLY="${TEAMS_ONLY:-}"
STOP_AFTER_FAILS="${STOP_AFTER_FAILS:-2}"
API="${PROJECT}-api-1"

compose() { docker compose -p "$PROJECT" -f docker-compose.yml -f docker-compose.loadtest.yml --profile loadtest "$@"; }
k6() { compose run --rm --no-deps -T k6 run --quiet --no-color "$@" 2>&1 | grep --line-buffered -v '^ Container '; }
log() { printf '\n==> %s\n' "$*"; }

# --- background `docker stats` sampler ---------------------------------------------------------------------
STATS_PID=""
start_stats() { # start_stats <file>: "<epoch> <container> <cpu%> <mem used> / <mem limit>" per container per sample
  local out=$1
  : >"$out"
  (
    while :; do
      ts=$(date +%s)
      ids=$(docker ps -q --filter "name=${PROJECT}-")
      # shellcheck disable=SC2086
      [ -n "$ids" ] && docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' $ids 2>/dev/null | sed "s/^/$ts /" >>"$out"
    done
  ) &
  STATS_PID=$!
}
stop_stats() {
  if [ -n "$STATS_PID" ]; then
    kill "$STATS_PID" 2>/dev/null || true
    wait "$STATS_PID" 2>/dev/null || true
    STATS_PID=""
  fi
}

# API cgroup CPU counters (cgroup v2): usage_usec, nr_periods, nr_throttled, throttled_usec.
cgroup_snapshot() { docker exec "$API" cat /sys/fs/cgroup/cpu.stat 2>/dev/null | awk '{ printf "%s=%s ", $1, $2 } END { print "" }'; }
# throttled periods as a percentage of all CFS periods between two snapshots (is the 1 vCPU quota binding?)
throttle_pct() { # throttle_pct "<before>" "<after>"
  awk -v a="$1" -v b="$2" 'function val(s, k,   n, i, p) { n = split(s, p, " "); for (i = 1; i <= n; i++) if (index(p[i], k "=") == 1) return substr(p[i], length(k) + 2) + 0; return 0 }
    BEGIN { dp = val(b, "nr_periods") - val(a, "nr_periods"); dt = val(b, "nr_throttled") - val(a, "nr_throttled"); printf "%.0f", (dp > 0 ? 100 * dt / dp : 0) }'
}

# Average / max over the last <window> seconds that a k6 container was alive (the measured part of the run).
# Prints: api_cpu_avg api_cpu_max db_cpu_avg proxy_cpu_avg web_cpu_avg k6_cpu_avg api_mem_idle_mib api_mem_max_mib
stats_line() { # stats_line <file> <window seconds>
  awk -v win="$2" '
    function mib(s,   n, u) { n = s + 0; u = s; sub(/^[0-9.]+/, "", u); if (u == "GiB") return n * 1024; if (u == "KiB") return n / 1024; if (u == "B") return n / 1048576; return n }
    { t[NR] = $1; k[NR] = $2; c[NR] = $3 + 0; m[NR] = mib($4)
      if (k[NR] ~ /-k6-/ && $1 > last) last = $1
      if (first == "" || $1 < first) first = $1 }
    END {
      for (i = 1; i <= NR; i++) {
        key = k[i]; sub(/^.*load-/, "", key); sub(/-1$/, "", key); if (key ~ /^k6-/) key = "k6"
        if (key == "api" && t[i] == first) idle = m[i]
        if (t[i] >= last - win && t[i] <= last) {
          n[key]++; cs[key] += c[i]; if (c[i] > cm[key]) cm[key] = c[i]; if (m[i] > mm[key]) mm[key] = m[i]
        }
      }
      printf "%.0f %.0f %.0f %.0f %.0f %.0f %.0f %.0f\n", cs["api"] / (n["api"] ? n["api"] : 1), cm["api"], cs["db"] / (n["db"] ? n["db"] : 1), cs["proxy"] / (n["proxy"] ? n["proxy"] : 1), cs["web"] / (n["web"] ? n["web"] : 1), cs["k6"] / (n["k6"] ? n["k6"] : 1), idle, mm["api"]
    }' "$1"
}

# Reseed between phases. `seed --reset` TRUNCATEs, which can deadlock with requests an overloaded API is still
# working through after an aborted step, so wait for them to drain and retry.
reseed() { # reseed [seconds to wait first]
  local attempt out
  sleep "${1:-0}"
  for attempt in 1 2 3 4 5; do
    if out=$(compose exec -T api python -m app.seed --reset 2>&1); then return 0; fi
    echo "reseed attempt $attempt failed; retrying in 5 s" >&2
    sleep 5
  done
  echo "$out" | tail -5 >&2
  return 1
}

cleanup() {
  stop_stats
  log "tearing down project $PROJECT (down -v)"
  compose down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# --- bring-up ------------------------------------------------------------------------------------------------
mkdir -p "$RESULTS"
chmod a+rwx "$RESULTS" 2>/dev/null || true # the k6 image runs as a non-root user
find "$RESULTS" -type f ! -name .gitkeep -delete

log "starting from a clean state: removing any previous $PROJECT stack"
compose down -v --remove-orphans >/dev/null 2>&1 || true

log "building and starting the isolated stack (project $PROJECT; API limited to 1 vCPU / 1 GiB)"
compose pull -q k6 || true
compose up -d --build --wait db api web proxy
reseed
{
  echo "date:        $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "docker:      $(docker version --format '{{.Server.Version}}')"
  docker info --format 'docker host:  {{.OperatingSystem}}, kernel {{.KernelVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes RAM' 2>/dev/null || true
  echo "api limits:  $(docker inspect "$API" --format 'nano_cpus={{.HostConfig.NanoCpus}} memory={{.HostConfig.Memory}}')"
  echo "k6 image:    $(grep -o 'grafana/k6:[^ ]*' docker-compose.loadtest.yml | head -1)"
  echo "settings:    STEPS='$STEPS' SOCKET_STEPS='$SOCKET_STEPS' HOLD=$HOLD WARMUP=$WARMUP WRITE_MIX=$WRITE_MIX TEAMS_ONLY='$TEAMS_ONLY'"
} | tee "$RESULTS/environment.txt"

# --- capacity ------------------------------------------------------------------------------------------------
run_capacity() {
  log "capacity: Team-page bundles at increasing rates (STEPS=$STEPS), $WARMUP s warmup + $HOLD s measured each"
  reseed || return 1
  local lat="$RESULTS/summary-capacity.md" cpu fails=0 rc rate before after
  cpu="$RESULTS/.cpu-rows"
  : >"$cpu"
  {
    echo "| target bundles/s | achieved bundles/s | req/s | browse GET p50 ms | p95 ms | p99 ms | page load p95 ms | write p95 ms | errors % | dropped | verdict |"
    echo "|---|---|---|---|---|---|---|---|---|---|---|"
  } >"$lat"
  {
    echo "| target bundles/s | api CPU % avg (max) | db CPU % | proxy CPU % | web CPU % | k6 CPU % | api memory MiB (max) | api CFS-throttled periods % |"
    echo "|---|---|---|---|---|---|---|---|"
  } >>"$cpu"
  for rate in $STEPS; do
    log "capacity step: $rate bundles/s"
    start_stats "$RESULTS/stats-capacity-$rate.txt"
    before=$(cgroup_snapshot)
    rc=0
    k6 -e "RATE=$rate" -e "HOLD=$HOLD" -e "WARMUP=$WARMUP" -e "WRITE_MIX=$WRITE_MIX" -e "TEAMS_ONLY=$TEAMS_ONLY" \
      --summary-export "/loadtest/results/capacity-$rate.json" /loadtest/capacity.js | tee "$RESULTS/capacity-$rate.log" || rc=$?
    after=$(cgroup_snapshot)
    stop_stats
    if [ ! -s "$RESULTS/capacity-$rate.row" ]; then
      echo "k6 produced no summary for step $rate (exit $rc); see $RESULTS/capacity-$rate.log" >&2
      return 1
    fi
    cat "$RESULTS/capacity-$rate.row" >>"$lat"
    read -r a_avg a_max d_avg p_avg w_avg k_avg a_idle a_mem <<<"$(stats_line "$RESULTS/stats-capacity-$rate.txt" "$HOLD")"
    printf '| %s | %s (%s) | %s | %s | %s | %s | %s | %s |\n' "$rate" "$a_avg" "$a_max" "$d_avg" "$p_avg" "$w_avg" "$k_avg" "$a_mem" \
      "$(throttle_pct "$before" "$after")" >>"$cpu"
    echo "$before" >"$RESULTS/cgroup-capacity-$rate.txt"
    echo "$after" >>"$RESULTS/cgroup-capacity-$rate.txt"
    if [ "$rc" -ne 0 ]; then fails=$((fails + 1)); else fails=0; fi
    if [ "$fails" -ge "$STOP_AFTER_FAILS" ]; then
      echo "stopping: $fails consecutive failing steps"
      break
    fi
    sleep 10 # let the API drain before the next step
  done
  { echo; cat "$cpu"; } >>"$lat"
  rm -f "$cpu"
  log "capacity results ($lat)"
  cat "$lat"
}

# --- websockets ----------------------------------------------------------------------------------------------
run_ws() {
  log "websockets: concurrent sockets (SOCKET_STEPS=$SOCKET_STEPS), API restarted before each step"
  reseed 15 || return 1
  local tab="$RESULTS/summary-ws.md" mem="$RESULTS/.mem-rows" n rc a_avg a_max d_avg p_avg w_avg k_avg a_idle a_mem per
  : >"$mem"
  {
    echo "| sockets | connected | open p95 ms | snapshot p95 ms | delivered | delivery p50 ms | p95 ms | p99 ms | max ms | writer PUT p95 ms | closed early |"
    echo "|---|---|---|---|---|---|---|---|---|---|---|"
  } >"$tab"
  {
    echo "| sockets | api CPU % avg (max) | db CPU % | proxy CPU % | api memory idle MiB | api memory with sockets MiB (max) | KiB per socket |"
    echo "|---|---|---|---|---|---|---|"
  } >>"$mem"
  for n in $SOCKET_STEPS; do
    log "websocket step: $n sockets"
    compose restart api >/dev/null
    compose up -d --no-deps --wait api >/dev/null
    sleep 3
    start_stats "$RESULTS/stats-ws-$n.txt"
    sleep 4 # idle samples before the sockets open: the memory baseline
    rc=0
    k6 -e "SOCKETS=$n" -e "HOLD=$HOLD" -e "TEAMS_ONLY=$TEAMS_ONLY" \
      --summary-export "/loadtest/results/ws-$n.json" /loadtest/websockets.js | tee "$RESULTS/ws-$n.log" || rc=$?
    stop_stats
    if [ ! -s "$RESULTS/ws-$n.row" ]; then
      echo "k6 produced no summary for $n sockets (exit $rc); see $RESULTS/ws-$n.log" >&2
      return 1
    fi
    cat "$RESULTS/ws-$n.row" >>"$tab"
    read -r a_avg a_max d_avg p_avg w_avg k_avg a_idle a_mem <<<"$(stats_line "$RESULTS/stats-ws-$n.txt" "$((HOLD + 3))")"
    per=$(awk -v hi="$a_mem" -v lo="$a_idle" -v n="$n" 'BEGIN { printf "%.0f", (hi - lo) * 1024 / n }')
    printf '| %s | %s (%s) | %s | %s | %s | %s | %s |\n' "$n" "$a_avg" "$a_max" "$d_avg" "$p_avg" "$a_idle" "$a_mem" "$per" >>"$mem"
  done
  { echo; cat "$mem"; } >>"$tab"
  rm -f "$mem"
  log "websocket results ($tab)"
  cat "$tab"
}

status=0
if [ "$mode" = all ] || [ "$mode" = capacity ]; then run_capacity || status=1; fi
if [ "$mode" = all ] || [ "$mode" = ws ]; then run_ws || status=1; fi
log "done (exit $status). Tables: $RESULTS/summary-*.md; raw k6 summaries: $RESULTS/*.json; docker stats samples: $RESULTS/stats-*.txt"
exit "$status"
