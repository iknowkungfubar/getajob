"""GetAJob CLI — command-line interface for the job application platform.

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

import asyncio
import sys
from pathlib import Path
from typing import Annotated, Optional

import structlog
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from core.config import get_settings
from core.database import create_engine, run_migrations
from core.event_bus import InMemoryEventBus
from core.llm_client import get_llm_client

from agents.orchestrator_agent import OrchestratorAgent

__all__: list[str] = [
    "app",
]

# ── Logger ───────────────────────────────────────────────────────────────────

logger = structlog.get_logger(__name__)

# ── Typer app ────────────────────────────────────────────────────────────────

app = typer.Typer(
    name="getajob",
    help="GetAJob — automated job application platform",
    add_completion=False,
    pretty_exceptions_show_locals=False,
)

console = Console()
err_console = Console(stderr=True)


# ── Utility ──────────────────────────────────────────────────────────────────


def _version_callback(value: bool) -> None:
    if value:
        from getajob import __version__

        console.print(f"GetAJob v{__version__}")
        raise typer.Exit()


# ── Commands ─────────────────────────────────────────────────────────────────


@app.callback()
def _main(
    version: bool = typer.Option(  # noqa: FBT001
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


@app.command()
def discover(
    ctx: typer.Context,
    continuous: bool = typer.Option(  # noqa: FBT001
        False,
        "--continuous",
        "-c",
        help="Run discovery continuously on an interval.",
    ),
    interval: float = typer.Option(
        15.0,
        "--interval",
        "-i",
        help="Minutes between discovery passes in continuous mode.",
        min=1.0,
        max=120.0,
    ),
) -> None:
    """Discover job listings from all configured search vectors.

    Executes the orchestrator's discovery phase: loads search vectors from
    config, queries each job source for new listings, runs context analysis
    against the active profile, and creates Application records in DISCOVERED
    state.
    """
    console.print(Panel.fit("[bold blue]🔍  Job Discovery[/]", border_style="blue"))
    _check_settings()

    async def _run() -> None:
        engine = create_engine()
        event_bus = InMemoryEventBus()
        llm_client = get_llm_client()

        orchestrator = OrchestratorAgent(
            engine=engine,
            llm_client=llm_client,
            event_bus=event_bus,
        )

        await orchestrator.start()
        try:
            if continuous:
                console.print(
                    "[blue]⟳[/] Continuous discovery mode -- "
                    "press Ctrl+C to stop gracefully.\n"
                )
                interval_seconds = interval * 60.0
                while True:
                    result = await orchestrator.run_once()
                    _print_result_table(result, title="Discovery Pass Complete")
                    console.print(
                        f"[dim]Sleeping {interval:.0f} min until next pass...[/]\n"
                    )
                    await asyncio.sleep(interval_seconds)
            else:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                    transient=True,
                ) as progress:
                    progress.add_task(description="Discovering jobs...", total=None)
                    result = await orchestrator.run_once()

                _print_result_table(result, title="Discovery Results")
        finally:
            await orchestrator.stop()

    _run_async(_run)


@app.command()
def tailor(
    ctx: typer.Context,
    job_id: str = typer.Argument(
        ...,
        help="UUID of the job listing to tailor.",
    ),
) -> None:
    """Generate tailored resume and cover letter for a specific job.

    Runs the full analysis + tailoring pipeline for a single job listing
    that has already been discovered.  Loads the job from the database,
    runs ContextAgent to extract requirements, then TailoringAgent to
    produce tailored materials.
    """
    console.print(Panel.fit("[bold green]📝  Job Tailoring[/]", border_style="green"))
    _check_settings()

    async def _run() -> None:
        from core.models import JobListing
        from sqlalchemy import select
        from core.database import get_session

        engine = create_engine()
        event_bus = InMemoryEventBus()
        llm_client = get_llm_client()

        from agents.context_agent import ContextAgent
        from agents.tailoring_agent import TailoringAgent

        # Load the listing.
        async with get_session(engine) as session:
            stmt = select(JobListing).where(JobListing.id == job_id)
            listing = (await session.execute(stmt)).scalar_one_or_none()

        if listing is None:
            err_console.print(f"[red]✗[/] Job listing '{job_id}' not found.")
            raise typer.Exit(code=1)

        rich_format = Panel(
            f"[bold]{listing.title}[/]\n"
            f"  Company: {listing.company}\n"
            f"  Source:  {listing.source}\n"
            f"  URL:     {listing.url or 'N/A'}",
            border_style="green",
            title="Job Details",
        )
        console.print(rich_format)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            # Context analysis.
            progress.add_task(description="Analysing job requirements...", total=None)
            context_agent = ContextAgent(
                engine=engine,
                llm_client=llm_client,
                event_bus=event_bus,
            )
            await context_agent.start()
            desc_text = (
                listing.description_json.get("raw", "")
                if listing.description_json
                else ""
            )
            analysis = await context_agent.analyze(
                job_id=str(listing.id),
                job_description=desc_text,
            )
            await context_agent.stop()

            console.print(
                f"\n  Match score: [bold]{analysis.match_score:.1%}[/]"
            )
            if analysis.matching_skills:
                console.print(
                    f"  Matching skills: {', '.join(analysis.matching_skills[:10])}"
                )
            if analysis.missing_skills:
                console.print(
                    f"  [yellow]Missing skills: {', '.join(analysis.missing_skills[:5])}[/]"
                )
            if analysis.warnings:
                for w in analysis.warnings:
                    console.print(f"  [yellow]⚠ {w}[/]")
            console.print()

            # Tailoring.
            progress.add_task(
                description="Generating resume & cover letter...", total=None
            )
            tailoring_agent = TailoringAgent(
                engine=engine,
                llm_client=llm_client,
                event_bus=event_bus,
            )
            await tailoring_agent.start()
            result = await tailoring_agent.tailor(
                job_listing_id=str(listing.id),
                profile_id=analysis.profile_id,
                job_title=listing.title,
                company=listing.company,
                job_description=desc_text,
                generate_cover_letter=True,
            )
            await tailoring_agent.stop()

        # Display results.
        response_panel = Panel(
            f"[bold]Resume:[/] {len(result.resume_text)} chars\n"
            f"[bold]Cover Letter:[/] {'✓ Generated' if result.cover_letter else '✗ Skipped'}\n"
            f"[bold]Matched Skills:[/] {', '.join(result.matched_skills[:10]) or 'None'}\n"
            f"[bold]Warnings:[/] {'; '.join(result.warnings) if result.warnings else 'None'}\n",
            border_style="green",
            title="Tailoring Complete",
        )
        console.print(response_panel)

    _run_async(_run)


@app.command()
def run(
    ctx: typer.Context,
    continuous: bool = typer.Option(  # noqa: FBT001
        False,
        "--continuous",
        "-c",
        help="Run continuously on an interval.",
    ),
    interval: float = typer.Option(
        15.0,
        "--interval",
        "-i",
        help="Minutes between pipeline passes in continuous mode.",
        min=1.0,
        max=120.0,
    ),
) -> None:
    """Run the full pipeline: discover -> analyse -> stage.

    Executes job discovery, context analysis, and Application record creation
    for all unprocessed listings, then stages them for human review.
    """
    console.print(Panel.fit("[bold cyan]🚀  GetAJob Pipeline[/]", border_style="cyan"))
    _check_settings()

    async def _run() -> None:
        engine = create_engine()
        event_bus = InMemoryEventBus()
        llm_client = get_llm_client()

        orchestrator = OrchestratorAgent(
            engine=engine,
            llm_client=llm_client,
            event_bus=event_bus,
        )

        await orchestrator.start()
        try:
            if continuous:
                console.print(
                    "[blue]⟳[/] Continuous mode -- "
                    "press Ctrl+C to stop gracefully.\n"
                )
                interval_seconds = interval * 60.0
                while True:
                    result = await orchestrator.run_once()
                    _print_result_table(result, title="Pipeline Pass Complete")
                    console.print(
                        f"[dim]Sleeping {interval:.0f} min until next pass...[/]\n"
                    )
                    await asyncio.sleep(interval_seconds)
            else:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                    transient=True,
                ) as progress:
                    progress.add_task(description="Running pipeline...", total=None)
                    result = await orchestrator.run_once()

                _print_result_table(result, title="Pipeline Results")
        finally:
            await orchestrator.stop()

    _run_async(_run)


@app.command()
def serve(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Bind address for the web server.",
    ),
    port: int = typer.Option(
        8080,
        "--port",
        "-p",
        help="Port for the web server.",
        min=1024,
        max=65535,
    ),
) -> None:
    """Start the approval queue web UI.

    Launches a Uvicorn server serving the FastAPI approval queue app.
    The web UI provides a dashboard for reviewing applications, approving
    or rejecting submissions, and monitoring application state.
    """
    _check_settings()

    console.print(
        Panel.fit("[bold magenta]🌐  Approval Queue Web UI[/]", border_style="magenta")
    )
    console.print(f"\n  Starting server at [bold]http://{host}:{port}[/]\n")

    settings = get_settings()
    import uvicorn  # noqa: PLC0415

    uvicorn.run(
        "approval_queue.main:app",
        host=host,
        port=port,
        reload=settings.environment == "development",
        log_level="debug" if settings.debug else "info",
        proxy_headers=True,
    )


@app.command()
def setup(
    drop_first: bool = typer.Option(  # noqa: FBT001
        False,
        "--drop-first",
        help="Drop existing tables before creating (destructive!).",
    ),
) -> None:
    """First-time setup -- create database tables and initialise the schema.

    Uses :func:`~core.database.run_migrations` to create all ORM tables.
    In production, use Alembic migrations instead of this command.

    After tables are created, ensures required data directories exist and
    prints a summary of what was set up.
    """
    console.print(
        Panel.fit("[bold yellow]⚙️  First-Time Setup[/]", border_style="yellow")
    )

    async def _run() -> None:
        engine = create_engine()
        try:
            # ── Database ──────────────────────────────────────────────────────
            console.print("  Creating database tables...")
            await run_migrations(engine, drop_first=drop_first)
            console.print("  [green]✓[/] Database schema ready.")

            # ── Data directories ──────────────────────────────────────────────
            settings = get_settings()
            data_dir = settings.data_dir
            data_dir.mkdir(parents=True, exist_ok=True)
            console.print(f"  [green]✓[/] Data directory: {data_dir}")

            # Additional subdirectories.
            for sub in ("profiles", "resumes", "cover_letters", "screenshots"):
                (data_dir / sub).mkdir(parents=True, exist_ok=True)
            console.print("  [green]✓[/] Data subdirectories created.")

            # Check for .env file.
            env_path = Path.cwd() / ".env"
            if env_path.exists():
                console.print(
                    f"  [green]✓[/] Environment file found: {env_path}"
                )
            else:
                env_template = Path.cwd() / "env.template"
                if env_template.exists():
                    console.print(
                        "  [yellow]⚠[/] No .env file found. "
                        f"Copy env.template to .env and configure it:\n"
                        f"       cp env.template .env"
                    )
                else:
                    console.print(
                        "  [yellow]⚠[/] No .env file found. "
                        "Create one with your configuration."
                    )

            # ── Summary ───────────────────────────────────────────────────────
            console.print()
            console.print(
                Panel(
                    "[green]✓[/] Setup complete!\n\n"
                    f"  [bold]Next steps:[/]\n"
                    f"  1. Configure [cyan].env[/] with your API keys\n"
                    f"  2. Run [cyan]getajob run[/] to start discovering jobs\n"
                    f"  3. Run [cyan]getajob serve[/] to open the approval queue\n",
                    border_style="green",
                    title="Setup Complete",
                )
            )

        except Exception as exc:
            err_console.print(f"[red]✗[/] Setup failed: {exc}")
            raise typer.Exit(code=1) from exc
        finally:
            await engine.dispose()

    _run_async(_run)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _run_async(coro: object) -> None:
    """Run an async coroutine in a new event loop.

    Catches :exc:`KeyboardInterrupt` for clean shutdown on Ctrl+C.
    """
    try:
        asyncio.run(coro)  # type: ignore[arg-type]
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/]")
        sys.exit(0)
    except typer.Exit:
        raise
    except Exception as exc:
        err_console.print(f"\n[red]✗[/] {exc}")
        sys.exit(1)


def _check_settings() -> None:
    """Warn the user if critical settings are misconfigured.

    Checks for:
    - Mock LLM provider (no real AI calls).
    - Missing encryption key (PII stored in plaintext).
    """
    settings = get_settings()

    if settings.llm.provider == "mock":
        console.print(
            "[yellow]⚠ Using mock LLM client -- "
            "no real AI calls will be made.[/]"
        )

    if not settings.security.encryption_key:
        console.print(
            "[yellow]⚠ Encryption key not set -- "
            "PII will NOT be encrypted at rest.[/]\n"
            "  Set GETAJOB_SECURITY__ENCRYPTION_KEY in your .env file."
        )


def _print_result_table(result: dict, title: str = "Results") -> None:
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


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
