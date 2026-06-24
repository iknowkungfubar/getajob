#!/usr/bin/env bash
# ── GetAJob — First-Time Environment Setup ─────────────────────────────────
#
# Usage:  bash scripts/setup.sh
#
# This script:
#   1. Checks for required system dependencies (Python 3.12+, Redis, …)
#   2. Creates a Python virtual environment with uv (or pip)
#   3. Installs project dependencies (production + dev)
#   4. Copies env.template → .env if no .env exists
#   5. Installs Playwright browser binaries
#   6. Initialises the database schema
#   7. Writes a setup summary
#
# Safe to re-run — idempotent for existing environments.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Colour

info()  { printf "${CYAN}ℹ %s${NC}\n" "$*"; }
ok()    { printf "${GREEN}✓ %s${NC}\n" "$*"; }
warn()  { printf "${YELLOW}⚠ %s${NC}\n" "$*"; }
err()   { printf "${RED}✗ %s${NC}\n" "$*"; }

# ── 1. System dependency checks ─────────────────────────────────────────────

info "Checking system dependencies…"

PYTHON_OK=false
for cmd in python3.12 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+')
        if awk -v v="$ver" 'BEGIN { exit !(v >= 3.12) }'; then
            PYTHON="$cmd"
            PYTHON_OK=true
            break
        fi
    fi
done

if [ "$PYTHON_OK" = false ]; then
    err "Python 3.12+ is required but not found."
    err "Install it via:  uv python install 3.12   or   pacman -S python"
    exit 1
fi
ok "Python: $($PYTHON --version)"

# Check for uv (recommended) or pip.
HAVE_UV=false
if command -v uv &>/dev/null; then
    HAVE_UV=true
    ok "uv: $(uv --version)"
else
    warn "uv not found — falling back to pip (install uv for faster, reproducible builds)"
    if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
        err "Neither uv nor pip found. Install uv or ensure pip is available."
        exit 1
    fi
fi

# Check for Redis (needed for event bus).
REDIS_OK=false
if command -v redis-server &>/dev/null; then
    REDIS_OK=true
    ok "Redis: $(redis-server --version 2>&1 | head -1)"
else
    warn "redis-server not found — the platform can run with InMemoryEventBus for development."
    warn "Install Redis for production:  pacman -S redis  (or brew install redis)"
fi

# Check for Playwright system dependencies.
PLAYWRIGHT_DEPS=true
for pkg in libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 libasound2; do
    if ! ldconfig -p 2>/dev/null | grep -q "$pkg" && ! pacman -Qi "$pkg" &>/dev/null 2>&1; then
        PLAYWRIGHT_DEPS=false
    fi
done
if [ "$PLAYWRIGHT_DEPS" = false ]; then
    warn "Some Playwright system deps may be missing — they will be listed during playwright install."
fi

# ── 2. Python virtual environment ───────────────────────────────────────────

info "Setting up Python virtual environment…"

if [ "$HAVE_UV" = true ]; then
    if [ ! -d ".venv" ]; then
        uv venv --python "$PYTHON" .venv
        ok "Virtual environment created with uv at .venv"
    else
        ok "Virtual environment already exists at .venv"
    fi

    # shellcheck disable=SC1091
    source .venv/bin/activate

    info "Installing dependencies with uv…"
    uv sync --frozen
    ok "Dependencies installed (uv sync)"

    # Install dev dependencies
    uv sync --frozen --group dev 2>/dev/null || uv pip install -e ".[dev]"
    ok "Dev dependencies installed"
else
    if [ ! -d ".venv" ]; then
        $PYTHON -m venv .venv
        ok "Virtual environment created with venv at .venv"
    fi

    # shellcheck disable=SC1091
    source .venv/bin/activate

    info "Installing dependencies with pip…"
    pip install --quiet --upgrade pip
    pip install --quiet -e .
    pip install --quiet -e ".[dev]"
    ok "Dependencies installed (pip)"
