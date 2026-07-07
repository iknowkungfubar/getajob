"""GetAJob CLI - command-line interface for the job application platform.

Usage
-----
    # Run the full pipeline (discover -> tailor -> stage)
    python -m getajob.cli run

    # Discover jobs only
    python -m getajob.cli discover

    # Tailor a specific job
    python -m getajob.cli tailor <job-id>

    # Start the approval queue web UI
    python -m getajob.cli serve

    # First-time setup (create tables, etc.)
    python -m getajob.cli setup
"""

from __future__ import annotations as _annotations

import typer

from getajob.cli._helpers import console

__all__: list[str] = [
    "app",
    "commands",
    "profile_app",
    "vector_app",
]

# ── Typer app (main) ──────────────────────────────────────────────────────────

app = typer.Typer(
    name="getajob",
    help="GetAJob - automated job application platform",
    add_completion=False,
    pretty_exceptions_show_locals=False,
)

# ── Sub-apps ──────────────────────────────────────────────────────────────────

profile_app = typer.Typer(
    name="profile",
    help="Manage user profile.",
)

vector_app = typer.Typer(
    name="vector",
    help="Manage search vectors.",
)


# ── Utility ───────────────────────────────────────────────────────────────────


def _version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        from getajob import __version__ as ver

        console.print(f"GetAJob v{ver}")
        raise typer.Exit()


# ── Main callback ─────────────────────────────────────────────────────────────


@app.callback()
def _main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """GetAJob -- automated, agentic job application platform for 2026."""
    pass


# ── Register commands (import triggers @app.command() decorators) ─────────────

from getajob.cli import commands  # noqa: E402

app.add_typer(profile_app)
app.add_typer(vector_app)

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
