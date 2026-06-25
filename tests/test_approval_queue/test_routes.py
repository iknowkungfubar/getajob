"""Tests for Approval Queue Web UI routes.

Exercises the FastAPI app's page and API endpoints via TestClient, covering
authentication flow, health check, dashboard rendering, and data endpoints.

The app gracefully degrades to UI-only mode (mock data) when no database is
available, so these tests run without a real PostgreSQL connection.
"""

from __future__ import annotations as _annotations

import pytest
from starlette.testclient import TestClient

from core.config import GetAJobSettings, SecuritySettings

# ── Fixture ─────────────────────────────────────────────────────────────


@pytest.fixture
def client() -> TestClient:
    """Yield a TestClient bound to the approval queue app.

    The lifespan context manager runs on enter / exit, which initialises
    Jinja2 templates, the event bus, and attempts to create a database
    engine (gracefully degrading to UI-only mode when no DB is available).
    Each test gets its own client for clean isolation.
    """
    from approval_queue.main import app

    with TestClient(app) as c:
        yield c


# ── Health check ────────────────────────────────────────────────────────


class TestHealth:
    """The ``/api/health`` endpoint is public and returns basic status."""

    def test_health_returns_ok(self, client: TestClient) -> None:
        """GET /api/health returns 200 with ``{"status": "ok"}``."""
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "approval-queue"}


# ── Authentication ──────────────────────────────────────────────────────


class TestAuth:
    """Session-cookie authentication flow for the single-user web UI."""

    def test_dashboard_redirects_when_unauthenticated(self, client: TestClient) -> None:
        """GET / redirects to /login when no valid session cookie is present.

        The session middleware checks for a ``getajob_session`` cookie that
        matches the server's internal token.  Without one it issues a 307
        (temporary redirect) so that POST-data is preserved by the browser,
        though in practice only GET requests hit this path from the UI.
        """
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers.get("location") == "/login"

    def test_login_with_correct_password(self, client: TestClient) -> None:
        """POST /login with any non-empty password succeeds in dev mode.

        In ``development`` mode (the default) every non-empty password is
        accepted.  The response is a 302 redirect to the dashboard with a
        ``getajob_session`` cookie set.
        """
        response = client.post(
            "/login",
            data={"password": "any-password-works-in-dev"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers.get("location") == "/"
        assert "getajob_session" in response.cookies

    def test_login_with_wrong_password(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST /login with an incorrect password stays on the login page.

        In ``production`` mode the server validates against a configured
        password.  We temporarily override the ``get_settings`` singleton to
        simulate a production environment so the password check actually runs.
        """
        # Override get_settings in the approval_queue.main module namespace
        # so the login handler sees a production configuration.
        from approval_queue import main as approval_main

        prod_settings = GetAJobSettings(
            environment="production",
            security=SecuritySettings(approval_password="correct-password"),
        )
        monkeypatch.setattr(approval_main, "get_settings", lambda: prod_settings)

        response = client.post(
            "/login",
            data={"password": "wrong-password"},
            follow_redirects=False,
        )
        # A failed login returns the login page (200 HTML), not a redirect.
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

    def _login(self, client: TestClient) -> str:
        """Helper: log in in dev mode and return the session cookie value."""
        resp = client.post(
            "/login",
            data={"password": "dev-mode-password"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        cookie = resp.cookies.get("getajob_session")
        assert cookie is not None
        # Set the cookie on the client so subsequent requests include it
        # (avoids the per-request cookies= deprecation warning).
        client.cookies.set("getajob_session", cookie)
        return cookie

    def test_dashboard_loads_when_authenticated(self, client: TestClient) -> None:
        """After logging in, GET / returns 200 with the dashboard HTML.

        The dashboard template renders with stats and applications
        (mock data or real DB depending on environment).
        """
        self._login(client)

        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")

        # Sanity-check that the page looks like a dashboard.
        body = response.text
        assert "GetAJob" in body or "dashboard" in body.lower()

    def test_api_returns_401_without_auth(self, client: TestClient) -> None:
        """API endpoints return 401 (not 307) when no session cookie is set.

        The middleware distinguishes HTML routes (redirect) from API routes
        (JSON 401) so that programmatic callers get a parseable error.
        """
        response = client.get("/api/stats", follow_redirects=False)
        assert response.status_code == 401
        assert response.json() == {"detail": "Not authenticated"}


# ── API endpoints ───────────────────────────────────────────────────────


class TestStats:
    """The ``/api/stats`` endpoint returns aggregate application counts."""

    def test_api_stats_returns_data(self, client: TestClient) -> None:
        """GET /api/stats (authenticated) returns JSON with state counts.

        We assert on the response *shape* rather than exact values because
        the app may connect to a real database (returning live counts) or
        fall back to mock data — the contract is that a dict is returned
        with ``state_counts`` and ``total``.
        """
        # Log in and attach the session cookie to the client.
        login_resp = client.post(
            "/login",
            data={"password": "any-password-works-in-dev"},
            follow_redirects=False,
        )
        assert login_resp.status_code == 302
        client.cookies.set(
            "getajob_session",
            login_resp.cookies["getajob_session"],
        )

        response = client.get("/api/stats")
        assert response.status_code == 200
        data = response.json()

        # The contract is: state_counts dict + total int.
        # state_counts may be empty if the database exists but tables
        # haven't been created yet, or populated if using mock data.
        assert "state_counts" in data
        assert isinstance(data["state_counts"], dict)
        assert "total" in data
        assert isinstance(data["total"], int)