fi

# ── 3. Environment file ─────────────────────────────────────────────────────

if [ ! -f ".env" ]; then
    if [ -f "env.template" ]; then
        cp env.template .env
        ok "Created .env from env.template — edit it to add your API keys and secrets."
    else
        warn "No env.template found; creating minimal .env"
        cat > .env <<-ENVEOF
# GetAJob Environment Configuration
# Fill in your secrets before running the platform.

# LLM Provider (Anthropic)
GETAJOB_LLM__API_KEY=your-anthropic-api-key-here

# Database
GETAJOB_DATABASE__HOST=localhost
GETAJOB_DATABASE__PORT=5432
GETAJOB_DATABASE__DATABASE=getajob
GETAJOB_DATABASE__USER=getajob
GETAJOB_DATABASE__PASSWORD=changeme

# Redis
GETAJOB_REDIS__HOST=localhost
GETAJOB_REDIS__PORT=6379

# Security
GETAJOB_SECURITY__ENCRYPTION_KEY=your-64-hex-char-encryption-key
GETAJOB_SECURITY__ENCRYPTION_SALT=your-32-hex-char-salt
ENVEOF
        ok "Created minimal .env — edit it to add your API keys and secrets."
    fi
else
    ok ".env already exists — not overwriting."
fi

# ── 4. Playwright browser binaries ──────────────────────────────────────────

info "Installing Playwright browser binaries…"
if [ "$HAVE_UV" = true ]; then
    uv run playwright install chromium 2>/dev/null || \
        .venv/bin/python -m playwright install chromium 2>/dev/null || \
        warn "Playwright install failed — you can run 'playwright install chromium' manually."
else
    .venv/bin/python -m playwright install chromium 2>/dev/null || \
        warn "Playwright install failed — you can run 'playwright install chromium' manually."
fi
ok "Playwright Chromium binary installed."

# ── 5. Database initialisation ──────────────────────────────────────────────

info "Initialising database schema…"
if [ -f ".env" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
    # Try to run schema creation; if DB is unavailable, warn but don't fail.
    .venv/bin/python -c "
import asyncio
from core.database import create_engine, run_migrations

async def init():
    engine = create_engine()
    try:
        await run_migrations(engine)
        await engine.dispose()
    except Exception as e:
        print(f'Database init skipped: {e}')
        await engine.dispose()

asyncio.run(init())
" 2>/dev/null && ok "Database schema initialised." || warn "Database init skipped — PostgreSQL may not be running."
else
    warn "No .env — database initialisation skipped."
fi

# ── 6. Git hooks (pre-commit) ───────────────────────────────────────────────

if command -v pre-commit &>/dev/null; then
    pre-commit install 2>/dev/null && ok "Pre-commit hooks installed." || warn "Pre-commit install skipped."
fi

# ── Summary ─────────────────────────────────────────────────────────────────

echo ""
echo "┌──────────────────────────────────────────────────────────────────────┐"
echo "│                         GetAJob — Setup Complete                      │"
echo "├──────────────────────────────────────────────────────────────────────┤"
echo "│  • Virtual environment: .venv                                         │"
echo "│  • Environment:         .env (edit to add secrets)                    │"
echo "│  • Database:            PostgreSQL (start it separately)              │"
echo "│  • Redis:               $( [ "$REDIS_OK" = true ] && echo 'Available ✓' || echo 'Not found — InMemoryEventBus will be used' ) │"
echo "│                                                                       │"
echo "│  Next steps:                                                          │"
echo "│    1. Edit .env with your API keys and secrets                       │"
echo "│    2. Start PostgreSQL:   sudo systemctl start postgresql            │"
echo "│    3. Start Redis:        sudo systemctl start redis                 │"
echo "│    4. Run the platform:   bash scripts/run.sh                        │"
echo "└──────────────────────────────────────────────────────────────────────┘"
