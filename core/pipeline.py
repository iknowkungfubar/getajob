"""Pipeline protocols and orchestration for the getajob application lifecycle.

Extracted from OrchestratorAgent to create testable seams between pipeline stages.
Each stage implements a Stage protocol — inject different implementations for
testing or to add new capabilities.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from core.models import JobListing

logger = logging.getLogger("getajob.pipeline")


@runtime_checkable
class DiscoveryStage(Protocol):
    """Discovers job listings from one or more sources."""

    async def discover(self) -> list[JobListing]:
        """Return newly discovered job listings."""
        ...


@runtime_checkable
class AnalysisStage(Protocol):
    """Analyzes job listings for fit against a user context."""

    async def analyze(self, listings: list[JobListing]) -> list[JobListing]:
        """Filter/score listings. Return those that pass the threshold."""
        ...


@runtime_checkable
class TailoringStage(Protocol):
    """Tailors resumes and cover letters for application-ready jobs."""

    async def tailor(self, listings: list[JobListing]) -> list[JobListing]:
        """Generate tailored materials for each listing."""
        ...


@runtime_checkable
class SubmissionStage(Protocol):
    """Subjects applications through browser automation or manual export."""

    async def submit(self, listings: list[JobListing]) -> list[JobListing]:
        """Submit applications and return updated records."""
        ...


class JobPipeline:
    """Composable pipeline for the job-application lifecycle.

    Stages:
        discovery -> analysis -> tailoring -> submission -> outreach

    Each stage is swappable. Add a new job source by adding a
    DiscoveryStage implementation — no changes to the pipeline.
    """

    def __init__(
        self,
        discovery: DiscoveryStage,
        analysis: AnalysisStage,
        tailoring: TailoringStage | None = None,
        submission: SubmissionStage | None = None,
    ):
        self.discovery = discovery
        self.analysis = analysis
        self.tailoring = tailoring
        self.submission = submission

    async def run(self) -> dict[str, int]:
        """Run one full pipeline cycle.

        Returns statistics for the cycle.
        """
        stats: dict[str, int] = {
            "vectors_processed": 0,
            "jobs_discovered": 0,
            "jobs_analyzed": 0,
            "applications_created": 0,
            "applications_advanced": 0,
            "applications_submitted": 0,
        }

        # Stage 1: Discovery
        listings = await self.discovery.discover()
        stats["jobs_discovered"] = len(listings)
        logger.info("Discovered %d job listings", len(listings))

        if not listings:
            return stats

        # Stage 2: Analysis
        filtered = await self.analysis.analyze(listings)
        stats["jobs_analyzed"] = len(filtered)
        logger.info("Analyzed %d listings, %d passed", len(listings), len(filtered))

        if not filtered:
            return stats

        # Stage 3: Tailoring (if available)
        if self.tailoring:
            tailored = await self.tailoring.tailor(filtered)
            logger.info("Tailored %d applications", len(tailored))

        # Stage 4: Submission (if available)
        if self.submission:
            submitted = await self.submission.submit(filtered)
            stats["applications_submitted"] = len(submitted)
            logger.info("Submitted %d applications", len(submitted))

        return stats
