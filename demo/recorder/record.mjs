// Records the demo: two headless Chromium browsers (A = Blake, the facilitator; B = Casey, then Ellis) driven
// through the L10 app at DEMO_BASE_URL, each with its own 1280x1100 video. Then compose.mjs makes the final mp4.
import { chromium, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { compose } from "./compose.mjs";
import { beat, clickVisibly, createTimeline, cursorInit, hoverVisibly, scrollToElement, typeVisibly } from "./helpers.mjs";

const BASE = process.env.DEMO_BASE_URL ?? "http://proxy";
const OUT = process.env.DEMO_OUT_DIR ?? "/demo/out";
const RAW = path.join(OUT, "raw");
const TEAM = "team_lead";
const SYNC_MS = 4_000; // generous for the recording; the app's own target is ~1 s
const VIEWPORT = { width: 1280, height: 1100 };

fs.rmSync(RAW, { recursive: true, force: true });
fs.mkdirSync(RAW, { recursive: true });

const browser = await chromium.launch();

async function newBrowser(name) {
  const context = await browser.newContext({
    viewport: VIEWPORT,
    baseURL: BASE,
    deviceScaleFactor: 1,
    bypassCSP: true, // the cursor overlay needs inline styles/SVG
    recordVideo: { dir: path.join(RAW, name), size: VIEWPORT },
  });
  await context.addInitScript(cursorInit);
  const page = await context.newPage();
  const t0 = Date.now(); // the video starts at page creation: A/B alignment comes from these two numbers
  page.on("pageerror", (e) => console.error(`[${name}] pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") console.error(`[${name}] console.error: ${m.text()}`);
  });
  return { name, context, page, t0 };
}

const A = await newBrowser("A");
const B = await newBrowser("B");
const a = A.page;
const b = B.page;
const tl = createTimeline();
let tStart = 0;

const currentSection = (page) => page.getByRole("navigation", { name: "Meeting agenda" }).locator('[aria-current="step"]');
const signInButton = (page, name) => page.getByRole("button", { name: new RegExp(`^Sign in as ${name}`) });
const teamReady = (page) => page.getByText("Weekly numbers for the last 13 weeks.").waitFor();
const cell = (page, label) => page.getByRole("button", { name: label });

async function story() {
  // ---- sign in ------------------------------------------------------------------------------------------------------
  await Promise.all([a.goto("/login"), b.goto("/login")]);
  await Promise.all([signInButton(a, "Blake Brooks").waitFor(), signInButton(b, "Casey Cho").waitFor()]);
  await beat(500);
  tStart = tl.start();

  await tl.caption("Sign in (seeded users)");
  await clickVisibly(a, signInButton(a, "Blake Brooks"));
  await a.waitForURL(/\/teams\//);
  await clickVisibly(b, signInButton(b, "Casey Cho"));
  await b.waitForURL(/\/teams\//);
  await Promise.all([a.goto(`/teams/${TEAM}`), b.goto(`/teams/${TEAM}`)]);
  await Promise.all([teamReady(a), teamReady(b)]);
  await beat(800);
  tl.still("signed-in");

  // ---- scorecard: status is computed ----------------------------------------------------------------------------------
  await tl.caption("Scorecard: live status");
  await hoverVisibly(a, cell(a, /^Customer churn, week of Nov 2: 3, On track/), { dwell: 1500 });
  await hoverVisibly(a, cell(a, /^Payroll runs, week of Oct 26: 0, Off track/), { dwell: 1800 });

  // ---- a score edit syncs to the other browser --------------------------------------------------------------------------
  await tl.caption("Score edit syncs live");
  await clickVisibly(a, cell(a, /^Cash on hand, week of Nov 2: no score, Off track\. Edit score/));
  const editor = a.getByRole("textbox", { name: /^Cash on hand, week of Nov 2\./ });
  await editor.waitFor();
  await typeVisibly(editor, "510");
  await beat(300);
  await editor.press("Enter");
  await expect(cell(a, /^Cash on hand, week of Nov 2: 510, On track/)).toBeVisible();
  await expect(cell(b, /^Cash on hand, week of Nov 2: 510, On track/)).toBeVisible({ timeout: SYNC_MS });
  await beat(1800);
  tl.still("live-score");

  // ---- Make it an Issue, live in the other browser ------------------------------------------------------------------------
  await tl.caption("Make it an Issue");
  await scrollToElement(b, b.locator("#issues")); // Casey is looking at the Issues list when it lands
  const churn = "Customer churn, week of Oct 5";
  await clickVisibly(a, a.getByRole("button", { name: `Make it an Issue: ${churn}` }));
  await expect(a.getByText("Issue created")).toBeVisible();
  await expect(b.locator("#issues").getByText(churn)).toBeVisible({ timeout: SYNC_MS });
  await beat(1800);
  tl.still("issue-live");

  // ---- one quick tour of the rest of the page ------------------------------------------------------------------------
  await tl.caption("Issues · To-Dos · Rocks · Activity");
  await scrollToElement(a, a.locator("#rocks"));
  await beat(1500);
  await scrollToElement(a, a.locator("#activity"));
  await scrollToElement(b, b.locator("#activity"));
  await beat(1800);

  // ---- start the live meeting, the teammate joins -------------------------------------------------------------------------
  await tl.caption("Start meeting · teammate joins");
  await clickVisibly(a, a.getByRole("button", { name: "Start meeting" }).first());
  await a.waitForURL(new RegExp(`/teams/${TEAM}/meeting`));
  await expect(currentSection(a)).toContainText("Scorecard");
  const live = b.getByRole("link", { name: /Meeting live/ });
  await expect(live).toBeVisible({ timeout: SYNC_MS });
  await beat(700);
  await clickVisibly(b, live);
  await b.waitForURL(new RegExp(`/teams/${TEAM}/meeting`));
  await expect(currentSection(b)).toContainText("Scorecard");
  await expect(a.getByRole("region", { name: "Attendees" })).toContainText("Casey Cho", { timeout: SYNC_MS });
  await beat(1500);
  tl.still("meeting-live");
  await tl.caption("Live presence · server timer");
  await hoverVisibly(a, a.getByRole("region", { name: "Attendees" }), { dwell: 1800 });
  await hoverVisibly(a, a.getByRole("timer"), { dwell: 1800 });

  // ---- an Issue made inside the meeting ------------------------------------------------------------------------------------
  await tl.caption("Meeting Scorecard: Make it an Issue");
  await clickVisibly(a, a.getByRole("button", { name: "Make it an Issue: Avg first response, week of Nov 2" }));
  await expect(a.getByText("Issue created")).toBeVisible();
  await beat(1800);

  // ---- advance: the other browser follows -----------------------------------------------------------------------------------
  await tl.caption("Next: IDS · other browser follows");
  await clickVisibly(a, a.getByRole("button", { name: "Next: IDS" }));
  await expect(currentSection(b)).toContainText("IDS", { timeout: SYNC_MS });
  await expect(b.getByText("Avg first response, week of Nov 2").first()).toBeVisible({ timeout: SYNC_MS });
  await hoverVisibly(b, b.getByText("Avg first response, week of Nov 2").first(), { dwell: 1500 });
  tl.still("ids-follow");

  // ---- conclude: ratings --------------------------------------------------------------------------------------------------------
  await tl.caption("Next: Conclude · everyone rates");
  await clickVisibly(a, a.getByRole("button", { name: "Next: Conclude" }));
  await expect(currentSection(b)).toContainText("Conclude", { timeout: SYNC_MS });
  await beat(600);
  await tl.caption("Ratings: average 8.5");
  await clickVisibly(b, b.getByRole("radio", { name: "8 out of 10" }));
  await clickVisibly(a, a.getByRole("radio", { name: "9 out of 10" }));
  await expect(a.getByText(/Average\s*8\.5/)).toBeVisible({ timeout: SYNC_MS });
  await expect(b.getByText(/Average\s*8\.5/)).toBeVisible({ timeout: SYNC_MS });
  await beat(2200);
  tl.still("conclude-average");

  // ---- end the meeting: a frozen summary -------------------------------------------------------------------------------------------
  await tl.caption("End meeting · frozen summary");
  await clickVisibly(a, a.getByRole("button", { name: "End meeting" }));
  const confirm = a.getByRole("alertdialog").getByRole("button", { name: "End meeting" });
  await confirm.waitFor();
  await beat(600);
  await clickVisibly(a, confirm);
  for (const page of [a, b]) {
    await expect(page.getByRole("heading", { name: "Meeting summary" })).toBeVisible({ timeout: SYNC_MS });
  }
  await hoverVisibly(a, a.getByText("Meeting rating"), { dwell: 1500 });
  await hoverVisibly(b, b.getByText("Meeting rating"), { dwell: 1500 });
  tl.still("summary");

  // ---- an Observer: view only (optional: a failure here still keeps everything recorded so far) -----------------------------------
  try {
    await tl.caption("Observer: view only");
    await clickVisibly(b, b.getByRole("button", { name: /^Account: Casey Cho/ }));
    await clickVisibly(b, b.getByRole("menuitem", { name: "Sign out" }));
    await b.waitForURL(/\/login/);
    await signInButton(b, "Ellis Evans").waitFor();
    tl.labelB("B · Ellis Evans — observer");
    await clickVisibly(b, signInButton(b, "Ellis Evans"));
    await b.waitForURL(/\/teams\//);
    await b.goto(`/teams/${TEAM}`);
    await teamReady(b);
    await expect(b.getByText("View only")).toBeVisible();
    await hoverVisibly(b, b.getByText("View only"), { dwell: 1500 });
    await hoverVisibly(b, b.getByRole("group", { name: /^Cash on hand, week of Nov 2: 510/ }), { dwell: 1500 });
    await hoverVisibly(b, b.getByText("Read-only"), { dwell: 1500 });
    tl.still("observer");
  } catch (err) {
    console.error("OBSERVER BEAT FAILED (video keeps what was recorded):", err.message);
    for (const x of [A, B]) await x.page.screenshot({ path: path.join(RAW, `observer-fail-${x.name}.png`) }).catch(() => {});
  }
  await beat(800);
}

let failed = false;
try {
  await story();
} catch (err) {
  failed = true;
  console.error("STORY FAILED:", err);
  for (const x of [A, B]) {
    await x.page.screenshot({ path: path.join(RAW, `fail-${x.name}.png`) }).catch(() => {});
    console.error(`[${x.name}] url at failure: ${x.page.url()}`);
  }
}

const tEnd = tStart ? tStart + (await tl.finish()) : Date.now();
await A.context.close();
await B.context.close();
await browser.close();

// Playwright names videos with random ids; give them fixed names.
const keep = (name) => {
  const dir = path.join(RAW, name);
  const file = fs.readdirSync(dir).find((f) => f.endsWith(".webm"));
  fs.renameSync(path.join(dir, file), path.join(RAW, `${name}.webm`));
  fs.rmSync(dir, { recursive: true, force: true });
};
keep("A");
keep("B");

const timeline = { tA0: A.t0, tB0: B.t0, tStart, tEnd, ...tl.data() };
fs.writeFileSync(path.join(RAW, "timeline.json"), JSON.stringify(timeline, null, 2));
if (failed) process.exit(1);

if (process.env.DEMO_SKIP_COMPOSE !== "1") compose(timeline);
