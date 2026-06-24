#!/usr/bin/env bash
# ── GetAJob — Platform Startup Script ──────────────────────────────────────
#
# Usage:  bash scripts/run.sh [--dev] [--skip-redis]
#
# This script:
#   1. Sources .env for environment variables
#   2. Starts Redis if it is not already running (unless --skip-redis)
#   3. Runs database migrations via Alembic (or create_all if no Alembic)
#   4. Starts the Approval Queue web UI (uvicorn)
#   5. Starts the Hermes orchestrator agent loop
#
# Options:
#   --dev         Enable debug logging, console log format, hot reload
#   --skip-redis  Skip Redis startup check (use InMemoryEventBus)
#   --help        Show this help message

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { printf "${CYAN}ℹ %s${NC}\n" "$*"; }
ok()    { printf "${GREEN}✓ %s${NC}\n" "$*"; }
warn()  { printf "${YELLOW}⚠ %s${NC}\n" "$*"; }
err()   { printf "${RED}✗ %s${NC}\n" "$*"; }

# ── Parse arguments ─────────────────────────────────────────────────────────
DEV_MODE=false
SKIP_REDIS=false

for arg in "$@"; do
    case "$arg" in
        --dev)      DEV_MODE=true ;;
        --skip-redis) SKIP_REDIS=true ;;
        --help|-h)
            sed -n '3,20p' "$0" | sed 's/^#//' | sed 's/^ \?//'
            exit 0
            ;;
    esac
done

# ── 1. Environment ──────────────────────────────────────────────────────────

if [ ! -f ".env" ]; then
    err "No .env file found. Run 'bash scripts/setup.sh' first."
    exit 1
fi

info "Sourcing .env…"
# shellcheck disable=SC1091
set -a; source .env; set +a
ok "Environment loaded."

# ── 2. Activate virtual environment ─────────────────────────────────────────

if [ ! -d ".venv" ]; then
    err "No virtual environment found at .venv. Run 'bash scripts/setup.sh' first."
    exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
ok "Virtual environment activated: .venv"

# ── 3. Redis startup ────────────────────────────────────────────────────────

if [ "$SKIP_REDIS" = false ]; then
    if command -v redis-server &>/dev/null; then
        if redis-cli ping &>/dev/null; then
            ok "Redis is already running."
        else
            info "Starting Redis…"
            redis-server --daemonize yes --port "${GETAJOB_REDIS__PORT:-6379}"
            sleep 1
            if redis-cli -p "${GETAJOB_REDIS__PORT:-6379}" ping &>/dev/null; then
                ok "Redis started on port ${GETAJOB_REDIS__PORT:-6379}."
            else
                warn "Redis may not have started — check with: redis-cli ping"
            fi
        fi
    else
        warn "redis-server not found — the platform will use InMemoryEventBus."
    fi
else
    info "Skipping Redis startup (--skip-redis) — InMemoryEventBus will be used."
fi

# ── 4. Database migrations ──────────────────────────────────────────────────

info "Running database migrations…"

# Prefer Alembic for versioned migrations; fall back to create_all for dev.
if [ -d "alembic" ] && [ -f "alembic.ini" ]; then
    info "Running Alembic migrations…"
    if alembic upgrade head 2>/dev/null; then
        ok "Alembic migrations applied."
    else
        warn "Alembic upgrade failed — falling back to create_all."
        python -c "
import asyncio
from core.database import create_engine, run_migrations

async def migrate():
    engine = create_engine()
    try:
        await run_migrations(engine)
        await engine.dispose()
        print('Schema created via create_all')
    except Exception as e:
        print(f'Migration skipped: {e}')
        await engine.dispose()

asyncio.run(migrate())
" 2>/dev/null || warn "Database migration failed — ensure PostgreSQL is running."
    fi
else
    info "No Alembic setup found — using create_all for schema initialisation."
    python -c "
import asyncio
from core.database import create_engine, run_migrations

async def migrate():
    engine = create_engine()
    try:
        await run_migrations(engine)
        await engine.dispose()
        print('Schema created via create_all')
    except Exception as e:
        print(f'Migration skipped: {e}')
        await engine.dispose()

asyncio.run(migrate())
" || warn "Database migration failed — PostgreSQL may not be available."
fi

# ── 5. Startup the Approval Queue (FastAPI) ──────────────────────────────────

APPROVAL_QUEUE_PORT="${GETAJOB_APPROVAL_PORT:-8000}"
LOG_LEVEL="info"
if [ "$DEV_MODE" = true ]; then
    LOG_LEVEL="debug"
    export GETAJOB_DEBUG=true
    export GETAJOB_LOG_FORMAT="console"
fi

info "Starting Approval Queue web UI on port ${APPROVAL_QUEUE_PORT}…"
if [ "$DEV_MODE" = true ]; then
    uvicorn approval_queue.main:app \
        --host 0.0.0.0 \
        --port "$APPROVAL_QUEUE_PORT" \
        --log-level "$LOG_LEVEL" \
        --reload \
        --reload-dir "$REPO_ROOT/approval_queue" \
        --reload-dir "$REPO_ROOT/core" &
else
    uvicorn approval_queue.main:app \
        --host 0.0.0.0 \
        --port "$APPROVAL_QUEUE_PORT" \
        --log-level "$LOG_LEVEL" &
fi
APPROVAL_PID=$!
ok "Approval Queue started (PID: $APPROVAL_PID, port: $APPROVAL_QUEUE_PORT)"

# ── 6. Launch Hermes orchestrator agent loop ────────────────────────────────

if [ "${GETAJOB_SKIP_ORCHESTRATOR:-false}" = "true" ]; then
    info "Orchestrator loop disabled via GETAJOB_SKIP_ORCHESTRATOR."
else
    info "Starting Hermes orchestrator agent loop…"
    if [ "$DEV_MODE" = true ]; then
        python -m core.hermes --dev &
    else
        python -m core.hermes &
    fi
    HERMES_PID=$!
    ok "Hermes orchestrator started (PID: $HERMES_PID)"
fi

# ── 7. Trap & graceful shutdown ──────────────────────────────────────────────

cleanup() {
    echo ""
    info "Shutting down GetAJob platform…"
    if [ -n "${HERMES_PID:-}" ]; then
        kill "$HERMES_PID" 2>/dev/null && info "Hermes orchestrator stopped." || true
    fi
    kill "$APPROVAL_PID" 2>/dev/null && info "Approval Queue stopped." || true

    # Optionally stop Redis if we started it.
    if [ "$SKIP_REDIS" = false ] && [ "${REDIS_STARTED_BY_US:-false}" = true ]; then
        redis-cli shutdown 2>/dev/null && info "Redis stopped." || true
    fi

    ok "GetAJob platform shut down."
    exit 0
}

trap cleanup SIGINT SIGTERM

# ── 8. Wait ─────────────────────────────────────────────────────────────────

echo ""
echo "┌──────────────────────────────────────────────────────────────────────┐"
echo "│                    GetAJob Platform — Running                         │"
echo "├──────────────────────────────────────────────────────────────────────┤"
echo "│  Approval Queue:  http://localhost:${APPROVAL_QUEUE_PORT}              │"
echo "│  Hermes Agent:    PID ${HERMES_PID:-N/A}                              │"
echo "│  Environment:     ${GETAJOB_ENVIRONMENT:-development}                          │"
echo "│                                                                       │"
echo "│  Press Ctrl+C to stop all services.                                   │"
echo "└──────────────────────────────────────────────────────────────────────┘"

# Wait for either process to exit (or Ctrl+C).
wait
