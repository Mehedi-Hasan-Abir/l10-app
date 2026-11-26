# Load test (HARDENING H5)

**Purpose.** Put numbers behind `DECISIONS.md` §3 (one API process, in-process hub) and §6 (one Postgres goes a long way):
how much does **one API vCPU** serve at p95 < 300 ms, how many sockets does it hold, and what follows for a single VM?

## Setup and method
- **Host:** AMD Ryzen 7 5700X (8C/16T, 3.4 GHz base), 31.9 GB RAM, Windows 11 Pro 10.0.26300; Docker Desktop (Engine 29.6.1)
  on WSL2 (kernel 6.18.58.1), VM capped at 6 vCPUs / ~9.7 GiB. Compose project `l10load` (`docker-compose.loadtest.yml`):
  Caddy → Next.js + FastAPI (one uvicorn process) → Postgres 17, **`api` limited to `cpus: 1.0`, 1 GiB**, login limiter off;
  db/proxy/web and the load generator (k6 2.4.0) are uncapped in the same VM. Seed data. The default stack stayed up, idle.
- **Capacity** (`capacity.js`): open model (constant arrival rate), 10 s warm-up + 45 s measured per step. One arrival = one
  Team-page bundle (8 parallel GETs: me, team, scorecard, issues, todos, rocks, meetings, audit) by one of 13 seeded
  (user, team) pairs; ~8 % also write (score PUT or to-do toggle; observers never write). Pass: p95 < 300 ms, errors < 1 %,
  no dropped arrivals. `run.sh` raises the rate and stops after two failing steps.
