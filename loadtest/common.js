/**
 * Shared helpers for the L10 load tests (docs/LOAD_TEST.md). Imported by capacity.js and websockets.js.
 *
 * What a "user" is here
 *   Each k6 VU is one signed-in browser tab: on its first iteration it POSTs /api/auth/login for one seeded
 *   (user, team) pair and keeps the l10_session cookie for its lifetime. Pairs come from seed/seed.json and are
 *   interleaved across the three teams: the admin u1 (any team), team members u2, u3, u4, u6, u8, u9, and the
 *   observers u5, u7 (read-only: they browse but never write).
 *
 * The Team-page bundle
 *   What the Team page fetches when it loads or when a realtime message makes it refetch: 8 GETs, issued in
 *   parallel (k6 batch; at most 6 per host, like a browser): /api/auth/me, /api/teams/{t}, scorecard, issues
 *   (unsolved), todos, rocks, active meetings, audit (limit 20). Every request carries tags `endpoint` and
 *   `kind` (browse | write | login | setup), so results can be sliced per endpoint.
 *
 * Environment (set by docker-compose.loadtest.yml): BASE_URL (http://proxy), WS_URL (ws://proxy), ORIGIN.
 */
import http from 'k6/http';
import { check, group } from 'k6';
import exec from 'k6/execution';
import { Trend, Counter } from 'k6/metrics';

export const BASE_URL = __ENV.BASE_URL || 'http://proxy';
export const WS_URL = __ENV.WS_URL || 'ws://proxy';
export const ORIGIN = __ENV.ORIGIN || BASE_URL;

export const num = (value, fallback) => (value === undefined || value === '' ? fallback : Number(value));

// Seeded users and the teams they belong to (admins act in every team).
const USERS = {
  u1: { role: 'admin', teams: ['team_lead', 'team_sales', 'team_ops'] },
  u2: { role: 'team_member', teams: ['team_lead', 'team_sales'] },
  u3: { role: 'team_member', teams: ['team_lead'] },
  u4: { role: 'team_member', teams: ['team_lead', 'team_ops'] },
  u5: { role: 'observer', teams: ['team_lead'] },
  u6: { role: 'team_member', teams: ['team_sales'] },
  u7: { role: 'observer', teams: ['team_sales'] },
  u8: { role: 'team_member', teams: ['team_ops'] },
  u9: { role: 'team_member', teams: ['team_ops'] },
};
export const USER_IDS = Object.keys(USERS);
export const TEAMS = ['team_lead', 'team_sales', 'team_ops'];

/** All (user, team) pairs, interleaved so consecutive VUs land on different teams. Optional TEAMS_ONLY env
 *  (comma list) restricts the mix, e.g. TEAMS_ONLY=team_lead for the heaviest single team. */
export const PAIRS = (() => {
  const only = (__ENV.TEAMS_ONLY || '').split(',').filter(Boolean);
  const byTeam = TEAMS.filter((t) => only.length === 0 || only.includes(t)).map((team) =>
    USER_IDS.filter((id) => USERS[id].teams.includes(team)).map((id) => ({
      user: id,
      role: USERS[id].role,
      team,
    })),
  );
  const pairs = [];
  for (let i = 0; byTeam.some((list) => i < list.length); i++) {
    for (const list of byTeam) if (i < list.length) pairs.push(list[i]);
  }
  return pairs;
})();

export const cookieHeader = (token) => ({ Cookie: `l10_session=${token}` });

// --- custom metrics ---------------------------------------------------------------------------------------
export const bundleMs = new Trend('bundle_ms', true); // wall time of the 8 parallel GETs (= page load time)
export const bundles = new Counter('bundles'); // completed bundles
export const writes = new Counter('writes'); // write requests sent (score PUT / to-do PATCH)

// --- requests ---------------------------------------------------------------------------------------------

/** POST /api/auth/login; returns the session cookie value, or null on failure. */
export function login(userId, tags = { kind: 'login', endpoint: 'login' }) {
  const res = http.post(`${BASE_URL}/api/auth/login`, JSON.stringify({ user_id: userId }), {
    headers: { 'Content-Type': 'application/json', Origin: ORIGIN },
    tags,
  });
  const ok = check(res, { 'login 200': (r) => r.status === 200 }, tags);
  const cookie = res.cookies && res.cookies.l10_session;
  return ok && cookie && cookie.length > 0 ? cookie[0].value : null;
}

let session = null; // module state is per VU in k6: this is the VU's own login
/** The calling VU's session; logs in on first use. Pair = f(VU id), so the mix is stable across runs. */
export function getSession() {
  if (session === null) {
    const pair = PAIRS[(exec.vu.idInTest - 1) % PAIRS.length];
    const token = login(pair.user);
    if (token === null) return null;
    session = { ...pair, token };
  }
  return session;
}

