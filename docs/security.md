---
title: GetAJob Security Documentation
description: Security architecture, encryption standards, PII protection, and secure development practices
status: active
last_reviewed: 2026-06-24
---

# GetAJob — Security Documentation

> **Version:** 0.1.0 (Pre-Alpha)
> **Last Updated:** 2026-06-24

---

## Table of Contents

1. [Security Overview](#security-overview)
2. [Encryption at Rest](#encryption-at-rest)
3. [PII Tokenization](#pii-tokenization)
4. [Human-in-the-Loop Validation Gateway](#human-in-the-loop-validation-gateway)
5. [Secrets Management](#secrets-management)
6. [Secure Coding Practices](#secure-coding-practices)
7. [Dependency Vulnerability Scanning](#dependency-vulnerability-scanning)
8. [Threat Model](#threat-model)
9. [Known Security Gaps](#known-security-gaps)
10. [Security Checklist](#security-checklist)

---

## Security Overview

GetAJob handles sensitive personal data (name, email, phone, work history) and interacts with external platforms under user credentials. The security architecture follows a **defense-in-depth** approach:

| Layer | Control | Status |
|-------|---------|--------|
| **Data at Rest** | AES-256-GCM encryption + PBKDF2 key derivation | ✅ Implemented |
| **PII** | Field-level tokenization with HMAC-SHA256 | ✅ Implemented |
| **Secrets** | Environment variables, never in source code | ✅ Implemented |
| **HITL Gateway** | Mandatory human review before any external action | ⚠️ UI built, auth pending |
| **Transport** | HTTPS for web UI, API keys via env vars | ⚠️ Implicit (varies per deployment) |
| **Authentication** | Session-based (single user, local) | ❌ Not implemented |
| **Audit Logging** | Structured event log for all state transitions | ✅ Implemented |
| **Supply Chain** | Minimum-version deps + lockfile | ⚠️ No CI scanning |

---

## Encryption at Rest

### Algorithm

All Personally Identifiable Information (PII) is encrypted at rest using **AES-256-GCM** (Galois/Counter Mode):

- **Cipher:** AES with 256-bit key
- **Mode:** GCM (authenticated encryption — provides both confidentiality and integrity)
- **Nonce:** 12 bytes (random, generated per encryption operation)
- **Tag:** 16 bytes (authentication tag, verified on decryption)
- **Output Format:** Binary concatenation of `nonce || ciphertext || tag`, base64-encoded for storage

### Key Derivation

The encryption key is derived from a user-supplied passphrase using **PBKDF2-HMAC-SHA256**:

```python
# core/security.py — KeyDerivation
def derive_key(password: str, salt: bytes, iterations: int = 600_000) -> bytes:
    """Derive a 256-bit AES key from a password using PBKDF2."""
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
```

- **Iterations:** 600,000 (OWASP 2023 recommendation for PBKDF2-HMAC-SHA256)
- **Salt:** 16 bytes (random, stored alongside encrypted data)
- **Output:** 32 bytes (256 bits)

### Implementation

```python
# core/security.py — Encrypt
def encrypt_value(plaintext: str, key: bytes) -> str:
    iv = os.urandom(12)           # 96-bit nonce
    cipher = Cipher(algorithms.AES(key), modes.GCM(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(plaintext.encode()) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext + encryptor.tag).decode()
```

### Key Management

| Key | Source | Storage | Rotation |
|-----|--------|---------|----------|
| Encryption passphrase | `ENCRYPTION_KEY` env var | Environment variable (`.env`) | Manual — re-encrypt all PII on change |
| Encryption salt | `ENCRYPTION_SALT` env var (hex) | Environment variable | Manual — affects all future key derivations |
| HMAC key (tokenization) | Derived from encryption key | Runtime only | Tied to encryption key rotation |

> **⚠️ Important:** If `ENCRYPTION_SALT` is not configured, the system falls back to an all-zero salt (`"00" * 16`). This is a **known security gap** — it enables rainbow-table precomputation against the PBKDF2 derivation. **Always configure a unique, random salt in production.**

---

## PII Tokenization

### Approach

GetAJob uses **field-level tokenization** to allow safe logging and display of sensitive data without exposing the raw values. The implementation uses **HMAC-SHA256** with an application-specific key:

```python
# core/security.py — Tokenization
def tokenize_pii(value: str, key: bytes, store: dict[str, str] | None = None) -> str:
    """Create a reversible or verifiable token for a PII value."""
    token = hashlib.sha256(key + value.encode()).hexdigest()[:16]
    if store is not None:
        store[token] = value  # reversible only with store access
    return token
```

### Token Types

| Type | Reversible? | Use Case |
|------|-------------|----------|
| With `store` dict | ✅ Yes (lookup) | In-memory mapping during a session — logs show token, active session can resolve |
| Without `store` | ❌ No (verify only) | Long-term audit logs — `detokenize_pii()` verifies a value matches a token |

### What Gets Tokenized

| Field | Tokenization | Storage |
|-------|-------------|---------|
| Email | Encrypted at rest, tokenized in logs | AES-256-GCM in DB |
| Phone | Encrypted at rest, tokenized in logs | AES-256-GCM in DB |
| Full name | Encrypted at rest | AES-256-GCM in DB |
| Address/Location | Encrypted at rest | AES-256-GCM in DB |
| Application IDs | Not PII (UUID) | Plain text |

### PII in Transit

- **Between services:** All API communication uses PII-minimized schemas — tokens instead of raw values where possible
- **To LLM providers:** Profile data sent to Claude API for tailoring is filtered to only what's relevant (NOT full PII). The system never sends encryption keys or tokens to LLM providers.

---

## Human-in-the-Loop Validation Gateway

### Purpose

The HITL gateway is a **mandatory review step** before any application is submitted or any outreach message is sent. No automated action touches an external platform without human confirmation.

### Architecture

```
Application (state: PENDING_REVIEW)
              │
              ▼
┌──────────────────────────────────────┐
│        Approval Queue (FastAPI)       │
│                                      │
│  ┌──────────────────────────────┐   │
│  │  Dashboard: Pending items    │   │
│  │  - Job details + score      │   │
│  │  - Tailored resume preview  │   │
│  │  - Cover letter preview     │   │
│  │  - Recruiter info (if any)  │   │
│  └──────────────────────┬───────┘   │
│                         │            │
│           ┌─────────────┼──────┐    │
│           │ Approve     │Reject│    │
│           └──────┬──────┴──┬───┘    │
│                  │         │        │
└──────────────────┼─────────┼────────┘
                   │         │
                   ▼         ▼
              ┌────────┐ ┌────────┐
              │ STAGED │ │REJECTED│
              └────────┘ └────────┘
```

### Security Properties

| Property | Implementation | Status |
|----------|---------------|--------|
| **Mandatory gate** | Browser engine pauses at "Submit" and waits for HITL signal | ✅ Implemented (file-based gate) |
| **Audit trail** | Every transition logged in `ApplicationEvent` with timestamp and outcome | ✅ Implemented |
| **Bulk approval** | Supported with explicit confirmation step | ✅ Implemented |
| **Auth required** | Only authorized user can approve/reject | ❌ Not implemented |

### Current Gap

The browser engine's HITL gate (`form_filler.py:412`) uses a file-based polling mechanism (`.approval-gate` file on disk) that is **independent** of the web-based approval queue. These two systems are not connected — the file gate is a development placeholder.

---

## Secrets Management

### Policy

**No secrets in source code.** The `env.template` contains placeholder values only — real credentials are provided via environment variables at runtime.

### What Constitutes a Secret

| Secret | Env Variable | In `.gitignore`? |
|--------|-------------|------------------|
| Anthropic API Key | `ANTHROPIC_API_KEY` | ✅ (in `.env`) |
| Encryption Key | `ENCRYPTION_KEY` | ✅ (in `.env`) |
| Encryption Salt | `ENCRYPTION_SALT` | ✅ (in `.env`) |
| Database URL | `DATABASE_URL` | ✅ (in `.env`) |
| Redis URL | `REDIS_URL` | ✅ (in `.env`) |
| Session Secret | `SESSION_SECRET` | ✅ (in `.env`) |
| Any `.env` file | — | ✅ (`.gitignore` covers `.env`) |

### Development Workflow

```bash
# 1. Copy template (no secrets)
cp env.template .env

# 2. Edit .env with real credentials
#    .env is in .gitignore — never committed

# 3. Set CI/CD secrets via platform UI or CLI
#    GitHub Actions: Settings → Secrets and variables → Actions
```

### Environment Variable Loading

Config loading follows this precedence (highest to lowest):

1. **Process environment** — already-set env vars (highest priority)
2. **`.env` file** — loaded by `python-dotenv` (must be in `.gitignore`)
3. **YAML config** — `config/settings.yaml` (defaults only, no secrets)

---

## Secure Coding Practices

### Input Validation

All API inputs are validated through Pydantic schemas (`core/schemas.py`). However, some fields have overly permissive validation:

```python
# Current — too permissive
phone: str = Field(max_length=64)      # Accepts anything up to 64 chars
email: str = Field(max_length=512)     # Accepts anything up to 512 chars

# Recommended
phone: str = Field(pattern=r"^\+?[1-9]\d{1,14}$")   # E.164 format
email: str = Field(pattern=r"^[^@]+@[^@]+\.[^@]+$")  # Basic email format
```

### Logging

- **Structured logging** via `structlog` with correlation IDs across all modules
- **No PII in logs** — PII fields are tokenized before entering log records
- **Connection parameters** logged at debug level may leak sensitive info — see `database.py:195-203`

### Browser Security

- **Ephemeral sessions** — no persistent cookies stored in the repository
- **Stealth patches** — canvas/WebGL/navigator fingerprint randomization
- **No credential injection** — credentials are typed via keyboard simulation, never via `input.value=` DOM manipulation

### Error Handling

- Typed exception hierarchy (`core/exceptions.py`) — catch specific errors, not `Exception`
- Graceful degradation — if one job board fails, others continue
- No sensitive data in exception messages
- Broad `except Exception` in orchestrator isolates module failures

---

## Dependency Vulnerability Scanning

### Current State

- Dependencies specify minimum versions (not pinned exact versions) in `pyproject.toml`
- `uv.lock` provides reproducible builds
- **No automated vulnerability scanning** in CI as of 2026-06-24

### Recommendations

#### 1. Add `pip-audit` to CI

```yaml
# .github/workflows/ci.yml
- name: Scan dependencies for vulnerabilities
  run: |
    pip install pip-audit
    pip-audit --requirement pyproject.toml
```

#### 2. Add Safety CLI (alternative)

```bash
pip install safety
safety check -r <(uv export --format requirements)
```

#### 3. Dependabot / Renovate

Enable GitHub Dependabot for automatic dependency update PRs. Configure `.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    open-pull-requests-limit: 10
```

#### 4. Pre-Commit Hook for Secrets

The `.pre-commit-config.yaml` includes `detect-secrets` to catch accidental secret commits before they reach the remote.

---

## Threat Model

### Assets

| Asset | Sensitivity | Location |
|-------|-------------|----------|
| User's full name, email, phone | 🔴 HIGH | Encrypted in PostgreSQL |
| Work history & skills | 🟡 MEDIUM | Encrypted in PostgreSQL |
| API keys (Anthropic, etc.) | 🔴 HIGH | Environment variables |
| Browser session cookies | 🟡 MEDIUM | Ephemeral (in-memory) |
| Job application data | 🟡 MEDIUM | PostgreSQL |
| Recruiter contact info | 🟢 LOW | PostgreSQL |

### Threat Scenarios

| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|------------|
| DB dump exposes PII | Low | High | AES-256-GCM encryption at rest |
| Attacker gains local access | Medium | High | Environment-based secrets, no hardcoded keys |
| Secret committed to git | Low | High | `.gitignore`, `detect-secrets` pre-commit hook |
| LLM prompt injection | Medium | Medium | Truth validator cross-checks claims against profile |
| Browser automation detected | Medium | Medium | Stealth patches, human-mimicking behavior |
| Dependency vulnerability | Medium | Medium | Minimum versions, lockfile, recommended scanning |
| Unauthorized approval | Medium | High | HITL auth not yet implemented (known gap) |

---

## Known Security Gaps

These items were identified in the [2026-06-24 audit](../audit-findings.md#4-security-audit) and require attention:

| # | Severity | File | Issue | Fix |
|--|----------|------|-------|-----|
| 1 | **MEDIUM** | `profile_store.py:74-77` | Zero-salt PBKDF2 fallback (`"00" * 16`) when `encryption_salt` not configured | Either require the salt or auto-generate and store it on first run |
| 2 | **MEDIUM** | `security.py:205-235` | `detokenize_pii()` without store is verification, not detokenization — misleading API | Rename or provide clear documentation |
| 3 | **LOW** | `contact_finder.py:391-483` | LinkedIn scraping without proxy rotation or rate-limit obfuscation | Add proxy rotation, randomized delays, session management |
| 4 | **LOW** | `approval_queue/` | No authentication — any local user can approve submissions | Implement session-based auth middleware |
| 5 | **LOW** | `routes.py:645-805` | Mock data in production code paths — activates silently if DB unavailable | Gate mock data behind `DEBUG=true` or dev-only module |
| 6 | **LOW** | `database.py:195-203` | Connection event listeners log all kwargs including potentially sensitive params | Filter or sanitize logged kwargs |
| 7 | **LOW** | `core/schemas.py` | `phone` and `email` fields lack format validation (max length only) | Add regex pattern validation |

---

## Security Checklist

### Pre-Deployment

- [ ] Generate a unique `ENCRYPTION_SALT` (32 hex chars: `openssl rand -hex 16`)
- [ ] Generate a strong `ENCRYPTION_KEY` (32+ alphanumeric characters)
- [ ] Verify `ENCRYPTION_SALT` is set — confirm no fallback to all-zero salt
- [ ] Generate `SESSION_SECRET` for FastAPI session middleware
- [ ] Review `env.template` — ensure no real secrets leaked into it
- [ ] Run `detect-secrets scan` over the repository
- [ ] Configure Dependabot or Renovate for dependency updates
- [ ] Run `pip-audit` or `safety check` on current lockfile
- [ ] Enable HTTPS on the approval queue (reverse proxy with TLS termination)

### Per-Release

- [ ] Run `pre-commit run --all-files` before commit
- [ ] Verify no `.env` files in the commit
- [ ] Check `git diff --stat` for unexpected files
- [ ] Review new dependencies for known vulnerabilities
- [ ] Update `docs/security.md` for any security-relevant changes

### Incident Response

- [ ] If encryption key is compromised: rotate key, re-encrypt all PII from backup
- [ ] If API key is leaked: revoke immediately in Anthropic console, rotate in `.env`
- [ ] If database is breached: assess which fields were encrypted vs plaintext, notify affected parties per applicable law

---

*Security document generated 2026-06-24 from codebase audit. This document should be reviewed and updated before any production deployment.*
