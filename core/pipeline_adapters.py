"""Pipeline adapters — wraps existing child agents into JobPipeline stages.

These adapters let the existing agents work with JobPipeline without
modifying the original classes. Each adapter implements one stage protocol
by delegating to the existing agent.
"""
from __future__ import annotations

import logging
from typing import Any, cast

from core.models import JobListing
from core.pipeline import (
    JobPipeline,
)

logger = logging.getLogger("getajob.pipeline.adapters")


class IngestionDiscoveryAdapter:
    """DiscoveryStage wrapping IngestionAgent's discover methods."""

    def __init__(self, ingestion_agent: Any) -> None:
        self._agent = ingestion_agent

    async def discover(self) -> list[JobListing]:
        """Delegate to ingestion agent's discover_from_source."""
        return cast("list[JobListing]", await self._agent.discover_from_source(resolve=True))


class ContextAnalysisAdapter:
    """AnalysisStage wrapping ContextAgent's analysis methods."""

    def __init__(self, context_agent: Any) -> None:
        self._agent = context_agent

    async def analyze(self, listings: list[JobListing]) -> list[JobListing]:
        """Analyze and filter listings using context agent."""
        return cast("list[JobListing]", await self._agent.analyze_listings(listings))


class TailoringStageAdapter:
    """TailoringStage wrapping TailoringAgent."""

    def __init__(self, tailoring_agent: Any) -> None:
        self._agent = tailoring_agent

    async def tailor(self, listings: list[JobListing]) -> list[JobListing]:
        """Tailor resumes for applications."""
        return cast("list[JobListing]", await self._agent.tailor_applications(listings))


def build_pipeline(
    ingestion_agent: Any = None,
    context_agent: Any = None,
    tailoring_agent: Any = None,
) -> JobPipeline:
    """Build a JobPipeline from existing child agents.

    Creates adapter instances for each agent that's available.
    Pass None for agents you don't have — the pipeline skips those stages.
    """
    discovery = IngestionDiscoveryAdapter(ingestion_agent) if ingestion_agent else None
    analysis = ContextAnalysisAdapter(context_agent) if context_agent else None
    tailoring = TailoringStageAdapter(tailoring_agent) if tailoring_agent else None

    if not discovery or not analysis:
        logger.warning("Pipeline built without discovery or analysis stages")

    # Use dummy no-op stages if not available
    class _NoopDiscovery:
        async def discover(self) -> list[JobListing]:
            return []

    class _NoopAnalysis:
        async def analyze(self, _listings: list[JobListing]) -> list[JobListing]:
            return []

    pipeline = JobPipeline(
        discovery=discovery or _NoopDiscovery(),
        analysis=analysis or _NoopAnalysis(),
        tailoring=tailoring,
    )
    return pipeline