- **WebSockets** (`websockets.js`): N sockets (cookie + `Origin`), opened at 50/s, held 45 s; every 3 s a writer PUTs a score in
  each of the 3 teams on a fixed clock grid. Delivery latency = socket receives `invalidate` − grid tick (whole path, including
  the PUT's commit). API restarted per size for a common memory baseline. `docker stats` and cgroup CFS counters sampled throughout.

## Results (last full run; raw k6 JSON, logs and samples in `loadtest/results/`)
| bundles/s | req/s | browse p50 / p95 / p99 ms | write p95 | api CPU % | db CPU % | api throttled | |
|---|---|---|---|---|---|---|---|
| 5 | 41 | 22 / 36 / 50 | 14 | 21 | 14 | 3 % | pass |
| 10 | 81 | 22 / 38 / 56 | 14 | 40 | 28 | 4 % | pass |
| 15 | 121 | 24 / 81 / 232 | 84 | 59 | 43 | 12 % | pass |
| 20 | 159 | 29 / **629** / 1556 | 366 | 80 | 46 | 29 % | **fail (transient)** |
| 25 | 202 | 26 / 156 / 241 | 193 | 88 | 62 | 25 % | pass |
| **27** | **218** | 31 / **169** / 270 | 177 | 93 | 61 | 34 % | **pass** |
| 30 | 241 | 187 / 576 / 948 | 449 | 96 | 27 | 56 % | fail |
| 35 | 214 | 1291 / 3170 / 4910 | 3252 | 96 | 17 | 49 % | fail (collapse) |

Errors 0.00 % everywhere (overload shows as queueing, not 5xx); proxy ≤ 10 %, k6 ≤ 11 %, web ~1 % of a core.
**The sustained knee is ~27 bundles/s ≈ 218 req/s (p95 169 ms).** At 30 the API is pinned (96 %, throttled in 56 % of CFS
periods); at 35 throughput falls to 23.8 bundles/s (no load shedding). The step-20 failure is a transient: 25 and 27
passed at higher load and 20 passed in three earlier ladders (p95 89-108 ms); cause not diagnosed (api at 94 % with the db
at ~25 % for the first seconds of the window). Over four ladders: 25 passed 4/4 (p95 138-167 ms), 27 2/2, 30 only 1/4
(237 ms vs 305-576), 35 collapsed 4/4. A Leadership-only run (largest team) matched the mix. Treat **25-27 as capacity, 30 as the edge**.

| sockets | delivered | delivery p50 / p95 / p99 / max ms | writer PUT p95 | api CPU % avg (max) | api MiB (idle 67) |
|---|---|---|---|---|---|
| 100 | 100 % | 19 / 26 / 59 / 59 | 30 | 6 (18) | 76 |
| 500 | 100 % | 31 / 99 / 119 / 122 | 110 | 7 (20) | 106 |
| 1,000 | 100 % | 47 / 82 / 186 / 191 | 175 | 9 (21) | 124 |
| 2,000 | 100 % | 73 / **262** / 305 / 316 | 319 | 12 (31) | 185 |
| 4,000 | 100 % | 169 / **604** / 783 / 817 | 802 | 22 (69) | 294 |

Every socket connected (open p95 ≤ 10 ms), none dropped; **≈ 58 KiB per socket**. Latency grows with sockets *per team* (here
hundreds to 1,500); real teams have ~8-15, so the 100-socket row is the realistic one. Not tested above 4,000.

**Bottleneck: API CPU.** ≈ 4.3 ms of API CPU per request; at 30 the db falls to 27 % because it waits on the API. Postgres is
next (≈ 3 ms/request, 0.6 core at the knee). Nothing was profiled; the per-request path (auth + queries) is the lever.

## Capacity translation
**Assumptions.** *Capacity* = 25 bundles/s ≈ 200 req/s; *planned* = 60 % of it (15 bundles/s ≈ 120 req/s), since the curve is a
cliff. *Browsing user*: Team page open, one bundle per 30 s (busy) to 60 s (relaxed). *Meeting*: 8 attendees, one mutation
per 15 s; each causes 1 write + 8 clients × 2 refetches (changed resource + audit, per `use-team-channel.ts`) = 17 requests
≈ 1.1 req/s; the timer is client-side. These are assumptions, not measurements.

| per API vCPU | at capacity | planned |
|---|---|---|
| browsing users, 30 s / 60 s per bundle | ~750 / ~1,500 | ~450 / ~900 |
| concurrent meetings (8 attendees) | ~175 | ~105 |

Mixed: 50 meetings + 300 browsing users (45 s) ≈ 110 req/s, under the planned line.
**Single VM.** One process means a bigger VM does not raise this ceiling. API + db need ~1.6 vCPU at capacity and ~1.1 at planned
load, so **2 vCPUs is the minimum**, 4 comfortable; memory is a non-issue (~100 MiB + 58 KiB/socket). For §6 (1,000 companies
× 3 teams like the seed, a weekly 90-minute meeting each in a 40-hour week ≈ 110 concurrent, ~340 at a 3× peak) one process
is not enough: the next step is the cross-process fan-out in §3 (`LISTEN/NOTIFY`) with ~4 API processes, since `--workers`
cannot be added while the hub is in-process. Postgres would then serve ~500 req/s (≈ 1.5 cores at ~3 ms/request).

## Caveats
- Docker Desktop/WSL2 is not a Linux VM (CPU quota and scheduling differ; Windows shares the host). One 45 s run per step: ±20 %.
- Load generator and system share one VM. Tiny seeded data (3 teams, 83 scores, 20 to-dos) is fully cached; real tables are
  larger, so per-request cost will be higher. 13 users, writes hit the same rows.
- The bundle is API-only (no `web`, TLS or static assets). Not tested: active-meeting `meeting` messages, a reconnect storm
  after an API restart, soak/leaks. The earlier ladders cited above were run with the same scripts; their raw files were replaced.

**Rerun:** `bash loadtest/run.sh [capacity|ws]` (~30 min; env `STEPS`, `SOCKET_STEPS`, `HOLD`, `TEAMS_ONLY`). It builds and seeds the
isolated stack, replaces `loadtest/results/`, and tears down with `down -v`.
