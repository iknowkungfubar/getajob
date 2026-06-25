# GetAJob — Production Readiness Loop Charter

**Goal:** GetAJob is production-grade, launchable, and easy for non-technical users to install and use.

**Trigger:** Manual invocation via `getajob-loop.md` instructions.

**Scope:**
- IN: CLI improvements (init, doctor), default vectors, PyPI publishing, graceful browser degradation, tests, ruff cleanup, Docker polish, documentation updates
- OUT: No new agent modules, no browser engine refactoring, no outreach engine changes, no multi-user auth, no LinkedIn scraping

**Stopping Conditions (ALL must be met):**
1. `getajob init` creates a working .env + profile + search vectors in one interactive command
2. `getajob doctor` pre-flights API key, database, browser, and config — clear pass/fail per check
3. 3 default search vectors ship so `getajob run` produces results on first invocation
4. `pip install getajob` works (published to PyPI or test PyPI)
5. Browser submission degrades gracefully if Chromium not installed (export URL for manual submission instead)
6. Approval queue route tests exist (min 5 tests covering auth, health, dashboard, stats)
7. Ruff warnings ≤ 20 (down from 105)
8. All 46+ existing tests continue to pass
9. Docker compose up works for first-time user with just `docker compose up`

**Budget:**
- Max iterations: 7 (one per work item)
- Max tool calls per iteration: 50
- Timeout per Claude Code invocation: 600s

**Escalation Criteria:**
- 3 consecutive identical failures
- Claude Code proxy unavailable for 3 consecutive attempts
- Scope boundary violation (touches OUT items)

**Human Review Required:** On escalation
