"""Typer application definition for the GetAJob CLI.

Defines the top-level ``app``, callback, sub-app registration, and entry
point.  Commands are registered in separate modules and wired via imports
for side effects.
"""

from __future__ import annotations as _annotations

import typer

from getajob.cli.helpers import _version_callback, console

__all__: list[str] = [
    "app",
    "vector_app",
    "profile_app",
]

# ── Sub-apps -----------------------------------------------------------------

vector_app = typer.Typer(
    name="vector",
    help="Manage search vectors.",
)

profile_app = typer.Typer(
    name="profile",
    help="Manage user profile.",
)

# ── Main app -----------------------------------------------------------------

app = typer.Typer(
    name="getajob",
    help="GetAJob - automated job application platform",
    add_completion=False,
    pretty_exceptions_show_locals=False,
)


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


# ── Import command modules (side effects: @app.command() decorators) ---------

import getajob.cli.commands  # noqa: E402, F401  -- registers main commands
import getajob.cli.profile  # noqa: E402, F401   -- registers profile commands
import getajob.cli.vector  # noqa: E402, F401    -- registers vector commands


# ── Register sub-apps --------------------------------------------------------

app.add_typer(profile_app)
app.add_typer(vector_app)


# ── Entry point --------------------------------------------------------------

if __name__ == "__main__":
    app()
