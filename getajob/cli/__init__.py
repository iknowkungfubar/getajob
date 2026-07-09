"""GetAJob CLI - command-line interface for the job application platform.

Usage
-----
    # Run the full pipeline (discover -> tailor -> stage)
    getajob run

    # Discover jobs only
    getajob discover

    # Tailor a specific job
    getajob tailor <job-id>

    # Start the approval queue web UI
    getajob serve

    # First-time setup (create tables, etc.)
    getajob setup
"""
from __future__ import annotations as _annotations

# Import sub-modules so their decorator-registered commands are picked up.
# Each sub-module imports ``app`` / ``profile_app`` / ``vector_app`` from
# ``._app`` and registers commands on them at import time.
from getajob.cli import (
    commands,  # noqa: F401
    helpers,  # noqa: F401
    profile,  # noqa: F401
    vector,  # noqa: F401
)

# Import the shared app instances first, before any sub-modules that
# depend on them.  Python resolves the partial module correctly because
# ``app``, ``console``, etc. are defined at module level in ``_app.py``.
from getajob.cli._app import app, console, err_console, profile_app, vector_app  # noqa: F401

# ── Register sub-apps ───────────────────────────────────────────────────────
# Must happen after sub-module imports so their commands are defined.
app.add_typer(profile_app)
app.add_typer(vector_app)

# ── Entry point: ``python -m getajob.cli`` ──────────────────────────────────

if __name__ == "__main__":
    app()
