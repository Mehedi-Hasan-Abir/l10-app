import { expect, test, type Browser, type Page } from "@playwright/test";

/**
 * Core journey with two browsers in the same Level 10 Meeting (brief §2.3, Part 3 "End-to-end"):
 * the facilitator (Blake, browser A) starts and advances the agenda; a teammate (Casey, browser B)
 * follows every section change, sees an Issue made in A appear live, and both end on the summary.
 *
 * Needs a freshly seeded stack (scripts/test.sh reseeds before running). Set E2E_DEMO_FAILURE=1 to
 * make one assertion fail on purpose; that run produced docs/artifacts/e2e-failure-trace.zip.
 */

const TEAM = "team_lead";
const SYNC_MS = 2_000; // "changes reach all attendees within about a second": allow 2 s in CI

async function signIn(browser: Browser, name: string): Promise<Page> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByRole("button", { name: new RegExp(`^Sign in as ${name}`) }).click();
  await page.waitForURL(/\/teams\//);
  await page.goto(`/teams/${TEAM}`);
  return page;
}

function currentSection(page: Page) {
  return page.getByRole("navigation", { name: "Meeting agenda" }).locator('[aria-current="step"]');
}

test.beforeAll(async ({ request }) => {
  // Fail fast with a clear message instead of a confusing timeout if the DB isn't freshly seeded.
  await request.post("/api/auth/login", { data: { user_id: "u2" } });
  const active = await request.get(`/api/teams/${TEAM}/meetings?status=active`);
  expect(active.ok(), "API reachable").toBe(true);
  expect(await active.json(), "no active meeting for team_lead (reseed: python -m app.seed --reset)").toEqual([]);
});

test("facilitator advances the agenda and the second browser follows", async ({ browser }) => {
  const facilitator = await signIn(browser, "Blake Brooks");
  const teammate = await signIn(browser, "Casey Cho");

  // A starts the meeting and becomes the facilitator.
  await facilitator.getByRole("button", { name: "Start meeting" }).first().click();
  await facilitator.waitForURL(new RegExp(`/teams/${TEAM}/meeting`));
  await expect(currentSection(facilitator)).toContainText("Scorecard");

  // B's header flips to the live meeting without a reload; B joins.
  await expect(teammate.getByRole("link", { name: /Meeting live/ })).toBeVisible({ timeout: SYNC_MS });
  await teammate.getByRole("link", { name: /Meeting live/ }).click();
  await teammate.waitForURL(new RegExp(`/teams/${TEAM}/meeting`));
  await expect(currentSection(teammate)).toContainText("Scorecard");
  await expect(teammate.getByRole("timer")).toBeVisible();

  // Both see each other as attendees (presence).
  const attendeesOf = (page: Page) => page.getByRole("region", { name: "Attendees" });
  await expect(attendeesOf(facilitator)).toContainText("Casey Cho", { timeout: SYNC_MS });
  await expect(attendeesOf(teammate)).toContainText("Blake Brooks");

  // Scorecard: the facilitator makes an Issue from an off-track cell of the last closed week.
  await facilitator.getByRole("button", { name: "Make it an Issue: Avg first response, week of Nov 2" }).click();
  await expect(facilitator.getByText("Issue created")).toBeVisible();

  // Facilitator advances to IDS: the second browser follows.
  await facilitator.getByRole("button", { name: "Next: IDS" }).click();
  await expect(currentSection(teammate)).toContainText("IDS", { timeout: SYNC_MS });
  if (process.env.E2E_DEMO_FAILURE === "1") {
    // Deliberate failure for the committed trace: the facilitator never advanced to Conclude.
    await expect(currentSection(teammate)).toContainText("Conclude", { timeout: SYNC_MS });
  }
  // The Issue made in browser A is in B's IDS list.
  await expect(teammate.getByText("Avg first response, week of Nov 2").first()).toBeVisible({ timeout: SYNC_MS });

  // Facilitator advances to Conclude: the second browser follows; both rate.
  await facilitator.getByRole("button", { name: "Next: Conclude" }).click();
  await expect(currentSection(teammate)).toContainText("Conclude", { timeout: SYNC_MS });
  await teammate.getByRole("radio", { name: "8 out of 10" }).click();
  await facilitator.getByRole("radio", { name: "9 out of 10" }).click();
  await expect(teammate.getByText(/Average\s*8\.5/)).toBeVisible({ timeout: SYNC_MS });
  await expect(facilitator.getByText(/Average\s*8\.5/)).toBeVisible({ timeout: SYNC_MS });

  // Facilitator ends the meeting: both browsers land on the summary.
  await facilitator.getByRole("button", { name: "End meeting" }).click();
  await facilitator.getByRole("alertdialog").getByRole("button", { name: "End meeting" }).click();
  for (const page of [facilitator, teammate]) {
    await expect(page.getByRole("heading", { name: "Meeting summary" })).toBeVisible({ timeout: SYNC_MS });
  }
  await expect(teammate.getByText("8.5").first()).toBeVisible();
});
