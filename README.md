# GetAJob — Agentic Job Application Platform

[![CI](https://github.com/iknowkungfubar/getajob/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/iknowkungfubar/getajob/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

> **Automated job application platform for 2026.** Searches job boards, tailors resumes, generates cover letters, applies via browser automation, and performs recruiter outreach — with **human-in-the-loop validation** at every step. Targets **50 applications/day** across diverse platforms.

---

## Project Status

> **⚠️ Pre-Alpha — Active Development**
>
> This platform is under active construction. The codebase framework is structurally complete but the integration pipeline (Phase 9) has not been implemented. See [`docs/architecture.md`](docs/architecture.md#integration-status) for the current state of each module and the [audit findings](audit-findings.md) for the full gap analysis.
>
> **Estimated completion:** ~45% of the feature set. Suitable for development experimentation and contributor onboarding, not production use.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Modules](#modules)
- [State Machine](#state-machine)
- [Quick Start](#quick-start)
- [Development Setup](#development-setup)
- [CLI Reference](#cli-reference)
- [Project Structure](#project-structure)
- [Security](#security)
- [CI/CD](#cicd)
- [Testing](#testing)
- [Pre-Commit Hooks](#pre-commit-hooks)
- [License](#license)

---

## Architecture Overview

The platform is built around a **Hermes Agent** orchestrator that drives five specialized agents through a PostgreSQL-backed state machine. A FastAPI approval queue provides the human-in-the-loop gateway.

```
┌──────────────────────────────────────────────────────────────────┐
│                     Hermes Agent (Orchestrator)                    │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐   │
│   │Ingestion │  │ Profile  │  │Tailoring │  │  Browser Exec   │   │
│   │  Agent   │  │  Agent   │  │  Agent   │  │    Agent        │   │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬────────┘   │
│        │             │             │              │              │
│   ┌────▼─────────────▼─────────────▼──────────────▼──────────┐   │
│   │                PostgreSQL State Machine                    │   │
│   │  DISCOVERED → TAILORED → PENDING_REVIEW → STAGED →       │   │
│   │  SUBMITTED → OUTREACH_PENDING                             │   │
│   └───────────────────────────────────────────────────────────┘   │
│   ┌───────────────────────────────────────────────────────────┐   │
│   │                Approval Queue (HITL Gateway)               │   │
│   └───────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

**See the full architecture documentation** → [`docs/architecture.md`](docs/architecture.md)

Key design documents:
- [System Architecture](docs/architecture.md) — Full module descriptions, data flow diagrams, state machine, agent roles
- [Security Architecture](docs/security.md) — Encryption, PII tokenization, secrets management, threat model
- [Deployment Guide](docs/deployment.md) — Local dev, production requirements, Docker, monitoring
- [Audit Findings](audit-findings.md) — Comprehensive 2026-06-24 codebase audit with prioritized fix list

---

## Modules

| Module | Directory | Purpose | Status |
|--------|-----------|---------|--------|
| **Core Framework** | [`core/`](core/) | Config, async DB, state machine, LLM abstraction, security, event bus | ✅ Complete |
| **Profile Engine** | [`profile_engine/`](profile_engine/) | Immutable user profile storage, ChromaDB vector store, skill parsing | ✅ Complete |
| **Ingestion Agent** | [`agents/ingestion_agent.py`](agents/ingestion_agent.py) | Job discovery via public APIs (Greenhouse, Lever) with browser fallback | ⚠️ API sources work, browser stubs pending |
| **Context Agent** | [`agents/context_agent.py`](agents/context_agent.py) | JD analysis, skill matching, relevance scoring | ✅ Complete |
| **Tailoring Engine** | [`tailoring_engine/`](tailoring_engine/) | Resume/cover letter generation with anti-AI and anti-hallucination guardrails | ⚠️ Text output only, no PDF |
| **Browser Engine** | [`browser_engine/`](browser_engine/) | Stealth automation with ATS profiles (Workday, Greenhouse, Lever, LinkedIn, Indeed) | ⚠️ Known registry bug blocks ATS profiles |
| **Outreach Engine** | [`outreach_engine/`](outreach_engine/) | Recruiter contact discovery, personalized message gen, email validation | ✅ Complete (not integrated) |
| **Approval Queue** | [`approval_queue/`](approval_queue/) | FastAPI web UI for HITL review and approval | ⚠️ UI complete, auth pending |
| **Orchestrator** | [`agents/orchestrator_agent.py`](agents/orchestrator_agent.py) | Main pipeline loop | ✅ DISCOVERED → TAILORED → PENDING_REVIEW |

---

## State Machine

Applications flow through **7 states** with 18 validated transitions:

```
DISCOVERED → TAILORED → PENDING_REVIEW → STAGED → SUBMITTED → OUTREACH_PENDING
                  ↓            ↓
               FAILED      REJECTED
```

| State | Description |
|-------|-------------|
| **DISCOVERED** | Job listing found and parsed by Ingestion Agent |
| **TAILORED** | Resume and cover letter generated by Tailoring Engine |
| **PENDING_REVIEW** | Awaiting human approval via Approval Queue |
| **STAGED** | Approved, ready for browser submission |
| **SUBMITTED** | Application successfully submitted |
| **OUTREACH_PENDING** | Recruiter contact found, message drafted |
| **REJECTED** | Human rejected the application |
| **FAILED** | Unrecoverable error in processing |

> **Note:** As of v0.3.0, the automated pipeline reaches `DISCOVERED → TAILORED → PENDING_REVIEW` in a single pass. The approval queue API handles transitions to `STAGED` and `REJECTED`. Remaining states (`SUBMITTED`, `OUTREACH_PENDING`) require the browser engine and outreach module integration.

---

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL (or SQLite for development)
- Redis (optional, for production event bus)
- Playwright browsers (`playwright install chromium`)

### Installation (Local)

```bash
# Clone the repo
git clone https://github.com/iknowkungfubar/getajob.git
cd getajob

# Install with dev dependencies (recommended: use uv)
pip install -e ".[dev]"

# Or with uv (faster)
pip install uv
uv sync --group dev

# Install Playwright browser
playwright install chromium

# Copy and configure environment
cp env.template .env
# Edit .env with your settings

# Run setup
getajob setup
```

### Docker Quickstart

The fastest way to get the full stack running:

```bash
# 1. Clone and configure
git clone https://github.com/iknowkungfubar/getajob.git
cd getajob
cp env.template .env

# 2. Edit .env with your API keys and secrets (at minimum GETAJOB_LLM__API_KEY)
#    Set GETAJOB_SECURITY__APPROVAL_PASSWORD for production use.

# 3. Start all services
docker compose up -d

# 4. Watch the init container run setup (migrations, data dirs)
docker compose logs -f init

# 5. Open the approval queue
open http://localhost:8080

# 6. Run the pipeline (one-shot)
docker compose run --rm app run
```

> **Note:** The `docker compose up -d` command starts PostgreSQL, Redis, and the
> approval queue.  An `init` container runs `getajob setup` once and exits.
> The `app` service waits for init to complete before starting.

### Usage

```bash
# Full pipeline (discover → tailor → stage)
getajob run

# Continuous mode (run every 15 minutes)
getajob run --continuous

# Job discovery only
getajob discover

# Start the approval queue web UI
getajob serve

# Tailor a specific job
getajob tailor <job-id>
```

---

## Docker Deployment

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  docker-compose.yml                                          │
│                                                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────────┐   │
│  │ Postgres │    │  Redis   │    │   App (FastAPI +      │   │
│  │  16-alp  │◄──►│  7-alp   │◄──►│   pipeline daemon)    │   │
│  └──────────┘    └──────────┘    │  :8080 approval queue │   │
│                                  └──────────────────────┘   │
│  ┌──────────┐                                               │
│  │  Init    │  ← runs `getajob setup` once, then exits      │
│  └──────────┘                                               │
└─────────────────────────────────────────────────────────────┘
```

### Services

| Service  | Image                          | Purpose                                |
|----------|--------------------------------|----------------------------------------|
| `app`    | `getajob` (build from `Dockerfile`) | FastAPI approval queue + pipeline   |
| `postgres` | `postgres:16-alpine`           | State machine, application store       |
| `redis`  | `redis:7-alpine`               | Async event bus, caching               |
| `init`   | `getajob` (build)              | Run-once setup (migrations, data dirs) |

### Volumes

| Volume      | Mount Point                    | Data                           |
|-------------|--------------------------------|--------------------------------|
| `pgdata`    | `/var/lib/postgresql/data`     | Database files                 |
| `redisdata` | `/data`                        | Redis persistence (AOF/RDB)    |
| `appdata`   | `/app/data`                    | Resume PDFs, screenshots, etc. |

### Environment Variables

The compose file reads `GETAJOB_*` variables from your `.env` file.  Required
variables are validated at container start — compose will refuse to start if
they are missing:

| Variable | Required | Default |
|----------|----------|---------|
| `GETAJOB_LLM__API_KEY` | ✅ Yes | — |
| `GETAJOB_SECURITY__ENCRYPTION_KEY` | ✅ Yes | — |
| `GETAJOB_SECURITY__ENCRYPTION_SALT` | ✅ Yes | — |
| `GETAJOB_DATABASE__PASSWORD` | ✅ Yes | `change_me_in_production` |
| `GETAJOB_SECURITY__APPROVAL_PASSWORD` | ⚠️ Production | — |
| `GETAJOB_APP_PORT` | No | `8080` |

### Container Management

```bash
# Start everything
docker compose up -d

# Tail logs
docker compose logs -f app

# Run the pipeline (one-shot)
docker compose run --rm app run

# Run setup manually
docker compose run --rm app setup

# Open a shell in the app container
docker compose exec app bash

# Stop all services
docker compose down

# Stop and delete volumes (⚠️ destroys data)
docker compose down -v
```

### Production Deployment Checklist

- [ ] **Set `GETAJOB_ENVIRONMENT=production`** — enables HSTS headers,
      requires the approval password, disables hot-reload.
- [ ] **Set `GETAJOB_SECURITY__APPROVAL_PASSWORD`** — a strong random string
      for the approval queue login.  Not the same as the database password.
- [ ] **Set `GETAJOB_SECURITY__ENCRYPTION_KEY`** and **`_ENCRYPTION_SALT`** —
      generate fresh random values per deployment.  Key loss = data loss.
- [ ] **Change the database password** — do not use `change_me_in_production`.
- [ ] **Use HTTPS in production** — front the app container with a reverse
      proxy (nginx / Caddy / Traefik) for TLS termination.
- [ ] **Restrict CORS** — update `allow_origins` in `approval_queue/main.py`
      to your actual domain, or remove the middleware and rely on the proxy.
- [ ] **Pin image tags** — replace `postgres:16-alpine` and `redis:7-alpine`
      with exact versions (e.g. `postgres:16.4-alpine3.20`).
- [ ] **Enable Redis password** — set `GETAJOB_REDIS__PASSWORD` and uncomment
      the line in `env.template`.
- [ ] **Add resource limits** — un-comment `deploy.resources` in the compose
      file for production workloads.
- [ ] **Run database migrations** — the `init` container handles this
      automatically on first start.  For upgrades, run manually:
      `docker compose run --rm app setup`.
- [ ] **Schedule backups** — implement automated pg_dump + encrypted PII
      backups (see [Backup & Recovery](docs/deployment.md#backup--recovery)).
- [ ] **Enable structured logging** — set `GETAJOB_LOG_FORMAT=json` and ship
      logs to your observability backend (ELK / Loki / Datadog).

---

## Development Setup

### Environment Setup

```bash
# Python version
python --version   # Must be 3.12+

# Install dev extras
uv sync --group dev

# Verify setup
getajob setup --verify
```

### Database

For local development, SQLite works out of the box:

```bash
# SQLite (default, no setup needed)
export DATABASE_URL="sqlite+aiosqlite:///data/getajob.db"

# PostgreSQL (for production parity)
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/getajob"
createdb getajob
```

### Pre-Commit Hooks

This project uses [pre-commit](https://pre-commit.com) to enforce code quality. Install the hooks:

```bash
# Install pre-commit
pre-commit install

# Run all hooks on the full codebase
pre-commit run --all-files
```

The hooks run automatically on `git commit` and will:
- Lint and format code with **Ruff** (auto-fixes applied)
- Run **mypy** type checking on staged files
- Trim trailing whitespace and ensure files end with newline
- Validate YAML/JSON syntax
- Check for merge conflict markers
- Detect accidental secrets with **detect-secrets**

See [`.pre-commit-config.yaml`](.pre-commit-config.yaml) for the full configuration.

### Testing with Coverage

```bash
# Run all tests
pytest

# With coverage report
pytest --cov

# Coverage with HTML report
pytest --cov --cov-report=html
open htmlcov/index.html

# Run specific test suites
pytest tests/test_outreach/
pytest tests/integration/

# Run tests against PostgreSQL (default: SQLite)
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/getajob_test"
pytest --cov

# Run with verbose output
pytest -v --tb=short
```

### Code Quality

```bash
# Lint
ruff check .

# Auto-fix lint issues
ruff check --fix .

# Format
ruff format .

# Type check
mypy .

# Full CI pipeline locally
pre-commit run --all-files && pytest --cov && mypy .
```

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `getajob run` | Run the full pipeline: discover → context → tailor → stage |
| `getajob run --continuous` | Run in continuous mode (every 15 min, configurable) |
| `getajob run --dry-run` | Discovery only, no persistence |
| `getajob discover` | Run job discovery only |
| `getajob discover --dry-run` | Preview what would be discovered |
| `getajob tailor <job-id>` | Tailor resume + cover letter for a specific job |
| `getajob serve` | Start the approval queue web UI on port 8080 |
| `getajob serve --port 9090` | Start on a custom port |
| `getajob setup` | Verify environment, run migrations, check dependencies |
| `getajob setup --verify` | Run verification checks without applying changes |
| `getajob --help` | Show full CLI help |
| `getajob <command> --help` | Show help for a specific command |

---

## Project Structure

```
getajob/
├── core/                    # Core framework (config, DB, models, security, state machine)
├── agents/                  # Agent definitions (ingestion, context, tailoring, orchestrator)
├── browser_engine/          # Stealth browser automation + ATS profiles
├── profile_engine/          # User profile storage + ChromaDB vector store
├── tailoring_engine/        # Resume + cover letter generation
├── outreach_engine/         # Recruiter contact discovery + messaging
├── approval_queue/          # FastAPI web UI for human-in-the-loop
├── config/                  # YAML settings + LLM prompt templates
├── tests/                   # Test suite
├── docs/                    # Documentation
│   ├── architecture.md      # System architecture & data flow
│   ├── security.md          # Security architecture & threat model
│   └── deployment.md        # Deployment & operations guide
├── scripts/                 # Utility scripts
└── data/                    # Runtime data (gitignored)
```

---

## Security

See [`docs/security.md`](docs/security.md) for the complete security documentation.

### Key Security Features

- **AES-256-GCM encryption** for all PII at rest with PBKDF2 key derivation
- **PII tokenization** — dynamic masking of sensitive fields in logs
- **No secrets in code** — all credentials via environment variables (`.gitignore` covers `.env`)
- **Human-in-the-loop** gate before every submission (approval queue)
- **Zero-trust hallucination checking** — cross-verifies every generated claim against the master profile
- **Detect-secrets pre-commit hook** — prevents accidental secret commits

### Known Security Gaps

| Issue | File | Severity |
|-------|------|----------|
| Zero-salt PBKDF2 fallback if `ENCRYPTION_SALT` unset | `profile_store.py:74` | MEDIUM |
| No backend authentication in approval queue | `approval_queue/` | MEDIUM |
| LinkedIn scraping without proxy rotation | `contact_finder.py:391` | LOW |
| Mock data paths in production code | `routes.py:645` | LOW |

### Reporting Vulnerabilities

Please report security issues by opening a GitHub Issue with the `[security]` tag. Do not include sensitive data in the issue body.

---

## CI/CD

The project uses **GitHub Actions** for continuous integration. See [`.github/workflows/ci.yml`](.github/workflows/ci.yml) for the full configuration.

### CI Pipeline

| Job | Tool | Purpose |
|-----|------|---------|
| **Lint** | Ruff | Code style, formatting, and correctness |
| **Type Check** | mypy (strict) | Static type verification |
| **Test** | pytest + pytest-cov | Unit + integration tests (SQLite) with coverage |
| **Security** | TruffleHog + Bandit + pip-audit | Secrets detection, SAST, dependency vulnerability scan |

### Status Badges

[![CI](https://github.com/iknowkungfubar/getajob/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/iknowkungfubar/getajob/actions/workflows/ci.yml)

---

## License

MIT — See [LICENSE](LICENSE) for details.

---

*GetAJob — Agentic Job Application Platform. Built for the 2026 job market.*
