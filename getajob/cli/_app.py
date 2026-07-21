"""Shared application instances for the getajob CLI package.

This module exists solely to break circular imports between ``__init__.py``
and the sub-modules (``commands.py``, ``profile.py``, ``vector.py``).
Only module-level singleton instances live here -- no logic.
"""

from __future__ import annotations as _annotations

import typer
from rich.console import Console

# ── Main Typer app ──────────────────────────────────────────────────────────

app = typer.Typer(
    name="getajob",
    help="GetAJob - automated job application platform",
    add_completion=False,
    pretty_exceptions_show_locals=False,
)

# ── Sub-apps ────────────────────────────────────────────────────────────────

vector_app = typer.Typer(
    name="vector",
    help="Manage search vectors.",
)

profile_app = typer.Typer(
    name="profile",
    help="Manage user profile.",
)

# ── Shared console instances ────────────────────────────────────────────────

console = Console()
err_console = Console(stderr=True)

__all__: list[str] = [
    "app",
    "console",
    "err_console",
    "profile_app",
    "vector_app",
]
