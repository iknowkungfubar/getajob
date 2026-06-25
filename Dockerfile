# ─────────────────────────────────────────────────────────────────────────────
# GetAJob — Dockerfile (multi-stage)
#
# Stage 1 (builder):     install Python deps via uv into a virtualenv
# Stage 2 (runtime):     Python + Chromium + Playwright, non‑root user
#
# Build:
#   docker build -t getajob .
#
# Run (standalone — you still need Postgres/Redis):
#   docker run -it --rm \
#     -e GETAJOB_DATABASE__HOST=host.docker.internal \
#     -e GETAJOB_LLM__API_KEY=sk-ant-... \
#     -v getajob_data:/app/data \
#     getajob serve
#
# ── ⚠️  Chromium in Docker ─────────────────────────────────────────────────
# Chromium requires the `--no-sandbox` flag when running inside a container
# (no user-namespace access by default).  The browser-engine module already
# applies this flag automatically when `headless=true`, but if you override
# it to `false` you must set the environment variable:
#
#   GETAJOB_BROWSER__CHROMIUM_ARGS=--no-sandbox,--disable-dev-shm-usage
#
# For production, always keep `headless=true`.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv (fast drop-in for pip)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy source first so uv can see the full package tree
COPY . .

# Sync dependencies and install the package in one step
RUN uv sync --frozen

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Metadata
LABEL org.opencontainers.image.title="GetAJob"
LABEL org.opencontainers.image.description="Agentic job application platform"
LABEL org.opencontainers.image.version="0.1.0"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

# ── System dependencies ──────────────────────────────────────────────────────
# - curl:                      healthcheck endpoint
# - ca-certificates:           HTTPS trust
# - (Playwright install-deps adds the rest for Chromium)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── Python virtualenv from builder ───────────────────────────────────────────
COPY --from=builder /app/.venv .venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# ── Application source ───────────────────────────────────────────────────────
COPY . .

# ── Playwright (Chromium) ────────────────────────────────────────────────────
# Install system-level deps, then download Chromium itself.
# Browsers are stored under /opt/ms-playwright so the non-root user can read them.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN playwright install-deps chromium \
    && playwright install chromium \
    && chmod -R a+rX /opt/ms-playwright \
    && rm -rf /var/lib/apt/lists/* /tmp/*

# ── Non-root user ────────────────────────────────────────────────────────────
RUN useradd -m -u 1000 -U getajob \
    && mkdir -p /app/data && chown -R getajob:getajob /app/data

USER getajob

# ── Ports & healthcheck ──────────────────────────────────────────────────────
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -sf http://127.0.0.1:8080/api/health || exit 1

ENTRYPOINT ["getajob"]
CMD ["serve"]
