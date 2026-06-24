"""FastAPI application factory for the Approval Queue web UI.

Creates and configures the FastAPI ASGI app with Jinja2 templating, static
file serving, session-based single-user auth, CORS middleware, and security
headers.

The app is designed for **local single-user operation** — auth is a simple
cookie-based session check, not OAuth.  In production you can front this
with a reverse proxy (nginx / Caddy) for TLS and optional basic auth.
"""

from __future__ import annotations as _annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.cors import CORSMiddleware

from core.config import get_settings
from core.database import AsyncEngine, create_engine
from core.event_bus import InMemoryEventBus

__all__: list[str] = [
    "app",
]

logger = structlog.get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _PROJECT_ROOT / "approval_queue" / "templates"
_STATIC_DIR = _PROJECT_ROOT / "approval_queue" / "static"

# ── Application state ───────────────────────────────────────────────────────


class AppState:
    """Holds shared application state accessible via ``request.app.state``.

    Attributes:
        db_engine: SQLAlchemy async engine (lazy-initialised).
        event_bus: In-memory or Redis event bus reference.
        templates: Jinja2 template environment.
        session_token: Random token for the current server session.
    """

    def __init__(self) -> None:
        self.db_engine: AsyncEngine | None = None
        self.event_bus: Any = InMemoryEventBus()
        self.templates: Jinja2Templates | None = None
        self.session_token: str = uuid.uuid4().hex


# ── Lifecycle ───────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialise and tear down connections."""
    state = AppState()

    # Create the database engine (lazy — pool opened on first query).
    try:
        state.db_engine = create_engine()
        logger.info("Database engine created for approval queue")
    except Exception:
        logger.warning("Database engine creation failed — running in UI-only mode")

    # Initialise the event bus.
    await state.event_bus.start()

    # Set up Jinja2 templates.
    state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Attach to app state.
    app.state.db_engine = state.db_engine
    app.state.event_bus = state.event_bus
    app.state.templates = state.templates
    app.state.session_token = state.session_token

    logger.info(
        "Approval queue started",
        session_token_preview=state.session_token[:8],
    )

    yield

    # Shutdown: close DB connections and stop the event bus.
    if state.db_engine is not None:
        await state.db_engine.dispose()
        logger.debug("Database engine disposed")
    await state.event_bus.stop()
    logger.info("Approval queue stopped")


# ── App factory ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="GetAJob Approval Queue",
    description="Human-in-the-Loop gateway for reviewing and approving job applications.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,  # Disable Swagger in production; re-enable via env if needed
    redoc_url=None,
)

# CORS — restrict to localhost origins for safety.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://localhost:3000",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Static files.
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
else:
    logger.warning("Static directory not found — UI may lack styling", path=str(_STATIC_DIR))


# ── Security headers middleware ─────────────────────────────────────────────


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next: Any) -> Response:
    """Add hardening headers to every response."""
    response: Response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-XSS-Protection", "1; mode=block")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    # Only set in production to avoid breaking dev tools
    settings = get_settings()
    if settings.environment == "production":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


# ── Session auth middleware ─────────────────────────────────────────────────


@app.middleware("http")
async def session_auth_middleware(request: Request, call_next: Any) -> Response:
    """Single-user session auth via signed cookie.

    Login page and static assets are always accessible.  All other routes
    redirect to ``/login`` if the session cookie is missing or invalid.
    """
    public_paths = {"/login", "/static", "/api/health"}

    if any(request.url.path.startswith(p) for p in public_paths):
        return await call_next(request)

    session = request.cookies.get("getajob_session")
    token: str | None = getattr(request.app.state, "session_token", None)

    if session is not None and token is not None and session == token:
        return await call_next(request)

    # Redirect to login for HTML requests, 401 for API requests.
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})

    return RedirectResponse(url="/login")


# ── Login page ──────────────────────────────────────────────────────────────


_COOKIE_MAX_AGE = 86_400 * 7  # 7 days


@app.get("/login")
async def login_page(request: Request) -> Response:
    """Render the login page."""
    template: Jinja2Templates | None = getattr(request.app.state, "templates", None)
    if template is not None:
        return template.TemplateResponse(request, "login.html")
    return _fallback_login_html()


