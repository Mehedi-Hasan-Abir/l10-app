/**
 * WebSocket capacity step: hold SOCKETS concurrent connections to /ws/teams/{t} and measure how long a
 * mutation takes to reach every one of them.
 *
 * One run = one socket count; `loadtest/run.sh` runs it at 100 -> 500 -> 1000 -> 2000 (restarting the API
 * between runs so memory is measured from the same idle baseline) and samples `docker stats` alongside.
 *
 * Timeline (all times derived from `t0`, taken at the end of setup):
 *   connect  SOCKETS opened evenly over ceil(SOCKETS / CONNECT_RATE) s (default 50 sockets/s), spread over
 *            LISTENER_VUS VUs (default SOCKETS/50). Each socket is one (user, team) pair of the seed, cookie
 *            from a login in setup (one per user) and `Origin: http://proxy`, exactly what a browser sends.
 *   settle   5 s with no writes.
 *   hold     HOLD s (default 45): a writer PUTs a score in EACH of the 3 teams every PERIOD_MS (default 3 s) on
 *            a fixed wall-clock grid tick0 + k * PERIOD_MS. The server answers every PUT with one
 *            `{"type":"invalidate"}` to all sockets of that team.
 *   close    sockets close themselves 3 s after the last tick.
 *
 * Delivery latency = time a socket receives an `invalidate` minus the grid tick at which the writer fired
 * that PUT, i.e. the whole path: request through Caddy, FastAPI, the DB commit, the hub fan-out, Caddy,
 * socket. (k6 VUs share one clock, so no skew; the writer's own lateness is reported separately.) A receipt is
 * attributed to its tick by `(receipt - tick0) mod PERIOD_MS`, valid while latency < PERIOD_MS; the delivered
 * count (every socket must get exactly one invalidate per tick) proves nothing was lost or attributed wrongly.
 *
 * Connect success = the `snapshot` message arrived (handshake, Origin, cookie, team access, DB read all fine).
 * Env: SOCKETS, CONNECT_RATE, HOLD, PERIOD_MS, LISTENER_VUS, TEAMS_ONLY; BASE_URL/WS_URL/ORIGIN from compose.
 * Output: stdout summary, results/ws-<SOCKETS>.row (markdown row; run.sh adds api CPU/memory) and, with
 * `--summary-export`, the raw k6 summary JSON.
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { Counter, Rate, Trend } from 'k6/metrics';
import { WebSocket } from 'k6/websockets';
import { BASE_URL, ORIGIN, PAIRS, USER_IDS, WS_URL, cookieHeader, discover, fmt, login, num, stat } from './common.js';

const SOCKETS = num(__ENV.SOCKETS, 100);
const CONNECT_RATE = num(__ENV.CONNECT_RATE, 50);
const HOLD_S = num(__ENV.HOLD, 45);
const PERIOD_MS = num(__ENV.PERIOD_MS, 3000);
const SETTLE_S = 5;
const TAIL_MS = 3000;
const LISTENER_VUS = Math.max(2, num(__ENV.LISTENER_VUS, Math.ceil(SOCKETS / 50)));

const CONNECT_S = Math.max(5, Math.ceil(SOCKETS / CONNECT_RATE));
const TICKS = Math.floor((HOLD_S * 1000) / PERIOD_MS);
const TIMELINE_S = CONNECT_S + SETTLE_S + HOLD_S + TAIL_MS / 1000;

const connectOk = new Rate('ws_connect_ok');
const openMs = new Trend('ws_open_ms', true); // new WebSocket() -> 'open' (handshake incl. Caddy)
const readyMs = new Trend('ws_ready_ms', true); // new WebSocket() -> first message (the snapshot)
const deliveryMs = new Trend('ws_delivery_ms', true); // PUT fired -> invalidate received
const invalidates = new Counter('ws_invalidates');
const unexpectedClose = new Counter('ws_unexpected_close');
const putMs = new Trend('ws_put_ms', true); // writer PUT response time (includes the fan-out, it is awaited)
const putLateMs = new Trend('ws_put_late_ms', true); // how late the writer fired vs. its grid tick
const putFailed = new Counter('ws_put_failed');

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  scenarios: {
    listeners: {
      executor: 'per-vu-iterations',
      exec: 'listen',
      vus: LISTENER_VUS,
      iterations: 1,
      maxDuration: `${Math.ceil(TIMELINE_S) + 60}s`,
    },
    writer: {
      executor: 'per-vu-iterations',
      exec: 'write',
      vus: 1,
      iterations: 1,
      maxDuration: `${Math.ceil(TIMELINE_S) + 60}s`,
    },
  },
  thresholds: {
    ws_connect_ok: ['rate>0.99'],
    ws_unexpected_close: ['count<1'],
    ws_delivery_ms: ['p(95)<1000'],
  },
};

export function setup() {
  const found = discover();
  const cookies = {};
  for (const id of USER_IDS) {
    cookies[id] = login(id, { kind: 'setup', endpoint: 'login' });
    if (cookies[id] === null) throw new Error(`setup: login as ${id} failed`);
  }
  const t0 = Date.now() + 1000; // the scenarios start within a second of setup returning
  const tick0 = t0 + (CONNECT_S + SETTLE_S) * 1000;
  return { ...found, cookies, t0, tick0, tEnd: tick0 + TICKS * PERIOD_MS + TAIL_MS };
}

function openSocket(j, d) {
  const pair = PAIRS[j % PAIRS.length];
  const started = Date.now();
  let ready = false;
  let failed = false;
  let closing = false;
  const ws = new WebSocket(`${WS_URL}/ws/teams/${pair.team}`, null, {
    headers: { ...cookieHeader(d.cookies[pair.user]), Origin: ORIGIN },
  });
  const fail = () => {
    if (!ready && !failed) {
      failed = true;
      connectOk.add(0);
    }
  };
  ws.onopen = () => openMs.add(Date.now() - started);
  ws.onmessage = (event) => {
    const now = Date.now();
    const data = event.data;
    if (!ready) {
      if (failed || data.indexOf('"snapshot"') < 0) return;
      ready = true;
      connectOk.add(1);
      readyMs.add(now - started);
    } else if (data.indexOf('"invalidate"') >= 0 && now >= d.tick0) {
      invalidates.add(1);
      deliveryMs.add((now - d.tick0) % PERIOD_MS, { team: pair.team });
    }
  };
  ws.onerror = fail;
  ws.onclose = () => {
    if (!ready) fail();
    else if (!closing) unexpectedClose.add(1);
  };
  setTimeout(() => {
    closing = true;
    ws.close();
  }, Math.max(0, d.tEnd - Date.now()));
}

export function listen(d) {
  const v = exec.scenario.iterationInTest; // 0..LISTENER_VUS-1: one iteration per VU
  for (let j = v; j < SOCKETS; j += LISTENER_VUS) {
    const at = d.t0 + Math.floor((j / SOCKETS) * CONNECT_S * 1000); // spread the connects evenly
    setTimeout(() => openSocket(j, d), Math.max(0, at - Date.now()));
  }
  // The iteration lasts until every socket is closed (open sockets and timers keep k6's event loop alive).
}

export function write(d) {
  const headers = { 'Content-Type': 'application/json', Origin: ORIGIN, ...cookieHeader(d.cookies.u1) };
  const teams = [...new Set(PAIRS.map((p) => p.team))];
  for (let k = 0; k < TICKS; k++) {
    const tick = d.tick0 + k * PERIOD_MS;
    const wait = tick - Date.now();
    if (wait > 0) sleep(wait / 1000);
    putLateMs.add(Math.max(0, Date.now() - tick));
    const responses = http.batch(
      teams.map((team) => ({
        method: 'PUT',
        url: `${BASE_URL}/api/teams/${team}/measurables/${d.measurables[team][0]}/scores/${d.currentWeek}`,
        body: JSON.stringify({ value: 1000 + k }), // a different value every tick: a real change, an audit row
        params: { headers, tags: { kind: 'ws_write', endpoint: 'score_put' } },
      })),
    );
    for (const r of responses) {
      putMs.add(r.timings.duration);
      if (!check(r, { 'PUT 200': (x) => x.status === 200 })) putFailed.add(1);
    }
  }
}

export function handleSummary(data) {
  const connected = stat(data, 'ws_connect_ok', 'passes');
  const attempted = connected + (stat(data, 'ws_connect_ok', 'fails') || 0);
  const delivered = stat(data, 'ws_invalidates', 'count') || 0;
  const expected = connected * TICKS;
  const unexpected = stat(data, 'ws_unexpected_close', 'count') || 0;
  const d = (key) => stat(data, 'ws_delivery_ms', key);

  // | sockets | connected | open p95 | ready p95 | delivered | delivery p50 | p95 | p99 | max | PUT p95 | closed early |
  const row =
    `| ${SOCKETS} | ${fmt(connected)}/${fmt(attempted)} | ${fmt(stat(data, 'ws_open_ms', 'p(95)'))} ` +
    `| ${fmt(stat(data, 'ws_ready_ms', 'p(95)'))} | ${fmt((100 * delivered) / expected, 2)} % ` +
    `| ${fmt(d('med'))} | ${fmt(d('p(95)'))} | ${fmt(d('p(99)'))} | ${fmt(d('max'))} ` +
    `| ${fmt(stat(data, 'ws_put_ms', 'p(95)'))} | ${unexpected} |\n`;
  const text =
    `\nwebsocket step: ${SOCKETS} sockets (${LISTENER_VUS} VUs), ${TICKS} writes x 3 teams, one every ${PERIOD_MS} ms\n` +
    `  connected ${fmt(connected)}/${fmt(attempted)}, closed unexpectedly ${unexpected}, ` +
    `invalidates delivered ${fmt(delivered)}/${fmt(expected)} (${fmt((100 * delivered) / expected, 2)} %)\n` +
    `  connect: open p50 ${fmt(stat(data, 'ws_open_ms', 'med'))} p95 ${fmt(stat(data, 'ws_open_ms', 'p(95)'))} ms; ` +
    `snapshot ready p50 ${fmt(stat(data, 'ws_ready_ms', 'med'))} p95 ${fmt(stat(data, 'ws_ready_ms', 'p(95)'))} ` +
    `max ${fmt(stat(data, 'ws_ready_ms', 'max'))} ms\n` +
    `  delivery (PUT fired -> invalidate received): p50 ${fmt(d('med'))} p90 ${fmt(d('p(90)'))} ` +
    `p95 ${fmt(d('p(95)'))} p99 ${fmt(d('p(99)'))} max ${fmt(d('max'))} ms\n` +
    `  writer: PUT response p50 ${fmt(stat(data, 'ws_put_ms', 'med'))} p95 ${fmt(stat(data, 'ws_put_ms', 'p(95)'))} ms, ` +
    `fired late by p95 ${fmt(stat(data, 'ws_put_late_ms', 'p(95)'))} ms, failed ${stat(data, 'ws_put_failed', 'count') || 0}\n`;
  return { stdout: text, [`/loadtest/results/ws-${SOCKETS}.row`]: row };
}
