"""GetAJob CLI -- command-line interface package.

Re-exports the Typer ``app`` from :mod:`getajob.cli.main` so that the
``getajob.cli:app`` entry point in ``pyproject.toml`` continues to work.
"""

from __future__ import annotations as _annotations

from getajob.cli.main import app

__all__: list[str] = [
    "app",
]