@app.post("/login")
async def login(request: Request) -> Response:
    """Accept password and set the session cookie on success.

    In development mode any non-empty password is accepted.
    In production the password is compared against
    ``GETAJOB_APPROVAL_PASSWORD`` env var, falling back to the database
    password.
    """
    settings = get_settings()
    form = await request.form()
    password: str = form.get("password", "")

    if not password:
        template: Jinja2Templates | None = getattr(request.app.state, "templates", None)
        if template is not None:
            return template.TemplateResponse(
                request, "login.html", {"error": "Password is required"}
            )
        return _fallback_login_html(error="Password is required")

    # Dev mode: any password works.
    if settings.environment == "development":
        token: str | None = getattr(request.app.state, "session_token", None)
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key="getajob_session",
            value=token or uuid.uuid4().hex,
            max_age=_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=False,  # localhost
        )
        return response

    # Production: check against dedicated approval password.
    # Priority: env var > config file > fail closed.
    expected = (
        os.environ.get("GETAJOB_APPROVAL_PASSWORD")
        or settings.security.approval_password
        or ""
    )

    if password == expected:
        token = getattr(request.app.state, "session_token", None)
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key="getajob_session",
            value=token or uuid.uuid4().hex,
            max_age=_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=True,
        )
        return response

    logger.warning("Failed login attempt")
    template = getattr(request.app.state, "templates", None)
    if template is not None:
        return template.TemplateResponse(
            request, "login.html", {"error": "Invalid password"}
        )
    return _fallback_login_html(error="Invalid password")


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Simple health-check endpoint (no auth required)."""
    return {"status": "ok", "service": "approval-queue"}


# ── Logout ──────────────────────────────────────────────────────────────────


@app.post("/logout")
async def logout() -> Response:
    """Clear the session cookie."""
    response = RedirectResponse(url="/login", status_code=302)
    response.set_cookie(
        key="getajob_session",
        value="",
        max_age=0,
        httponly=True,
        samesite="lax",
    )
    return response


# ── Router inclusion ────────────────────────────────────────────────────────

# Import and include AFTER the app is created to avoid circular imports.
from approval_queue.routes import router  # noqa: E402

app.include_router(router)


# ── Standalone runner ───────────────────────────────────────────────────────


def _fallback_login_html(error: str = "") -> Response:
    """Return an inline HTML login page when Jinja2 templates are unavailable."""
    from fastapi.responses import HTMLResponse

    error_html = f'<p style="color:#e74c3c;margin-bottom:1rem">{error}</p>' if error else ""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GetAJob — Login</title>
    <style>
        body {{ background:#1a1b2e; color:#e0e0e0; font-family:system-ui,sans-serif;
               display:flex; justify-content:center; align-items:center; height:100vh; margin:0; }}
        .card {{ background:#252740; padding:2.5rem; border-radius:12px; min-width:360px;
                border:1px solid #3d3f5c; }}
        h1 {{ margin:0 0 0.25rem; font-size:1.5rem; color:#e0e0e0; }}
        p {{ color:#8e8ea0; margin:0 0 1.5rem; }}
        input {{ width:100%; padding:0.75rem; margin:0.5rem 0; border-radius:8px;
                 border:1px solid #3d3f5c; background:#1a1b2e; color:#e0e0e0;
                 font-size:0.95rem; box-sizing:border-box; }}
        input:focus {{ outline:2px solid #6c63ff; border-color:transparent; }}
        button {{ background:#6c63ff; color:#fff; border:none; padding:0.75rem;
                 border-radius:8px; width:100%; font-weight:600; cursor:pointer;
                 font-size:0.95rem; transition:background 0.2s; }}
        button:hover {{ background:#5a52e0; }}
        .logo {{ font-size:1.75rem; font-weight:700; background:linear-gradient(135deg,#6c63ff,#a78bfa);
                -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">GetAJob</div>
        <p>Approval Queue &middot; {get_settings().environment}</p>
        {error_html}
        <form method="post" action="/login">
            <input type="password" name="password" placeholder="Enter password" autofocus required />
            <button type="submit">Sign In</button>
        </form>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html)


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    port = 8000

    logger.info(
        "Starting approval queue server",
        host="0.0.0.0",
        port=port,
        environment=settings.environment,
    )
    uvicorn.run(
        "approval_queue.main:app",
        host="0.0.0.0",
        port=port,
        reload=settings.environment == "development",
        log_level="debug" if settings.debug else "info",
        proxy_headers=True,
    )
