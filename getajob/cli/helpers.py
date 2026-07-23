"""Helper utilities for the GetAJob CLI.

Provides shared helper functions used by multiple CLI commands:
async execution, settings validation, result display, and default
configuration generation.
"""

from __future__ import annotations as _annotations

import asyncio
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import structlog
import typer
import yaml
from rich.console import Console
from rich.table import Table

from core.config import get_settings

logger = structlog.get_logger(__name__)

console = Console()
err_console = Console(stderr=True)

__all__: list[str] = [
    "_check_settings",
    "_print_result_table",
    "_run_async",
    "_version_callback",
    "_write_default_search_vectors",
    "console",
    "err_console",
]


def _version_callback(value: bool) -> None:
    """Print the current version and exit."""
    if value:
        from getajob import __version__

        console.print(f"GetAJob v{__version__}")
        raise typer.Exit()


def _run_async(coro: Coroutine[Any, Any, Any]) -> None:
    """Run an async coroutine in a new event loop.

    Catches :exc:`KeyboardInterrupt` for clean shutdown on Ctrl+C.
    """
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/]")
        sys.exit(0)
    except typer.Exit:
        raise
    except Exception as exc:
        err_console.print(f"\n[red]\u2717[/] {exc}")
        sys.exit(1)


def _check_settings() -> None:
    """Warn the user if critical settings are misconfigured.

    Checks for:
    - Mock LLM provider (no real AI calls).
    - Missing encryption key (PII stored in plaintext).
    """
    settings = get_settings()

    if settings.llm.provider == "mock":
        console.print("[yellow]\u26a0 Using mock LLM client -- no real AI calls will be made.[/]")

    if not settings.security.encryption_key:
        console.print(
            "[yellow]\u26a0 Encryption key not set -- "
            "PII will NOT be encrypted at rest.[/]\n"
            "  Set GETAJOB_SECURITY__ENCRYPTION_KEY in your .env file."
        )


def _print_result_table(result: dict[str, Any], title: str = "Results") -> None:
    """Print a pipeline result dict as a Rich table.

    Args:
        result: Dict with string keys and int/float values.
        title: Title displayed above the table.
    """
    table = Table(title=title, border_style="blue")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="bold")

    for key, value in result.items():
        if isinstance(value, (int, float)):
            style = "green" if value > 0 else "dim"
            label = key.replace("_", " ").title()
            table.add_row(label, str(value), style=style)

    console.print(table)


def _write_default_search_vectors(project_root: Path) -> None:
    """Replace search_vectors in config/settings.yaml with defaults.

    Writes three starter search vectors (Software Engineer, Data Scientist,
    Product Manager) to help the user get started immediately.  Existing
    top-level keys (rate limits, ATS profiles, etc.) are preserved.
    """
    yaml_path = project_root / "config" / "settings.yaml"

    default_vectors = [
        {
            "roles": ["software engineer"],
            "keywords": [
                "python",
                "typescript",
                "go",
                "rust",
                "aws",
                "kubernetes",
            ],
            "locations": ["remote"],
            "seniority": ["senior", "staff"],
            "sources": ["linkedin", "indeed", "greenhouse", "workday", "lever"],
            "max_applications_per_day": 50,
        },
        {
            "roles": ["data scientist", "ml engineer"],
            "keywords": [
                "python",
                "pytorch",
                "tensorflow",
                "sql",
                "machine learning",
            ],
            "locations": ["remote"],
            "seniority": ["senior", "staff"],
            "sources": ["linkedin", "indeed", "greenhouse"],
            "max_applications_per_day": 25,
        },
        {
            "roles": ["product manager"],
            "keywords": [
                "product strategy",
                "agile",
                "analytics",
            ],
            "locations": ["remote"],
            "seniority": ["senior", "staff"],
            "sources": ["linkedin", "greenhouse"],
            "max_applications_per_day": 25,
        },
    ]

    if yaml_path.exists():
        with yaml_path.open("r") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    else:
        data = {}

    data["search_vectors"] = default_vectors

    with yaml_path.open("w") as f:
        yaml.safe_dump(
            data,
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
