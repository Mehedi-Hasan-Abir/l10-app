/**
 * Capacity step: how many Team-page loads per second can ONE API vCPU serve with p95 < 300 ms?
 *
 * One run = one step. `loadtest/run.sh` runs this script at increasing RATEs (the "stepped" part), so every
 * step gets its own docker-stats samples and its own summary. Each run has two phases at the same rate:
 *   warmup   WARMUP seconds (default 10): fills DB pool / caches, logs the VUs in. Not measured.
 *   measure  HOLD seconds (default 45): the numbers that count (sub-metrics tagged phase:measure).
 *
 * Open model (constant-arrival-rate): RATE iterations start per second whether or not the server keeps up, so
 * a saturated server shows up as latency and errors instead of the load generator quietly slowing down.
 * One iteration = one Team-page bundle (8 GETs, see common.js) by a VU's (user, team); with probability
 * WRITE_MIX (default 0.10) the iteration also does one write, half score PUT on a current-week cell and half
 * to-do PATCH toggle (observers never write, so the realised write share is a little below 10 %).
 * There is no think time: arrivals are the load, per-user pacing is handled in docs/LOAD_TEST.md.
 *
 * Pass/fail (per step, measure phase only): p95 < 300 ms for browse GETs and for writes, error rate < 1 %,
 * and no dropped iterations (a drop means the VU pool was exhausted: the server, or k6, fell behind).
 * A step past 3 s p95 aborts early. Exit code 99 = a threshold failed.
 *
 * Env: RATE (bundles/s), WARMUP, HOLD, WRITE_MIX, TEAMS_ONLY (e.g. team_lead), plus BASE_URL etc. from compose.
 * Output: stdout summary, results/capacity-<RATE>.row (a markdown table row; run.sh assembles the table) and,
 * with `--summary-export`, the raw k6 summary JSON.
 */
import { sleep } from 'k6';
import { ENDPOINTS, discover, fmt, getSession, maybeWrite, num, stat, teamPageBundle } from './common.js';

const RATE = num(__ENV.RATE, 10);
const WARMUP_S = num(__ENV.WARMUP, 10);
const HOLD_S = num(__ENV.HOLD, 45);
const WRITE_MIX = num(__ENV.WRITE_MIX, 0.1);
const P95_LIMIT_MS = 300;
const MAX_ERROR_RATE = 0.01;

// Enough pre-allocated VUs that arrivals rotate over every (user, team) pair and a slow server does not
// starve the generator; k6 allocates more up to maxVUs if the server falls behind.
const PRE_VUS = Math.max(26, Math.ceil(RATE * 0.6));
const MAX_VUS = Math.max(400, PRE_VUS * 3);

const arrivals = (phase, startTime, seconds) => ({
  executor: 'constant-arrival-rate',
  exec: 'iteration',
  rate: RATE,
  timeUnit: '1s',
  duration: `${seconds}s`,
  startTime: `${startTime}s`,
  preAllocatedVUs: PRE_VUS,
  maxVUs: MAX_VUS,
  tags: { phase },
});

const M = 'phase:measure';
const thresholds = {
  [`http_req_duration{${M},kind:browse}`]: [
    `p(95)<${P95_LIMIT_MS}`,
    { threshold: 'p(95)<3000', abortOnFail: true, delayAbortEval: '10s' },
  ],
  [`http_req_duration{${M},kind:write}`]: [`p(95)<${P95_LIMIT_MS}`],
  [`http_req_failed{${M},kind:browse}`]: [`rate<${MAX_ERROR_RATE}`],
  [`http_req_failed{${M},kind:write}`]: [`rate<${MAX_ERROR_RATE}`],
  [`dropped_iterations{${M}}`]: ['count<1'],
  // Informational (always pass): creates the sub-metrics the summary reads.
  [`http_reqs{${M},kind:browse}`]: ['count>=0'],
  [`http_reqs{${M},kind:write}`]: ['count>=0'],
  [`bundle_ms{${M}}`]: ['p(95)>=0'],
  [`bundles{${M}}`]: ['count>=0'],
  [`writes{${M}}`]: ['count>=0'],
};
for (const endpoint of ENDPOINTS) thresholds[`http_req_duration{${M},endpoint:${endpoint}}`] = ['p(95)>=0'];

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  scenarios: {
    warmup: arrivals('warmup', 0, WARMUP_S),
    measure: arrivals('measure', WARMUP_S + 1, HOLD_S),
  },
  thresholds,
};

