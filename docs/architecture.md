---
title: GetAJob Architecture
description: Comprehensive system architecture for the GetAJob Agentic Job Application Platform
status: active
last_reviewed: 2026-06-24
---

# GetAJob — System Architecture

> **Version:** 0.1.0 (Pre-Alpha)
> **Last Updated:** 2026-06-24
> **Audit Status:** ~45% feature completion — see [audit-findings.md](../audit-findings.md)

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture Diagram](#architecture-diagram)
3. [State Machine](#state-machine)
4. [Module Descriptions](#module-descriptions)
5. [Data Flow](#data-flow)
6. [Security Architecture](#security-architecture)
7. [Agent Roles & Orchestration](#agent-roles--orchestration)
8. [Deployment Architecture](#deployment-architecture)
9. [Integration Status](#integration-status)

---

## System Overview

GetAJob is an **agentic job application platform** designed for 2026-era job searching. It automates the full pipeline from job discovery through tailored application submission and recruiter outreach, with mandatory **human-in-the-loop (HITL)** validation at every submission gate.

### Design Goals

| Goal | Approach |
|------|----------|
| **High throughput** | Target 50 applications/day via automated multi-source discovery |
| **ATS-aware** | Per-platform navigation profiles (Workday, Greenhouse, Lever, LinkedIn, Indeed) |
| **Stealthy automation** | Human-mimicking browser interactions — Bezier curves, variable typing speed, canvas fingerprinting noise |
| **Honest tailoring** | Anti-hallucination guardrail cross-checks every generated claim against the master profile |
| **Safe by design** | PII encrypted at rest (AES-256-GCM), HITL gate before any submission, no secrets in code |

### Tech Stack

| Layer | Technology |
|-------|-----------|
| **Language** | Python 3.12+ (async/await throughout) |
| **Orchestration** | Hermes Agent pattern — master loop drives all sub-agents |
| **Web Framework** | FastAPI + Jinja2/HTMX (approval queue only) |
| **Database** | PostgreSQL 16+ via SQLAlchemy 2.0 (async); SQLite for dev/testing |
| **Vector Store** | ChromaDB (local, semantic skill matching) |
| **Event Bus** | Redis (optional, for inter-module pub/sub in production) |
| **Browser Engine** | `browser-use` library wrapping Playwright |
| **AI/LLM** | Claude API (Anthropic) for reasoning; local models for light extraction |
| **Auth** | Session-based (single user, local) — in progress |
| **Monitoring** | structlog (structured logging), Prometheus metrics (optional) |
| **Security** | AES-256-GCM + PBKDF2, PII tokenization, environment-based secrets |

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         Hermes Orchestrator                                │
│                                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Ingestion   │  │   Context    │  │  Tailoring   │  │   Browser    │  │
│  │    Agent     │  │    Agent     │  │    Agent     │  │    Agent     │  │
│  │              │  │              │  │              │  │              │  │
│  │  • LinkedIn  │  │  • Skill     │  │  • Resume    │  │  • Stealth   │  │
│  │  • Indeed    │  │    matching  │  │    gen       │  │    browser   │  │
│  │  • GH        │  │  • Relevance │  │  • Cover     │  │  • ATS       │  │
│  │  • Workday   │  │    scoring   │  │    letter    │  │    profiles  │  │
│  │  • Lever     │  │  • Profile   │  │  • Anti-AI   │  │  • Form fill │  │
│  └──────┬───────┘  │    enrich    │  │  • Truth     │  │  • Human     │  │
│         │          └──────┬───────┘  │    validate  │  │    simulate  │  │
│         │                 │          └──────┬───────┘  └──────┬───────┘  │
│         │                 │                 │                  │         │
│         └────────┬────────┴─────────────────┴──────────────────┘         │
│                  │                            │                           │
│         ┌────────▼────────────────────────────▼───────────────────┐     │
│         │              PostgreSQL State Machine (7 States)         │     │
│         │                                                          │     │
│         │  DISCOVERED → TAILORED → PENDING_REVIEW → STAGED →      │     │
│         │  SUBMITTED → OUTREACH_PENDING                            │     │
│         │                     ↓           ↓                        │     │
│         │                 REJECTED     FAILED                      │     │
│         └────────────────────────────┬─────────────────────────────┘     │
│                                      │                                    │
│         ┌────────────────────────────▼─────────────────────────────┐     │
│         │               Approval Queue (HITL Gateway)               │     │
│         │  FastAPI Web UI  ·  Review Dashboard  ·  Approve/Reject  │     │
│         └──────────────────────────────────────────────────────────┘     │
│                                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                    │
│  │   Profile    │  │  Outreach    │  │  Overseer    │                    │
│  │   Engine     │  │   Engine     │  │   Agent      │                    │
│  │              │  │              │  │              │                    │
│  │  • ChromaDB  │  │  • Contact   │  │  • Guardrail │                    │
│  │  • Immutable │  │    finder    │  │    validation│                    │
│  │    profile   │  │  • Message   │  │  • Policy    │                    │
│  │  • PII       │  │    gen       │  │    checks    │                    │
│  │    encrypted │  │  • Email     │  │              │                    │
│  │              │  │    validate  │  │              │                    │
│  └──────────────┘  └──────────────┘  └──────────────┘                    │
└──────────────────────────────────────────────────────────────────────────┘
```

### Component Boundary Diagram

```
┌─────────────────────────────────────────────────────┐
│                   External World                      │
│                                                       │
│  Job Boards      ATS Platforms     Company Sites      │
│  ┌────────┐    ┌──────────┐     ┌───────────────┐   │
│  │LinkedIn│    │ Workday  │     │Career Pages   │   │
│  │Indeed  │    │Greenhouse│     │"Team" pages   │   │
│  │Lever   │    │          │     │               │   │
│  └───┬────┘    └────┬─────┘     └───────┬───────┘   │
│      │              │                   │           │
└──────┼──────────────┼───────────────────┼───────────┘
       │              │                   │
       ▼              ▼                   ▼
┌──────────────────────────────────────────────────────┐
│                    System Boundary                     │
│                                                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────┐ │
│  │Ingestion │  │  Browser │  │ Outreach │  │ Redis │ │
│  │ Agent    │──│  Engine  │──│  Engine  │  │ Event │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  │ Bus   │ │
│       │             │             │        └───┬───┘ │
│       └─────────────┼─────────────┼────────────┘     │
│                     ▼             ▼                   │
│              ┌──────────────────────────┐             │
│              │  PostgreSQL + ChromaDB   │             │
│              └──────────┬───────────────┘             │
│                         │                             │
│              ┌──────────▼───────────────┐             │
│              │    Approval Queue UI     │             │
│              └──────────────────────────┘             │
└──────────────────────────────────────────────────────┘
```

---

## State Machine

The application lifecycle is governed by a **7-state state machine** with 18 allowed transitions. Every state change is validated against the transition map and recorded in the `ApplicationEvent` audit log.

### State Diagram

```
                        ┌─────────────┐
                        │  DISCOVERED │◄──── Ingestion Agent
                        └──────┬──────┘   finds & parses listing
                               │
                               │ Tailoring Agent generates
                               │ resume + cover letter
                               ▼
                        ┌─────────────┐
                        │   TAILORED  │
                        └──────┬──────┘
                               │
                               │ Context Agent validates match
                               ▼
                     ┌──────────────────┐
                     │ PENDING_REVIEW   │◄──── HUMAN-IN-THE-LOOP
                     │                  │      (Approval Queue)
                     └──┬───────┬───────┘
                        │       │
               Approved │       │ Rejected
                        │       ▼
                        │  ┌──────────┐
                        ▼  │ REJECTED │ (terminal)
                  ┌────────┴──────────┘
                  │   STAGED
                  │   (ready for browser
                  │    submission)
                  │
                  │ Browser Engine
                  │ submits application
                  ▼
            ┌─────────────┐
            │  SUBMITTED  │
            └──────┬──────┘
                   │
                   │ Outreach Engine
                   │ discovers recruiter
                   ▼
            ┌──────────────────┐
            │ OUTREACH_PENDING │
            │ (terminal —      │
            │  awaits human    │
            │  review before   │
            │  sending message)│
            └──────────────────┘

    Any state ──────► ┌────────┐
        on error      │ FAILED │ (terminal)
                       └────────┘
```

### Transition Map

| From | To | Trigger | Agent/Component |
|------|----|---------|-----------------|
| `DISCOVERED` | `TAILORED` | Resume/cover letter generated | Tailoring Agent |
| `TAILORED` | `PENDING_REVIEW` | Match validated, ready for review | Context Agent |
| `PENDING_REVIEW` | `STAGED` | Human approves | Approval Queue (HITL) |
| `PENDING_REVIEW` | `REJECTED` | Human rejects | Approval Queue (HITL) |
| `STAGED` | `SUBMITTED` | Browser submits form | Browser Engine |
| `SUBMITTED` | `OUTREACH_PENDING` | Recruiter contact found | Outreach Engine |
| Any | `FAILED` | Unrecoverable error | Any component |
| `TAILORED` | `FAILED` | Hallucination check fails | Tailoring Agent |
| `STAGED` | `FAILED` | Browser submission error | Browser Engine |

### Implementation Status

> **⚠️ Note:** The state machine definition is fully implemented in `core/state_machine.py`, but only 2 of 7 states are reachable through the automated pipeline as of 2026-06-24. See the [audit findings](../audit-findings.md#appendix-state-machine-implementation-status) for details.

| Transition | Status | Notes |
|-----------|--------|-------|
| `DISCOVERED → TAILORED` | ❌ Not wired | Orchestrator stops at `DISCOVERED` |
| `TAILORED → PENDING_REVIEW` | ❌ Not wired | No agent advances past `TAILORED` |
| `PENDING_REVIEW → STAGED` | ✅ Implemented | Via approval queue API |
| `STAGED → SUBMITTED` | ❌ Not wired | Browser agent not integrated |
| `SUBMITTED → OUTREACH_PENDING` | ❌ Not wired | Outreach not integrated |
| Any → `FAILED` | ❌ Not wired | No systematic error handling |
| Any → `REJECTED` | ✅ Implemented | Via approval queue API |

---

## Module Descriptions

### Core Framework (`core/`)

Shared infrastructure used by all modules.

| Module | Responsibility | Key Classes/Functions |
|--------|---------------|----------------------|
| `config.py` | Multi-source config (env, YAML, .env) | `Settings`, `get_settings()` (singleton) |
| `database.py` | Async SQLAlchemy engine, session lifecycle | `create_engine()`, `get_session()`, `run_migrations()` |
| `models.py` | ORM models (5 entities) | `JobListing`, `UserProfile`, `WorkExperience`, `Application`, `ApplicationEvent` |
| `schemas.py` | Pydantic request/response schemas | Create/Read/Update pattern, paginated responses |
| `security.py` | Encryption + PII tokenization | `encrypt_value()`, `decrypt_value()`, `tokenize_pii()`, `detokenize_pii()` |
| `state_machine.py` | 7-state transition engine | `ApplicationState` enum, `transition_state()`, `get_allowed_transitions()` |
| `event_bus.py` | Pub/sub event system | `InMemoryEventBus`, `RedisEventBus`, event type constants |
| `llm_client.py` | Unified LLM abstraction | `LLMClient` ABC, `ClaudeAPIClient`, `MockLLMClient` |
| `exceptions.py` | Typed exception hierarchy | 8 exception types (e.g., `StateTransitionError`, `EncryptionError`) |

### Ingestion Agent (`agents/ingestion_agent.py`)

**Purpose:** Query job boards and ATS platforms to discover relevant job listings.

**Capabilities:**
- Multi-source dispatch to API-based sources (Greenhouse, Lever)
- Browser-based source stubs (LinkedIn, Indeed, Workday — awaiting integration with Browser Engine)
- Rate-limit respecting token bucket per source
- Deduplication by company + title across sources
- Standardized output schema with skills extraction

**Known Gaps:**
- `time.sleep()` in token bucket blocks the event loop (should be `asyncio.sleep()`)
- Browser-based sources produce no results (stubbed with log message)
- Company resolution always returns empty list

### Context Agent (`agents/context_agent.py`)

**Purpose:** Analyze job descriptions against the user's profile to compute match scores and extract requirements.

**Capabilities:**
- Semantic relevance scoring via ChromaDB vector search
- Skill matching and gap analysis
- Experience requirement extraction
- Match score computation (0.0–1.0)

**Known Issues:**
- Circular import workaround places core imports at file bottom (fragile)
- `run()` returns placeholder — agent only works via direct `analyze()` calls

### Tailoring Agent (`agents/tailoring_agent.py`)

**Purpose:** Orchestrate resume and cover letter generation for specific job listings.

**Capabilities:**
- Loads job description + profile context
- Dispatches to Tailoring Engine for generation
- Coordinates hallucination validation

**Known Issues:**
- `run()` returns placeholder — orchestrator never calls `tailor()`
- Duplicated profile-loading code (shared with Context Agent)
- Company-hallucination check disabled ("too noisy")

### Tailoring Engine (`tailoring_engine/`)

**Purpose:** Generate tailored resumes and cover letters with anti-hallucination and anti-AI-detection guardrails.

| Module | Responsibility |
|--------|---------------|
| `resume_generator.py` | LLM-based resume generation with post-processing |
| `cover_letter_generator.py` | LLM-based cover letter with cliché stripping |
| `anti_ai_detector.py` | Blocklist + sentence-length + vocabulary analysis |
| `truth_validator.py` | Claim extraction and cross-reference against master profile |

**Known Gaps:**
- No PDF output (plain text/markdown only)
- Hallucination score computation yields false passes when no claims detected
- Anti-AI blocklist is hardcoded (80+ phrases) rather than system-prompt-driven

### Browser Engine (`browser_engine/`)

**Purpose:** Drive browser-use to stealthily navigate job portals, detect ATS type, fill forms, and submit applications.

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `stealth_browser.py` | 259 | Stealth Chromium factory — canvas/WebGL/navigator patches |
| `human_simulator.py` | 330 | Bezier curves, typing delays, typos, scrolling |
| `ats_detector.py` | 348 | URL/DOM/form-signal ATS detection |
| `form_filler.py` | 434 | Central form-fill coordinator with ATS dispatch |
| `selectors.py` | 449 | CSS/XPath fallback chains + dynamic label scanning |
| `ats_profiles/` | 5 files | Per-ATS navigation profiles (Workday, Greenhouse, Lever, LinkedIn, Indeed, Generic) |

**Critical Known Bug:**
ATS-specific handlers register themselves via module-level side effects in `PROFILE_HANDLER_REGISTRY`, but **no code imports these handler modules**. The registry always contains only `GenericFormHandler`. All ATS-specific automation is dead code until imports are added in `ats_profiles/__init__.py`.

### Outreach Engine (`outreach_engine/`)

**Purpose:** Identify recruiter/hiring manager contact info and generate personalized outreach messages.

| Module | Responsibility |
|--------|---------------|
| `contact_finder.py` | 4 discovery strategies: job listing, LinkedIn, website crawl, email patterns |
| `email_validator.py` | Format, MX, deliverability scoring, disposable/role-based detection |
| `message_generator.py` | LLM-based personalized outreach with anti-AI guardrails |

**Known Issues:**
- LinkedIn scraping may violate ToS (no proxy rotation or obfuscation)
- Never called from the orchestrator pipeline
- Email domain guess is best-effort with no verification

### Profile Engine (`profile_engine/`)

**Purpose:** Hold the user's immutable personal data as a searchable vector knowledge graph.

| Module | Responsibility |
|--------|---------------|
| `profile_store.py` | Immutable CRUD with transparent encryption, versioning, export/import |
| `vector_store.py` | ChromaDB wrapper, semantic search, profile chunk storage |
| `experience_parser.py` | Regex + LLM skill extraction |

**Known Issues:**
- Zero-salt PBKDF2 fallback when `encryption_salt` not configured (security finding)
- "Immutable ledger" pattern overwrites in practice (old versions inaccessible via API)

### Overseer Agent (`agents/overseer_agent.py`)

**Purpose:** Guardrail validation layer — policy checks and cross-cutting validation.

**Current State:** Implemented as a BaseAgent subclass. Not yet integrated into the orchestrator pipeline.

### Outreach Agent (`agents/outreach_agent.py`)

**Purpose:** Orchestrate recruiter discovery and message generation for submitted applications.

**Current State:** Implemented as a BaseAgent subclass. Not yet called from the orchestrator.

### Approval Queue (`approval_queue/`)

**Purpose:** Mandatory human review gateway before any submission or outreach.

**Capabilities:**
- FastAPI web UI (dashboard, review, settings pages)
- Application preview with job details, tailored resume, cover letter
- Approve/Reject/Edit actions
- Bulk approve
- Status filtering

**Known Gaps:**
- No authentication middleware (login template exists, no backend enforcement)
- Mock data paths can silently activate if database is unavailable

### CLI (`getajob/cli.py`)

**Purpose:** Command-line interface for all platform operations.

| Command | Function |
|---------|----------|
| `getajob run` | Full pipeline (discover → tailor → stage) |
| `getajob run --continuous` | Continuous mode (every 15 min) |
| `getajob discover` | Job discovery only |
| `getajob serve` | Start approval queue web UI |
| `getajob tailor <job-id>` | Tailor for specific job |
| `getajob setup` | First-time environment setup |

---

## Data Flow

### Main Pipeline Flow

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  Search   │     │  Fetch   │     │  Match   │     │  Tailor  │     │  Submit  │
│  Vectors  │────►│  Jobs    │────►│  Against │────►│  Resume  │────►│  Via     │
│  (Config) │     │  (API)   │     │  Profile │     │  + CL    │     │  Browser │
└──────────┘     └──────────┘     └──────────┘     └──────────┘     └──────────┘
                                                       │
                                                       ▼
                                                ┌──────────────┐
                                                │  HITL Review │
                                                │  (Approval   │
                                                │   Queue)     │
                                                └──────┬───────┘
                                                       │
                              ┌──────────┐     ┌──────▼───────┐
                              │  Send    │◄────│  Find        │
                              │  Message │     │  Recruiter   │
                              └──────────┘     └──────────────┘
```

### Detailed Data Flow per Pipeline Stage

#### Stage 1: Discovery (Ingestion Agent → Database)

```
Search Vectors ──► IngestionAgent.run()
                        │
                        ├──► Greenhouse API ──► Parse JSON
                        ├──► Lever API ──────► Parse JSON
                        ├──► LinkedIn (stub) ──► (awaiting browser engine)
                        ├──► Indeed (stub) ───► (awaiting browser engine)
                        ├──► Workday (stub) ──► (awaiting browser engine)
                        │
                        ▼
                  Deduplicate (company + title)
                        │
                        ▼
                  JobListing ──► PostgreSQL (state: DISCOVERED)
                        │
                        ▼
                  Emit: JOB_DISCOVERED (event bus)
```

#### Stage 2: Context Analysis (Context Agent → Profile)

```
New JobListing ──► ContextAgent.analyze()
                        │
                        ├──► Extract skills (regex + LLM)
                        ├──► VectorStore.semantic_search(JD, profile)
                        ├──► Compute match_score
                        │
                        ▼
                  ProfileEnrichment ──► PostgreSQL
                        │
                        ▼
                  Return: {analysis_id, match_score, matched_skills, gaps}
```

#### Stage 3: Tailoring (Tailoring Engine → Profile)

```
JobListing + MatchResult ──► TailoringAgent.tailor()
                        │
                        ├──► _load_profile_data() ──► ProfileStore
                        ├──► ResumeGenerator.generate(JD, profile)
                        │       │
                        │       ├──► LLM: generate tailored resume
                        │       ├──► TruthValidator.validate(resume, profile)
                        │       └──► AntiAIDetector.analyze(resume)
                        │
                        ├──► CoverLetterGenerator.generate(JD, profile)
                        │       │
                        │       ├──► LLM: generate cover letter
                        │       ├──► TruthValidator.validate(cl, profile)
                        │       └──► AntiAIDetector.analyze(cl)
                        │
                        ▼
                  Application ──► PostgreSQL (state: TAILORED → PENDING_REVIEW)
                  TailoredResume + CoverLetter stored
```

#### Stage 4: Browser Submission (Browser Engine)

```
Application (state: STAGED) ──► BrowserAgent.submit()
                        │
                        ├──► FormFiller.fill(application_url, profile)
                        │       │
                        │       ├──► ATSDetector.detect(url)
                        │       ├──► Select ATSProfile handler
                        │       ├──► StealthBrowser.launch()
                        │       ├──► HumanSimulator.navigate()
                        │       ├──► Form field mapping + fill
                        │       └──► HITL pause before submit
                        │
                        ├──► WaitForHumanApproval() ↔ Approval Queue
                        │
                        ▼
                  Application ──► PostgreSQL (state: SUBMITTED, or FAILED)
```

#### Stage 5: Outreach (Outreach Engine)

```
Application (state: SUBMITTED) ──► OutreachAgent.execute()
                        │
                        ├──► ContactFinder.find(company, title)
                        │       │
                        │       ├──► Strategy: job listing scan
                        │       ├──► Strategy: LinkedIn search
                        │       ├──► Strategy: website crawl
                        │       └──► Strategy: email pattern guess
                        │
                        ├──► EmailValidator.validate(contact.email)
                        │
                        ├──► MessageGenerator.generate(application, contact)
                        │       │
                        │       ├──► LLM: personalized outreach message
                        │       └──► AntiAIDetector.analyze(message)
                        │
                        ▼
                  Application ──► PostgreSQL (state: OUTREACH_PENDING)
                  RecruiterContact + DraftMessage stored
```

---

## Security Architecture

See [docs/security.md](security.md) for the complete security documentation.

### Summary

| Layer | Mechanism | Status |
|-------|-----------|--------|
| **At Rest** | AES-256-GCM with PBKDF2 key derivation | ✅ Implemented |
| **PII** | Field-level tokenization with HMAC-SHA256 | ✅ Implemented |
| **Secrets** | Environment variables, never in code | ✅ Implemented |
| **HITL** | Mandatory human review before submission | ✅ UI exists, auth pending |
| **Config** | Encryption fallback salt not configured → all-zero salt | ⚠️ Known issue |
| **Auth** | Session-based (single user, local) | ❌ Backend not implemented |
| **Dependencies** | Pinned minimum versions, uv.lock lockfile | ⚠️ No vuln scanning in CI |

---

## Agent Roles & Orchestration

### Orchestrator (Hermes Master Loop)

The orchestrator follows a **Hermes Agent** pattern — a master loop daemon that:

1. **Periodically wakes** (configurable interval, default 15 min)
2. **Runs the pipeline stages** in order
3. **Emits events** on the event bus for each stage transition
4. **Collects results** and persists to the database
5. **Handles errors** per-stage with graceful degradation

```
┌─────────────────────────────────────────────────────┐
│                 Orchestrator Loop                     │
│                                                       │
│  while True:                                          │
│      jobs = IngestionAgent.run()       # Discover     │
│      for job in jobs:                                 │
│          analysis = ContextAgent.analyze(job)         │
│          if analysis.match_score >= threshold:        │
│              app = TailoringAgent.tailor(job, analysis)│
│              app.state = PENDING_REVIEW               │
│                                                       │
│      # Separate loop for approved apps:              │
│      pending = db.get_applications(STAGED)            │
│      for app in pending:                              │
│          BrowserEngine.submit(app)                    │
│          OutreachEngine.execute(app)                  │
│                                                       │
│      sleep(interval)                                  │
└─────────────────────────────────────────────────────┘
```

### Agent Communication

Agents communicate through two mechanisms:

1. **Direct orchestration** — The orchestrator calls agent methods directly in the pipeline
2. **Event bus** — Agents emit events for async consumers:

| Event | Emitter | Consumer(s) |
|-------|---------|-------------|
| `JOB_DISCOVERED` | Ingestion Agent | Context Agent |
| `ANALYSIS_COMPLETE` | Context Agent | Tailoring Agent |
| `TAILORING_COMPLETE` | Tailoring Agent | Approval Queue |
| `APPLICATION_SUBMITTED` | Browser Engine | Outreach Agent |
| `OUTREACH_STAGED` | Outreach Engine | Approval Queue |

### Current Orchestration Status

As of 2026-06-24, the orchestrator (`agents/orchestrator_agent.py`):

- ✅ Runs Ingestion Agent (discovery)
- ✅ Runs Context Agent analysis on new listings
- ✅ Creates Application records in `DISCOVERED` state
- ✅ Emits `JOB_DISCOVERED` event
- ❌ Does NOT call Tailoring Agent
- ❌ Does NOT advance applications past `DISCOVERED`
- ❌ Does NOT invoke Browser Engine for submissions
- ❌ Does NOT invoke Outreach Engine for contact discovery

---

## Deployment Architecture

See [docs/deployment.md](deployment.md) for full deployment details.

### Development Topology

```
┌─────────┐     ┌──────────┐     ┌──────────┐
│  CLI     │────►│  Python  │────►│ SQLite   │
│  (typer) │     │  Runtime │     │ (dev)    │
└─────────┘     └──────────┘     └──────────┘
                      │
                      ▼
               ┌──────────────┐
               │ ChromaDB     │
               │ (local dir)  │
               └──────────────┘
```

### Production Topology

```
                         ┌─────────────────────┐
                         │   Load Balancer      │
                         │   (optional)         │
                         └──────────┬──────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
              ┌─────▼─────┐                   ┌─────▼─────┐
              │  Worker 1  │                   │  Worker 2  │
              │ (Pipeline) │                   │ (Pipeline) │
              └─────┬─────┘                   └─────┬─────┘
                    │                               │
                    └───────────────┬───────────────┘
                                    │
                    ┌───────────────▼────────────────┐
                    │        PostgreSQL (Primary)     │
                    │   + Redis (Event Bus + Cache)   │
                    └───────────────┬─────────────────┘
                                    │
                    ┌───────────────▼────────────────┐
                    │   Approval Queue (FastAPI)      │
                    │   Port 8080 · Session Auth      │
                    └────────────────────────────────┘
```

### Recommended Infrastructure

| Component | Dev | Production |
|-----------|-----|------------|
| **Database** | SQLite (aiosqlite) | PostgreSQL 16+ (asyncpg) |
| **Event Bus** | In-memory | Redis 7+ |
| **Vector Store** | ChromDB (local) | ChromaDB (persistent) |
| **Browser** | Local Chromium | Ephemeral container per job |
| **Proxies** | None | Residential proxy rotation |
| **LLM** | MockLLMClient (dev) / Claude API | Claude API |
| **Auth** | None (dev) | Session-based + optional SSO |

---

## Known Architectural Debt

The following items represent the highest-priority architectural gaps identified in the [2026-06-24 audit](../audit-findings.md):

| Priority | Issue | Impact |
|----------|-------|--------|
| **P0** | ATS handler modules never imported — `PROFILE_HANDLER_REGISTRY` always empty except Generic | All ATS-specific automation dead code |
| **P0** | Orchestrator stops at `DISCOVERED` — no post-discovery pipeline | 5 of 7 states unreachable |
| **P1** | `time.sleep()` in async token bucket | Blocks event loop |
| **P1** | LinkedIn/Indeed/Workday discovery stubbed | ~60% of job sources produce nothing |
| **P2** | Zero-salt fallback in PBKDF2 | Weakens encryption |
| **P2** | No approval queue authentication | Anyone on localhost can approve submissions |
| **P2** | No PDF output capability | Resume generation incomplete |
| **P3** | Duplicated profile-loading code | Maintenance burden |
| **P3** | Outdated LLM beta header | May cause API errors with newer models |

---

*Architecture document generated from codebase audit 2026-06-24. This document should be updated as integration proceeds.*
