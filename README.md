# GetAJob — Agentic Job Application Platform

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

> **Automated job application platform for 2026.** Searches job boards, tailors resumes, generates cover letters, applies via browser automation, and performs recruiter outreach — with human-in-the-loop validation. Targets **50 applications/day** across diverse platforms.

## Architecture

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

## Modules

| Module | Directory | Purpose |
|--------|-----------|---------|
| **Core Framework** | `core/` | Configuration, async DB, state machine, LLM abstraction, security, event bus |
| **Profile Engine** | `profile_engine/` | Immutable user profile storage, ChromaDB vector store, skill parsing |
| **Ingestion Agent** | `agents/ingestion_agent.py` | Job discovery via public APIs (Greenhouse, Lever) with browser fallback |
| **Context Agent** | `agents/context_agent.py` | Job description analysis, skill matching, relevance scoring |
| **Tailoring Engine** | `tailoring_engine/` | Resume/cover letter generation with anti-AI and anti-hallucination guardrails |
| **Browser Engine** | `browser_engine/` | Stealth browser automation with human-mimicking behavior; ATS profiles for Workday, Greenhouse, Lever, LinkedIn, Indeed |
| **Outreach Engine** | `outreach_engine/` | Recruiter contact discovery, personalized message generation, email validation |
| **Approval Queue** | `approval_queue/` | FastAPI web UI for human-in-the-loop review and submission approval |
| **Orchestrator** | `agents/orchestrator_agent.py` | Main pipeline loop tying all modules together |

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL (or SQLite for development)
- Redis (optional, for production event bus)
- Playwright browsers (`playwright install chromium`)

### Installation

```bash
# Clone the repo
git clone https://github.com/iknowkungfubar/getajob.git
cd getajob

# Install with dev dependencies
pip install -e ".[dev]"

# Install Playwright browser
playwright install chromium

# Copy and configure environment
cp env.template .env
# Edit .env with your settings

# Run setup
getajob setup
```

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

### Development

```bash
# Run tests
pytest

# With coverage
pytest --cov

# Lint
ruff check .

# Type check
mypy .
```

## State Machine

Applications flow through these states:

```
DISCOVERED → TAILORED → PENDING_REVIEW → STAGED → SUBMITTED → OUTREACH_PENDING
                  ↓            ↓
               FAILED      REJECTED
```

- **DISCOVERED** — Job listing found and parsed
- **TAILORED** — Resume/cover letter generated
- **PENDING_REVIEW** — Awaiting human approval
- **STAGED** — Approved, ready for browser submission
- **SUBMITTED** — Successfully submitted
- **OUTREACH_PENDING** — Recruiter contact staged

## Security

- **AES-256-GCM** encryption for all PII at rest
- **PII tokenization** — dynamic masking of sensitive fields
- **No secrets in code** — all credentials via environment variables
- **Human-in-the-loop** gate before every submission
- **Zero-trust hallucination checking** — cross-verifies every generated claim against the master profile

## License

MIT — See [LICENSE](LICENSE) for details.
