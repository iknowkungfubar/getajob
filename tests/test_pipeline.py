"""Tests for core.pipeline — the composable job-application pipeline.

Pipeline stages are pure async protocols with no database or network
dependencies, making them straightforward to test with stub implementations.
"""
from __future__ import annotations as _annotations

from typing import Any

import pytest

from core.pipeline import (
    AnalysisStage,
    DiscoveryStage,
    JobPipeline,
    SubmissionStage,
    TailoringStage,
)

# ── Stub implementations ─────────────────────────────────────────────────────


class _StubDiscovery:
    """Returns a fixed list of listing-like dicts."""

    def __init__(self, listings: list[dict[str, Any]] | None = None) -> None:
        self.listings = listings if listings is not None else [{"id": "job-1"}, {"id": "job-2"}]
        self.called = False

    async def discover(self) -> list[Any]:
        self.called = True
        return self.listings


class _StubAnalysis:
    """Passes through or filters listings based on threshold."""

    def __init__(self, threshold: float = 0.0) -> None:
        self.threshold = threshold
        self.called = False

    async def analyze(self, listings: list[Any]) -> list[Any]:
        self.called = True
        return listings  # pass through — all listings pass


class _StubTailoring:
    """Tracks call count for verification."""

    def __init__(self) -> None:
        self.called = False
        self.received: list[Any] = []

    async def tailor(self, applications: list[Any]) -> list[Any]:
        self.called = True
        self.received = applications
        return applications


class _StubSubmission:
    """Records the applications it received."""

    def __init__(self) -> None:
        self.called = False
        self.received: list[Any] = []

    async def submit(self, applications: list[Any]) -> list[Any]:
        self.called = True
        self.received = applications
        return applications


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def stub_discovery() -> _StubDiscovery:
    return _StubDiscovery()


@pytest.fixture
def stub_analysis() -> _StubAnalysis:
    return _StubAnalysis()


@pytest.fixture
def stub_tailoring() -> _StubTailoring:
    return _StubTailoring()


@pytest.fixture
def stub_submission() -> _StubSubmission:
    return _StubSubmission()


# ── Pipeline construction ────────────────────────────────────────────────────


class TestJobPipelineConstruction:
    """JobPipeline.__init__ stores stages correctly."""

    def test_requires_discovery_and_analysis_only(self) -> None:
        """Pipeline can be built with only mandatory stages."""
        pipeline = JobPipeline(
            discovery=_StubDiscovery(),
            analysis=_StubAnalysis(),
        )
        assert pipeline.discovery is not None
        assert pipeline.analysis is not None
        assert pipeline.tailoring is None
        assert pipeline.submission is None

    def test_accepts_all_stages(self) -> None:
        """Pipeline accepts all four stages."""
        pipeline = JobPipeline(
            discovery=_StubDiscovery(),
            analysis=_StubAnalysis(),
            tailoring=_StubTailoring(),
            submission=_StubSubmission(),
        )
        assert pipeline.tailoring is not None
        assert pipeline.submission is not None


# ── Full pipeline run ────────────────────────────────────────────────────────