const BUNDLE = [
  ['me', () => '/api/auth/me'],
  ['team', (t) => `/api/teams/${t}`],
  ['scorecard', (t) => `/api/teams/${t}/scorecard`],
  ['issues', (t) => `/api/teams/${t}/issues?status=unsolved`],
  ['todos', (t) => `/api/teams/${t}/todos`],
  ['rocks', (t) => `/api/teams/${t}/rocks`],
  ['meetings', (t) => `/api/teams/${t}/meetings?status=active`],
  ['audit', (t) => `/api/teams/${t}/audit?limit=20`],
];
export const ENDPOINTS = BUNDLE.map(([name]) => name);
const TODOS_INDEX = ENDPOINTS.indexOf('todos');

/** Load the Team page once (8 parallel GETs). Returns the responses in BUNDLE order. */
export function teamPageBundle(s) {
  const headers = cookieHeader(s.token);
  const requests = BUNDLE.map(([endpoint, path]) => ({
    method: 'GET',
    url: BASE_URL + path(s.team),
    params: {
      headers,
      tags: { endpoint, kind: 'browse' },
      // Only the to-do list is parsed (to pick a to-do to toggle); other bodies are discarded.
      ...(endpoint === 'todos' ? { responseType: 'text' } : {}),
    },
  }));
  let responses;
  group('team_page_bundle', () => {
    const start = Date.now();
    responses = http.batch(requests);
    bundleMs.add(Date.now() - start);
    bundles.add(1);
    responses.forEach((r, i) => check(r, { 'status 200': (x) => x.status === 200 }, { endpoint: ENDPOINTS[i] }));
  });
  return responses;
}

/** PUT a score on a current-week cell of one of the team's measurables (a different value every time). */
export function putScore(s, setupData) {
  const measurables = setupData.measurables[s.team];
  const id = measurables[Math.floor(Math.random() * measurables.length)];
  const res = http.put(
    `${BASE_URL}/api/teams/${s.team}/measurables/${id}/scores/${setupData.currentWeek}`,
    JSON.stringify({ value: Math.floor(Math.random() * 10000) / 10 }),
    {
      headers: { 'Content-Type': 'application/json', Origin: ORIGIN, ...cookieHeader(s.token) },
      tags: { endpoint: 'score_put', kind: 'write' },
    },
  );
  check(res, { 'status 200': (r) => r.status === 200 }, { endpoint: 'score_put' });
  writes.add(1);
}

/** Flip one to-do (taken from the list this iteration just loaded) so the PATCH is a real change. */
export function toggleTodo(s, todosResponse) {
  let todos = [];
  try {
    todos = todosResponse.json('todos');
  } catch (e) {
    return;
  }
  if (!todos || todos.length === 0) return;
  const todo = todos[Math.floor(Math.random() * todos.length)];
  const res = http.patch(`${BASE_URL}/api/teams/${s.team}/todos/${todo.id}`, JSON.stringify({ done: !todo.done }), {
    headers: { 'Content-Type': 'application/json', Origin: ORIGIN, ...cookieHeader(s.token) },
    tags: { endpoint: 'todo_patch', kind: 'write' },
  });
  check(res, { 'status 200': (r) => r.status === 200 }, { endpoint: 'todo_patch' });
  writes.add(1);
}

/** With probability `mix`, do one write (half score PUT, half to-do toggle). Observers never write. */
export function maybeWrite(s, setupData, bundleResponses, mix) {
  if (s.role === 'observer' || Math.random() >= mix) return;
  group('write', () => {
    if (Math.random() < 0.5) putScore(s, setupData);
    else toggleTodo(s, bundleResponses[TODOS_INDEX]);
  });
}

/** One-time discovery for a test run: current week and the measurable ids per team (as the admin). */
export function discover() {
  const token = login('u1', { kind: 'setup', endpoint: 'login' });
  if (token === null) throw new Error('setup: cannot log in as u1 (is the stack up and the login limiter off?)');
  const measurables = {};
  let currentWeek = null;
  for (const team of TEAMS) {
    const res = http.get(`${BASE_URL}/api/teams/${team}/scorecard`, {
      headers: cookieHeader(token),
      tags: { kind: 'setup', endpoint: 'scorecard' },
      responseType: 'text', // the script otherwise discards bodies
    });
    if (res.status !== 200) throw new Error(`setup: scorecard of ${team} returned ${res.status}`);
    const body = res.json();
    currentWeek = body.current_week;
    measurables[team] = body.measurables.map((m) => m.id);
  }
  return { currentWeek, measurables, token };
}

// --- summary helpers --------------------------------------------------------------------------------------

/** A statistic of a (sub-)metric from handleSummary's `data`, or NaN when there were no samples. */
export function stat(data, metric, key) {
  const m = data.metrics[metric];
  return m && m.values && m.values[key] !== undefined ? m.values[key] : NaN;
}
export const fmt = (x, digits = 0) => (Number.isFinite(x) ? x.toFixed(digits) : 'n/a');