export function setup() {
  return discover();
}

export function iteration(setupData) {
  const s = getSession();
  if (s === null) {
    sleep(1); // login failed (counted as a failed check); do not spin
    return;
  }
  const responses = teamPageBundle(s);
  maybeWrite(s, setupData, responses, WRITE_MIX);
}

export function handleSummary(data) {
  const tm = (name) => `${name}{${M}}`;
  // An early abort (p95 > 3 s) cuts the measure phase short: rates are per second actually measured.
  const measured = Math.max(1, Math.min(HOLD_S, data.state.testRunDurationMs / 1000 - WARMUP_S - 1));
  const browseReqs = stat(data, `http_reqs{${M},kind:browse}`, 'count');
  const writeReqs = stat(data, `http_reqs{${M},kind:write}`, 'count') || 0;
  const nBundles = stat(data, tm('bundles'), 'count');
  const failedBrowse = stat(data, `http_req_failed{${M},kind:browse}`, 'rate');
  const failedWrite = stat(data, `http_req_failed{${M},kind:write}`, 'rate');
  const errReqs = (failedBrowse || 0) * browseReqs + (failedWrite || 0) * writeReqs;
  const errPct = (100 * errReqs) / (browseReqs + writeReqs);
  const dropped = stat(data, tm('dropped_iterations'), 'count') || 0;
  const br = (key) => stat(data, `http_req_duration{${M},kind:browse}`, key);
  const wr = (key) => stat(data, `http_req_duration{${M},kind:write}`, key);

  const failed = Object.entries(data.metrics)
    .filter(([name]) => name.includes(M))
    .flatMap(([name, m]) =>
      Object.entries(m.thresholds || {})
        .filter(([, t]) => !t.ok)
        .map(([expr]) => `${name} ${expr}`),
    );
  const verdict = !(nBundles > 0) ? 'NO DATA' : failed.length === 0 ? 'PASS' : 'FAIL';

  // | target | bundles/s | req/s | browse p50 | p95 | p99 | page-load p95 | write p95 | errors % | dropped | verdict |
  const row =
    `| ${RATE} | ${fmt(nBundles / measured, 1)} | ${fmt((browseReqs + writeReqs) / measured, 0)} ` +
    `| ${fmt(br('med'))} | ${fmt(br('p(95)'))} | ${fmt(br('p(99)'))} | ${fmt(stat(data, tm('bundle_ms'), 'p(95)'))} ` +
    `| ${fmt(wr('p(95)'))} | ${fmt(errPct, 2)} | ${dropped} | ${verdict} |\n`;

  const perEndpoint = ENDPOINTS.map(
    (e) =>
      `  ${e.padEnd(10)} p50 ${fmt(stat(data, `http_req_duration{${M},endpoint:${e}}`, 'med')).padStart(5)} ms   ` +
      `p95 ${fmt(stat(data, `http_req_duration{${M},endpoint:${e}}`, 'p(95)')).padStart(5)} ms`,
  ).join('\n');
  const text =
    `\ncapacity step: ${RATE} bundles/s for ${HOLD_S}s (+${WARMUP_S}s warmup), write mix ${WRITE_MIX} -> ${verdict}\n` +
    `  achieved ${fmt(nBundles / measured, 1)} bundles/s, ${fmt((browseReqs + writeReqs) / measured)} req/s ` +
    `(${writeReqs} writes), errors ${fmt(errPct, 2)} %, dropped iterations ${dropped}\n` +
    `  browse GET  p50 ${fmt(br('med'))}  p95 ${fmt(br('p(95)'))}  p99 ${fmt(br('p(99)'))} ms; ` +
    `page load (8 GETs) p95 ${fmt(stat(data, tm('bundle_ms'), 'p(95)'))} ms; write p95 ${fmt(wr('p(95)'))} ms\n` +
    `${perEndpoint}\n` +
    (failed.length ? `  failed thresholds:\n    ${failed.join('\n    ')}\n` : '');

  return {
    stdout: text,
    [`/loadtest/results/capacity-${RATE}.row`]: row,
  };
}