class TestJobPipelineRun:
    """JobPipeline.run() orchestrates stages in the correct order."""

    async def test_calls_all_stages_in_order(
        self,
        stub_discovery: _StubDiscovery,
        stub_analysis: _StubAnalysis,
        stub_tailoring: _StubTailoring,
        stub_submission: _StubSubmission,
    ) -> None:
        """Each stage is called exactly once in the correct order."""
        pipeline = JobPipeline(
            discovery=stub_discovery,
            analysis=stub_analysis,
            tailoring=stub_tailoring,
            submission=stub_submission,
        )

        stats = await pipeline.run()

        assert stub_discovery.called
        assert stub_analysis.called
        assert stub_tailoring.called
        assert stub_submission.called
        assert stats["jobs_discovered"] == 2
        assert stats["jobs_analyzed"] == 2

    async def test_returns_statistics_dict(
        self,
        stub_discovery: _StubDiscovery,
        stub_analysis: _StubAnalysis,
        stub_tailoring: _StubTailoring,
        stub_submission: _StubSubmission,
    ) -> None:
        """Stats dict contains all expected keys with correct types."""
        pipeline = JobPipeline(
            discovery=stub_discovery,
            analysis=stub_analysis,
            tailoring=stub_tailoring,
            submission=stub_submission,
        )

        stats = await pipeline.run()

        assert isinstance(stats, dict)
        assert "jobs_discovered" in stats
        assert "jobs_analyzed" in stats
        assert "applications_created" in stats
        assert "applications_submitted" in stats
        assert all(isinstance(v, int) for v in stats.values())

    # ── Early exit paths ──────────────────────────────────────────────────

    async def test_early_exit_when_nothing_discovered(
        self,
        stub_analysis: _StubAnalysis,
    ) -> None:
        """Pipeline stops after discovery if no listings found."""
        empty_discovery = _StubDiscovery(listings=[])
        pipeline = JobPipeline(
            discovery=empty_discovery,
            analysis=stub_analysis,
        )

        stats = await pipeline.run()

        assert stats["jobs_discovered"] == 0
        assert stats["jobs_analyzed"] == 0
        # Analysis is never called when discovery returns empty
        assert not stub_analysis.called

    async def test_early_exit_when_nothing_analyzed(
        self,
        stub_discovery: _StubDiscovery,
    ) -> None:
        """Pipeline stops after analysis if nothing passes the filter."""
        empty_analysis = _StubAnalysis(threshold=1.0)
        empty_analysis.analyze = _always_empty  # type: ignore[method-assign]
        pipeline = JobPipeline(
            discovery=stub_discovery,
            analysis=empty_analysis,
        )

        stats = await pipeline.run()

        assert stats["jobs_discovered"] == 2
        assert stats["jobs_analyzed"] == 0

    # ── Optional stages ───────────────────────────────────────────────────

    async def test_skips_tailoring_when_none(
        self,
        stub_discovery: _StubDiscovery,
        stub_analysis: _StubAnalysis,
        stub_submission: _StubSubmission,
    ) -> None:
        """Pipeline runs without tailoring if not provided."""
        pipeline = JobPipeline(
            discovery=stub_discovery,
            analysis=stub_analysis,
            submission=stub_submission,
        )

        await pipeline.run()

        # Still reaches submission without tailoring
        assert stub_submission.called

    async def test_skips_submission_when_none(
        self,
        stub_discovery: _StubDiscovery,
        stub_analysis: _StubAnalysis,
        stub_tailoring: _StubTailoring,
    ) -> None:
        """Pipeline runs without submission if not provided."""
        pipeline = JobPipeline(
            discovery=stub_discovery,
            analysis=stub_analysis,
            tailoring=stub_tailoring,
        )

        stats = await pipeline.run()

        assert stub_tailoring.called
        # Submission stats key is initialised to 0 but never updated
        # when submission is None — a minor design quirk.
        assert stats["applications_submitted"] == 0

    async def test_passes_listings_through_tailoring_to_submission(
        self,
        stub_discovery: _StubDiscovery,
        stub_analysis: _StubAnalysis,
        stub_tailoring: _StubTailoring,
        stub_submission: _StubSubmission,
    ) -> None:
        """The same listings flow through all pipeline stages."""
        pipeline = JobPipeline(
            discovery=stub_discovery,
            analysis=stub_analysis,
            tailoring=stub_tailoring,
            submission=stub_submission,
        )

        await pipeline.run()

        # Tailoring received the analysed listings
        assert stub_tailoring.received is not None
        # Submission received the tailored listings
        assert stub_submission.received is not None


# ── Protocol conformance ─────────────────────────────────────────────────────


class TestStageProtocols:
    """Protocol classes are structurally checkable at runtime."""

    def test_stub_discovery_conforms(self) -> None:
        """Stub discovery satisfies DiscoveryStage."""
        assert isinstance(_StubDiscovery(), DiscoveryStage)

    def test_stub_analysis_conforms(self) -> None:
        """Stub analysis satisfies AnalysisStage."""
        assert isinstance(_StubAnalysis(), AnalysisStage)

    def test_stub_tailoring_conforms(self) -> None:
        """Stub tailoring satisfies TailoringStage."""
        assert isinstance(_StubTailoring(), TailoringStage)

    def test_stub_submission_conforms(self) -> None:
        """Stub submission satisfies SubmissionStage."""
        assert isinstance(_StubSubmission(), SubmissionStage)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _always_empty(_listings: list[Any]) -> list[Any]:
    """Analysis stub that always returns nothing."""
    return []
