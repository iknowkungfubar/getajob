# Changelog

All notable changes to the GetAJob project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.3.0] — 2026-06-24

### Added

- **Orchestrator pipeline wiring:** full `DISCOVERED → TAILORED → PENDING_REVIEW`
  cycle through the state machine in a single `run_once()` pass.
- Docker deployment infrastructure:
  - Multi-stage `Dockerfile` (uv builder + Chromium runtime, non-root user).
  - `docker-compose.yml` with Postgres 16, Redis 7, and an init container.
  - `.dockerignore` excluding dev and secret artifacts.
- `env.template` now documents the `GETAJOB_SECURITY__APPROVAL_PASSWORD` field.
- CI now runs `pip-audit` for dependency vulnerability scanning.

### Changed

- Simplified CI test matrix: removed the pointless Postgres variant (the app
  never read `DATABASE_URL`; it uses `GETAJOB_DATABASE__*` vars instead).
- CI caches uv packages and tool artifacts across runs for faster feedback.

---

## [0.2.0] — 2026-06-24

### Added

- **Security audit (v3):** hardened PII encryption paths, added tokenizer salt
  validation, remediated insecure default warnings.
- **Performance audit (v2):** identified and fixed 3 critical bugs —
  connection-pool starvation in the database layer, unbounded retry loop in the
  browser engine, and a race condition in the event-bus subscription registry.
- **Production SDLC hardening:** structured logging correlation IDs, graceful
  degradation in the ingestion agent, fail-fast config validation at startup.
- `ruff`, `mypy`, and `pre-commit` configs to `pyproject.toml`.

### Changed

- Minimum Python version raised to 3.12 across all tooling configs.
- All database queries now use bound parameters (no raw f-string interpolation).
- Test coverage raised to >80% for the outreach and profile modules.

---

## [0.1.1] — 2026-06-24

### Added

- `min_match_score` filter on the context agent's skill-matching step.
- Explicit timezone handling (UTC) for all datetime fields in the ORM models.

### Fixed

- `test_helper_models` assertion failure caused by naive vs. aware datetime
  comparison.  All datetime fields now carry a `timezone=True` SQLAlchemy flag.
- Alembic migration ordering — `3b8f1a3b9072` now correctly depends on the
  initial schema migration.

---

## [0.1.0] — 2026-06-24

### Added

- **Core framework:** async SQLAlchemy engine, Pydantic-v2 settings (loaded from
  env vars / `.env` / YAML overlay), AES-256-GCM PII encryption, structlog
  integration, custom exception hierarchy.
- **State machine:** PostgreSQL-backed application state machine with 8 states
  and 18 validated transitions (`DISCOVERED → TAILORED → PENDING_REVIEW →
  STAGED → SUBMITTED → OUTREACH_PENDING`, plus `FAILED` / `REJECTED`).
- **Profile engine:** immutable user profile store, ChromaDB vector store for
  semantic skill matching, experience parser.
- **Ingestion agent:** job discovery against Greenhouse and Lever public APIs
  with configurable search vectors, deduplication, rate-limit awareness.
- **Context agent:** JD analysis against the user profile, skill matching,
  relevance scoring, gap analysis.
- **Tailoring engine:** resume and cover letter generation via Claude API,
  anti-hallucination cross-check against the master profile, anti-AI-detection
  style guardrails.
- **Browser engine:** stealth Chromium automation factory, human interaction
  simulator (Bezier mouse, variable typing delays), ATS detector with profiles
  for Workday, Greenhouse, Lever, LinkedIn, Indeed, and a generic fallback.
- **Outreach engine:** recruiter contact discovery (OSINT), personalized message
  generation, email format and MX validation.
- **Approval queue:** FastAPI web UI with Jinja2/HTMX, session-based single-user
  auth, dashboard, review/approve/reject workflow, application tracking.
- **CLI:** `typer`-based command-line interface with `run`, `discover`, `tailor`,
  `serve`, and `setup` commands, Rich-formatted output.
- **CI pipeline:** GitHub Actions with lint (ruff), type check (mypy strict),
  tests (pytest + coverage), and security scan (trufflehog + bandit).
- **Project documentation:** architecture doc, security doc, deployment guide,
  MIT license.
- **Pre-commit hooks:** ruff lint+format, mypy, trailing-whitespace, end-of-file,
  YAML/JSON validation, merge-conflict detection, detect-secrets.
