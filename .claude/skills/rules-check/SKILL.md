---
name: rules-check
description: Differential test of the rules report against the independent oracle (tools/oracle_report.py) at the default clock and at tricky dates (DST, week and quarter boundaries). Use after any change to backend/app/rules, services, seed or report.
---

The rules report is graded against a hidden answer key and is also run at other dates. The oracle in
`tools/oracle_report.py` is a deliberately separate implementation of brief §2.6 computed straight from
`seed/seed.json`. A disagreement means one of the two is wrong; work out which from the brief, never by editing
the oracle to match.

For each NOW in: `2026-11-11T15:00:00Z` (default), `2026-11-09T05:59:59Z` (Sunday night local),
`2026-11-09T06:00:00Z` (Monday 00:00 local), `2026-11-18T15:00:00Z`, `2026-12-02T05:59:00Z`, `2026-12-02T06:00:00Z`,
`2026-10-01T12:00:00Z`, `2027-01-05T15:00:00Z`:

1. `uv run --no-project --with tzdata python tools/oracle_report.py --now NOW > .oracle.json`
2. `cd backend && uv run python -m app.report --now NOW > ../.report.json`
3. Compare as parsed JSON; on mismatch print the differing JSON paths with both values.

Delete the temp files afterwards. Summarize: dates checked, matches, and for each mismatch which side is wrong and why.
