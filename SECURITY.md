# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in GetAJob, please report it privately
by emailing **security@getajob.dev**.

**Do not** open a public GitHub issue for security vulnerabilities.

You can expect:
- An acknowledgement of your report within 48 hours.
- A status update within 5 business days (fixed, triaged, or declined).
- Coordinated disclosure — we will work with you on a timeline for public
  disclosure after a fix is released.

We appreciate your help in keeping GetAJob and its users safe.

---

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| latest  | ✅ Active support  |
| < latest | ❌ Not supported  |

Only the most recent release receives security patches. Users are strongly
encouraged to keep their deployment up to date.

---

## Security Practices

### Encryption at Rest

- All Personally Identifiable Information (PII) stored in the database is
  encrypted with **AES-256-GCM** using a random 12-byte nonce per value.
- Encryption keys are derived from a user-supplied password via
  **PBKDF2-HMAC-SHA256** with 600 000 iterations (OWASP 2023 recommendation).
- PII fields (email, phone) can be tokenized via **HMAC-SHA256** for use as
  deterministic lookup keys while keeping plaintext out of the database.

### Authentication & Authorization

- The **Approval Queue** web UI uses session-based authentication (single user,
  local-only deployment). The approval password is configured via the
  `GETAJOB_SECURITY__APPROVAL_PASSWORD` environment variable.
- The platform does **not** expose a public API — all browser automation,
  ingestion, and outreach are driven by the internal Hermes agent loop.
- LLM API keys and database passwords are loaded exclusively from environment
  variables or `.env` — never hardcoded.

### Dependency Management

- Dependencies are pinned in `uv.lock` with minimum-version constraints in
  `pyproject.toml`.
- CI runs **`pip-audit`** on every push and pull request to detect known
  vulnerabilities in the dependency tree.
- **Dependabot** (or equivalent) is configured to open automated PRs for
  vulnerable or outdated dependencies.

### Static Analysis

- **Bandit** scans all production code for common security issues (injection,
  hardcoded passwords, insecure crypto) as part of the CI pipeline.
- **CodeQL** performs interprocedural data-flow analysis to detect taint-style
  vulnerabilities (XSS, SQL injection, path traversal).
- **TruffleHog** scans for accidentally committed secrets (API keys, tokens,
  credentials) on every push.
- **Secrets are verified** — any secret found in the working tree fails CI.

### Runtime Safety

- The browser automation engine uses **ephemeral browser profiles** — no
  persistent cookies or session data is stored in the repository.
- Rate limiting is enforced per job source to respect platform terms of service.
- All HTTP requests from the outreach engine respect `robots.txt` by default.

---

## Known Security Limitations

1. **Local-only deployment.** The Approval Queue web UI is designed for a
   single-user, local-network deployment. It has not been hardened for
   multi-tenant or public-internet exposure.

2. **No audit log encryption.** The application audit log (submission timestamps,
   outcomes) is stored in plaintext in the database to support operational
   dashboards. It does not contain PII.

3. **Browser profile isolation.** While browser profiles are ephemeral, the
   underlying host OS is responsible for process-level isolation. In
   multi-tenant environments, containerization (Docker) is recommended.

4. **LLM prompt injection.** The platform sends untrusted job descriptions to
   LLMs for tailoring. While the truth validator cross-checks claims against
   the master profile, prompt-injected content in job descriptions could
   theoretically influence output. Reviewed outputs are the HITL safeguard.

5. **OSINT rate limits.** The contact-finder module performs public-web lookups
   for recruiter information. While rate-limited, it could still pattern-match
   against rate-limit thresholds of third-party services.

---

## Encryption Key Rotation

To rotate the encryption key:

1. Generate a new key with `generate_key()` (see `core/security.py`).
2. Re-encrypt all PII fields in the database with the new key.
3. Update the `GETAJOB_SECURITY__ENCRYPTION_KEY` environment variable.
4. Verify decryption works by running the test suite.

A key-rotation script is available under `scripts/` (forthcoming).

---

## Responsible Disclosure

We ask that researchers:
- Give us a reasonable time to fix and disclose before publishing.
- Do not access or modify user data without permission.
- Do not perform tests that degrade service availability.
- Follow all applicable laws.

Thank you for helping keep GetAJob secure.
