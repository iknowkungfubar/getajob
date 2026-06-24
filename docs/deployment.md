---
title: GetAJob Deployment Guide
description: Deployment and operations guide for the GetAJob platform
status: active
last_reviewed: 2026-06-24
---

# GetAJob — Deployment Guide

> **Version:** 0.1.0 (Pre-Alpha)
> **Last Updated:** 2026-06-24

---

## Table of Contents

1. [Local Development Setup](#local-development-setup)
2. [Production Requirements](#production-requirements)
3. [Environment Variables Reference](#environment-variables-reference)
4. [Docker Deployment](#docker-deployment)
5. [Manual Deployment](#manual-deployment)
6. [Security Checklist](#security-checklist)
7. [Monitoring & Logging](#monitoring--logging)
8. [Backup & Recovery](#backup--recovery)
9. [Troubleshooting](#troubleshooting)

---

## Local Development Setup

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.12+ | Required — 3.13 works if available |
| PostgreSQL | 16+ | Optional for dev (SQLite fallback) |
| Redis | 7+ | Optional for dev (in-memory fallback) |
| Playwright | Latest | `playwright install chromium` |
| uv | Latest | Recommended package manager |

### Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/iknowkungfubar/getajob.git
cd getajob

# 2. Create environment
uv python pin 3.12              # Ensure 3.12
uv sync --group dev              # Install all dependencies including dev

# 3. Install Playwright browser
playwright install chromium

# 4. Configure environment
cp env.template .env
# Edit .env with your credentials (see Environment Variables Reference below)

# 5. Run database migrations
getajob setup

# 6. Verify installation
getajob discover --dry-run       # Test job discovery without submitting
getajob serve                    # Start approval queue on http://localhost:8080

# 7. Run tests
pytest                           # Run all tests
pytest --cov                     # With coverage report
pytest tests/integration/        # Integration tests only
```

### Database Setup (Development)

```bash
# Option A: SQLite (simplest, no external service)
export DATABASE_URL="sqlite+aiosqlite:///data/getajob.db"
getajob setup

# Option B: PostgreSQL (feature-complete)
createdb getajob_dev
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/getajob_dev"
getajob setup
```

### Pre-Commit Hooks Setup

```bash
# Install pre-commit hooks
pre-commit install

# (Optional) Install commit-msg hooks too
pre-commit install --hook-type commit-msg

# Run all hooks on the whole codebase to verify setup
pre-commit run --all-files
```

---

## Production Requirements

### Minimum Infrastructure

| Component | Requirement | High-Availability |
|-----------|-------------|-------------------|
| **CPU** | 4 cores (x86-64-v3+) | 8+ cores |
| **RAM** | 8 GB | 16+ GB |
| **Disk** | 50 GB SSD | 100+ GB with RAID |
| **Python** | 3.12+ | Same |
| **PostgreSQL** | 16+ | Primary + Streaming replica |
| **Redis** | 7+ | Primary + Sentinel / Cluster |
| **ChromaDB** | Local persistent | N/A (ephemeral acceptable) |
| **Browser** | Chromium (via Playwright) | Ephemeral container per job |
| **Network** | Outbound HTTPS, residential proxies | Multiple proxy pools |

### Production Database Configuration

```ini
# postgresql.conf — Recommended settings for GetAJob
max_connections = 50
shared_buffers = '2GB'          # 25% of available RAM
effective_cache_size = '6GB'    # 75% of available RAM
work_mem = '64MB'               # Per-operation sort/hash memory
maintenance_work_mem = '512MB'
wal_level = replica             # Required for replication
max_wal_size = '4GB'
checkpoint_completion_target = 0.9
random_page_cost = 1.1          # SSD optimization
effective_io_concurrency = 200  # SSD optimization
```

### Redis Configuration

```ini
# redis.conf — Recommended settings for GetAJob
maxmemory 2gb
maxmemory-policy allkeys-lru
save 300 10
save 60 10000
appendonly yes
appendfsync everysec
```

---

## Environment Variables Reference

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `GETAJOB_DATABASE__PASSWORD` | PostgreSQL password | `generate a random 32-char string` |
| `GETAJOB_LLM__API_KEY` | Claude / LLM provider API key | `sk-ant-xxxxxxxxxxxxx` |
| `GETAJOB_SECURITY__ENCRYPTION_KEY` | Master key for AES-256-GCM PII encryption (64 hex chars) | `openssl rand -hex 32` |
| `GETAJOB_SECURITY__ENCRYPTION_SALT` | Salt for PBKDF2 key derivation (32 hex chars) | `openssl rand -hex 16` |

### Approval Queue

| Variable | Description | Example |
|----------|-------------|---------|
| `GETAJOB_SECURITY__APPROVAL_PASSWORD` | Password for the HITL approval queue web UI | `generate a strong random string` |

### LLM / AI

| Variable | Description | Example |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Claude API key | `sk-ant-xxxxxxxxxxxxx` |
| `LLM_MODEL` | Claude model ID (default: `claude-sonnet-4-6`) | `claude-sonnet-4-6` |
| `LLM_MAX_TOKENS` | Max output tokens (default: 4096) | `8192` |
| `LLM_TEMPERATURE` | Temperature (default: 0.7) | `0.5` |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | — | Redis connection string for event bus |
| `LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `json` | Log format: `json` or `console` |
| `APPROVAL_QUEUE_HOST` | `0.0.0.0` | Approval queue bind address |
| `APPROVAL_QUEUE_PORT` | `8080` | Approval queue HTTP port |
| `SESSION_SECRET` | — | Secret for session signing (random if unset) |
| `BROWSER_HEADLESS` | `true` | Run browser in headless mode |
| `BROWSER_PROXY_URL` | — | Proxy URL for browser traffic |
| `MAX_APPLICATIONS_PER_DAY` | `50` | Daily application cap |
| `SEARCH_INTERVAL_MINUTES` | `15` | Orchestrator loop interval |
| `MATCH_THRESHOLD` | `0.0` | Minimum match score (0.0–1.0) |
| `TZ` | `UTC` | Timezone |

### Testing

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///test.db` | Test database (set explicitly in CI) |
| `ENCRYPTION_KEY` | — | Must be set for tests that use encryption |
| `ENCRYPTION_SALT` | — | Must be set for tests that use encryption |

---

## Docker Deployment

Docker support is implemented and ready. The setup uses:
- A multi-stage `Dockerfile` (uv builder + slim runtime with Chromium)
- `docker-compose.yml` with PostgreSQL 16, Redis 7, and the application
- An `init` container that runs `getajob setup` once on startup
- A non-root user (`getajob`, UID 1000) inside the container

### Quick Start

```bash
cp env.template .env
# Fill in GETAJOB_LLM__API_KEY, GETAJOB_SECURITY__ENCRYPTION_KEY,
# GETAJOB_SECURITY__ENCRYPTION_SALT, and GETAJOB_DATABASE__PASSWORD

docker compose up -d
docker compose logs -f init   # wait for setup to finish
open http://localhost:8080     # approval queue
```

### Project Structure

```
getajob/
├── Dockerfile              # Multi-stage build
├── docker-compose.yml      # Dev / staging compose config
└── .dockerignore           # Build context exclusions
```

### Files

| File | Purpose |
|------|---------|
| [`Dockerfile`](../Dockerfile) | Multi-stage: builder installs deps via uv, runtime includes Chromium + Playwright |
| [`docker-compose.yml`](../docker-compose.yml) | Full stack: app, postgres:16-alpine, redis:7-alpine, init container |
| [`.dockerignore`](../.dockerignore) | Excludes venvs, caches, secrets, tests, and data from the build context |

### Container Details

| Aspect | Detail |
|--------|--------|
| Base image | `python:3.12-slim` |
| Browser | Chromium (via Playwright), installed to `/opt/ms-playwright` |
| User | `getajob` (non-root, UID 1000) |
| Workdir | `/app` |
| Entrypoint | `getajob` CLI |
| Default command | `serve` (approval queue on port 8080) |
| Healthcheck | `curl -sf http://127.0.0.1:8080/api/health` every 30 s |
| Data volume | `/app/data` (bind-mount for persistence) |

> **⚠️ Chromium in Docker:** The browser engine applies `--no-sandbox`
> automatically when `GETAJOB_BROWSER__HEADLESS=true`.  If you override this
> to `false`, supply `GETAJOB_BROWSER__CHROMIUM_ARGS=--no-sandbox,--disable-dev-shm-usage`.

### Volumes

| Compose Volume | Container Mount | Purpose |
|----------------|----------------|---------|
| `pgdata` | (Postgres data dir) | Database persistence |
| `redisdata` | `/data` | Redis AOF/RDB snapshots |
| `appdata` | `/app/data` | Runtime data (resumes, screenshots) |

---

## Manual Deployment

### Systemd Service

Create `/etc/systemd/system/getajob.service`:

```ini
[Unit]
Description=GetAJob Agentic Job Application Platform
After=network.target postgresql.service redis.service
Wants=postgresql.service redis.service

[Service]
Type=simple
User=getajob
Group=getajob
WorkingDirectory=/opt/getajob

Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/opt/getajob/.env

ExecStart=/opt/getajob/.venv/bin/getajob run --continuous
ExecStartPost=/opt/getajob/.venv/bin/getajob serve

Restart=on-failure
RestartSec=30
TimeoutStopSec=30

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ProtectKernelTunables=true
ProtectControlGroups=true
ReadWritePaths=/opt/getajob/data

[Install]
WantedBy=multi-user.target
```

### Nginx Reverse Proxy

```nginx
server {
    listen 443 ssl;
    server_name getajob.example.com;

    ssl_certificate /etc/ssl/certs/getajob.crt;
    ssl_certificate_key /etc/ssl/private/getajob.key;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket support for HTMX
    location /ws {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

---

## Security Checklist

### Pre-Deployment

- [ ] **Encryption salt configured** — verify `ENCRYPTION_SALT` is a unique random hex string (`openssl rand -hex 16`)
- [ ] **Encryption key configured** — verify `ENCRYPTION_KEY` is set to a strong random value
- [ ] **Database password changed** — not using the default test/dev password
- [ ] **API keys valid** — verify Anthropic API key has appropriate quota
- [ ] **`.env` not in repository** — run `git ls-files | grep .env` to confirm
- [ ] **Session secret configured** — set `SESSION_SECRET` for approval queue sessions
- [ ] **HTTPS enabled** — TLS termination for the approval queue
- [ ] **Firewall configured** — restrict database port (5432) to application server only
- [ ] **Dependencies scanned** — run `pip-audit` or `safety check`
- [ ] **Pre-commit hooks installed** — `pre-commit install`
- [ ] **Secrets baseline initialized** — `detect-secrets scan --baseline .secrets.baseline`

### Production Hardening

- [ ] **Run as unprivileged user** — create a `getajob` system user
- [ ] **Read-only filesystem** — only `data/` directory writable
- [ ] **CORS restrict** — limit approval queue access to localhost or VPN
- [ ] **Rate limiting** — configure nginx `limit_req` for approval queue API
- [ ] **Backup strategy** — automated PostgreSQL dumps + encrypted PII backups
- [ ] **Audit logging** — ship structlog to centralized logging (ELK/Loki)
- [ ] **Proxy rotation** — configure residential proxy pool for browser engine
- [ ] **API key rotation** — schedule periodic key rotation (every 90 days)
- [ ] **Dependency updates** — weekly automated dependency PRs via Dependabot

---

## Monitoring & Logging

### Structured Logging

GetAJob uses `structlog` with correlation IDs across all modules:

```json
{
  "event": "application_submitted",
  "application_id": "0190f123-...",
  "company": "Acme Corp",
  "state": "SUBMITTED",
  "match_score": 0.87,
  "correlation_id": "abc-123-def",
  "duration_ms": 1842,
  "timestamp": "2026-06-24T15:30:00Z",
  "level": "info",
  "logger": "orchestrator_agent"
}
```

### Key Metrics to Monitor

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| Application success rate | Database | < 80% over 1 hour |
| Approval queue latency | FastAPI | P95 > 5 seconds |
| Orchestrator loop duration | Logs | > 10 minutes |
| Browser submission failures | Logs | > 5 consecutive |
| Rate limit hits | Logs | > 10 per hour |
| API token consumption | LLM client | > 80% daily quota |
| PII encryption/decryption errors | Security logs | Any error |

### Log Shipping

```yaml
# Recommended: Filebeat configuration
filebeat.inputs:
  - type: log
    paths:
      - /var/log/getajob/*.json
    json.keys_under_root: true
    json.overwrite_keys: true

output.elasticsearch:
  hosts: ["http://elasticsearch:9200"]
  index: "getajob-%{+yyyy.MM.dd}"

# Alternative: Loki / Promtail
# Alternative: Vector.dev
```

### Prometheus Metrics (Optional)

The FastAPI approval queue can expose Prometheus metrics at `/metrics`:

```python
# Future: Prometheus middleware registration
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator().instrument(app).expose(app)
```

Key metrics to expose:
- `getajob_applications_total{status}` — Application count by status
- `getajob_discovery_count{source}` — Jobs discovered per source
- `getajob_orchestrator_loop_seconds` — Loop duration histogram
- `getajob_llm_tokens_total{model}` — Token consumption
- `getajob_browser_submissions_total{status}` — Browser submission outcomes

---

## Backup & Recovery

### Backup Strategy

```bash
#!/bin/bash
# backup.sh — Scheduled backup (run daily via cron)

BACKUP_DIR="/var/backups/getajob"
DATE=$(date +%Y%m%d_%H%M%S)

# PostgreSQL
pg_dump -Fc getajob > "$BACKUP_DIR/db_$DATE.dump"

# Encrypted PII (from PostgreSQL — already encrypted at rest)
# But keep separate backup for safety:
pg_dump --data-only --table=user_profiles getajob > "$BACKUP_DIR/profiles_$DATE.sql"
gpg --encrypt --recipient admin@example.com "$BACKUP_DIR/profiles_$DATE.sql"

# ChromaDB vector store
tar -czf "$BACKUP_DIR/chromadb_$DATE.tar.gz" /var/lib/chromadb/

# Retention: 7 daily, 4 weekly, 3 monthly
find "$BACKUP_DIR" -name "db_*.dump" -mtime +7 -delete
find "$BACKUP_DIR" -name "chromadb_*.tar.gz" -mtime +7 -delete
```

### Recovery Procedure

```bash
# 1. Restore database
pg_restore -d getajob --clean "$BACKUP_DIR/db_latest.dump"

# 2. Run migrations (ensure schema is current)
getajob setup

# 3. Restore vector store
tar -xzf "$BACKUP_DIR/chromadb_latest.tar.gz" -C /var/lib/chromadb/

# 4. Verify
getajob discover --dry-run
```

### Disaster Recovery Plan

| Scenario | RPO | RTO | Procedure |
|----------|-----|-----|-----------|
| Database corruption | 24 hours | 1 hour | Restore from latest pg_dump |
| Encryption key loss | — | — | **Data unrecoverable** — rotate key and re-encrypt from backup |
| Server failure | 24 hours | 4 hours | Provision new server, restore from S3/gcs backup |
| API key compromise | — | 5 min | Revoke in Anthropic console, update `.env`, restart |

---

## Troubleshooting

### Common Issues

| Symptom | Likely Cause | Solution |
|---------|-------------|----------|
| `ModuleNotFoundError: chromadb` | ChromaDB not installed | `uv sync --group dev` |
| `playwright._impl._errors.Error` | Chromium not installed | `playwright install chromium` |
| `sqlalchemy.exc.OperationalError` | Database unreachable | Check `DATABASE_URL` and database service |
| `EncryptionError: InvalidTag` | Wrong encryption key | Verify `ENCRYPTION_KEY` matches the key used when data was written |
| `RateLimitError` | API rate limit hit | Check `MAX_APPLICATIONS_PER_DAY`, proxy configuration |
| Browser fails to launch | Missing system deps | `playwright install-deps chromium` |
| No jobs discovered | API sources may be empty | Run `getajob discover --dry-run --verbose` to debug |

### Debugging

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG
getajob run

# Run a specific stage in isolation
getajob discover            # Discovery only
getajob tailor <job-id>     # Tailor a specific job

# Verify database
getajob setup --verify

# Test browser automation (dry run)
BROWSER_HEADLESS=true getajob run --dry-run
```

### Getting Help

- **Issues:** https://github.com/iknowkungfubar/getajob/issues
- **Security issues:** Contact project maintainers directly (see security.txt when available)

---

*Deployment guide generated 2026-06-24. Review and update before production deployment.*
