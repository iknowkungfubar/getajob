"""GetAJob — Agentic Job Application Platform.

Top-level package that re-exports the CLI entry point and provides
convenience access to the orchestrator and CLI runner.
"""

from __future__ import annotations as _annotations

from typing import Any

__version__ = "0.4.0"
__all__: list[str] = [
    "__version__",
    "run_pipeline",
]


async def run_pipeline(
    *,
    discover: bool = True,
    tailor: bool = True,
    continuous: bool = False,
    interval_minutes: float = 15.0,
) -> dict[str, Any]:
    """Programmatic entry point to run the GetAJob pipeline.

    Creates an :class:`~agents.orchestrator_agent.OrchestratorAgent`,
    starts it, runs a single cycle (``run_once``), and cleans up.

    Args:
        discover: Whether to run job discovery.
        tailor: Whether to run tailoring on discovered jobs.
        continuous: Run in continuous loop mode.
        interval_minutes: Sleep interval between passes in continuous mode.

    Returns:
        A dict with pipeline execution results.
    """
    from agents.orchestrator_agent import OrchestratorAgent
    from core.config import get_settings
    from core.database import create_engine
    from core.event_bus import InMemoryEventBus
    from core.llm_client import get_llm_client

    _ = discover  # orchestrator.run_once always includes discovery
    _ = tailor  # orchestrator.run_once always includes context analysis

    get_settings()
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
            import asyncio

            interval_seconds = interval_minutes * 60.0
            while True:
                result = await orchestrator.run_once()
                await asyncio.sleep(interval_seconds)
            return result  # unreachable
        else:
            return await orchestrator.run_once()
    finally:
        await orchestrator.stop()
