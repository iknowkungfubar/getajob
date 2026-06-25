# GetAJob — Next-Level Loop Charter

**Goal:** GetAJob is a polished, launch-ready product that delivers real value: discover real jobs, generate real tailored materials, submit via browser or manual export, and track everything through the approval queue.

**Trigger:** Manual invocation. Loop picks next highest-priority item, dispatches Claude Code, verifies, repeats.

**Scope:**
- IN: Search vector management, profile CLI, approval queue approve/reject wiring, STAGED→SUBMITTED integration test, dashboard with real data, Docker compose end-to-end, PyPI publishing prep, branch protection setup, Dependabot auto-merge config
- OUT: New agent modules, outreach engine changes, LinkedIn scraping, multi-user auth, browser engine refactoring

**Stopping Conditions (ALL must be met):**
1. `getajob vector` CLI commands exist (list, add, remove)
2. `getajob profile` CLI commands exist (show, update)
3. Approval queue approve/reject flow works end-to-end with real state transitions
4. Dashboard shows real data from DB (not mock data)
5. Docker compose up with PostgreSQL works end-to-end
6. Repository has branch protection rules configured
7. Dependabot auto-merge is configured for patch updates
8. All existing 53+ tests pass
9. Ruff warnings <= 20

**Budget:**
- Max iterations: 8 (one per work item, some may merge)
- Max Claude Code invocations: 12 (some items need 2 prompts)
- Max total execution: stay within session

**Escalation Criteria:**
- 3 consecutive identical failures
- Claude Code proxy unavailable
- Scope boundary violation

**Human Review Required:** On escalation or every 3 iterations
