import { defineConfig, devices } from "@playwright/test";

/**
 * E2E runs against a fully started stack (docker compose: Caddy → Next.js + FastAPI → Postgres), freshly
 * seeded by scripts/test.sh. Locally: `E2E_BASE_URL=http://localhost:3000 npx playwright test`.
 */
export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1, // one shared, seeded database
  retries: 0, // a realtime flake should fail loudly, not be retried away
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
});
