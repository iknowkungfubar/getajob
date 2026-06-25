# GetAJob — Bulletproof Loop Charter

**Goal:** Every code-quality gate passes (mypy strict, pre-commit, pip-audit, bandit), all state transitions have integration tests, and the app handles every failure path gracefully.

**Stopping Conditions:**
1. mypy --strict passes with 0 errors on core/, agents/, getajob/, approval_queue/
2. pre-commit run --all-files passes
3. pip-audit exits 0 (no known vulnerabilities)
4. bandit -r passes on all application code
5. Approval queue approve/reject flow has integration tests
6. At least 60 tests total (current: 53)
7. Ruff <= 12 errors (same as current — all intentional)
8. .secrets.baseline exists and pre-commit detect-secrets passes
9. All existing CLI commands have --help that renders without errors
10. Coverage >= 50% across core modules

**Budget:**
- Max iterations: 8
- Max Claude Code invocations: 12
- Escalation: 3 consecutive failures on same item

**Files on disk (loop infrastructure):**
- .hermes/loop/bulletproof-state.json — state tracker
- .hermes/loop/bulletproof-charter.md — this file
- .hermes/loop/prompts/bp-*.txt — per-item prompts
- .hermes/loop/runner.sh — loop automation script
