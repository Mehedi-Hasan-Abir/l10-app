# Failure artifact

`e2e-failure-trace.zip` is a real Playwright trace from a deliberately failing run of
`e2e/tests/meeting-sync.spec.ts`. `e2e-failure-screenshot.png` is the screenshot Playwright took on failure.

**How it was produced:** against a freshly seeded stack, with `E2E_DEMO_FAILURE=1`. That flag adds one wrong
assertion right after the facilitator advances to IDS: it expects the *second* browser to already show
**Conclude**, a section the facilitator never advanced to. The trace shows the sync working (browser B is on
"IDS (current)") and the assertion failing after its 2 s timeout:

```
Error: expect(locator).toContainText(expected) failed
Expected substring: "Conclude"
Received string:    "IDS (current)60 min"
```

**Open it:**
```bash
cd e2e && npx playwright show-trace ../docs/artifacts/e2e-failure-trace.zip
```
or drop the zip on https://trace.playwright.dev. The trace has the DOM snapshot before and after every action in
both browsers, the network log (REST calls, plus the WebSocket in the second context) and the console.

**Reproduce:** `E2E_DEMO_FAILURE=1 bash scripts/test.sh` (traces land in `e2e/test-results/`).
