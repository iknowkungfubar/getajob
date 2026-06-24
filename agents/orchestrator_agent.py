"""Orchestrator Agent — Master Loop for the GetAJob Application Pipeline.

The :class:`OrchestratorAgent` is the central coordinator that drives the
job-application lifecycle.  Each ``run_once()`` cycle:

1. Loads search vectors from ``config/settings.yaml``.
2. Spawns the :class:`~agents.ingestion_agent.IngestionAgent` to discover
   new job listings from configured sources.
3. Fetches unprocessed listings from the database (listings that have not
   yet been turned into an :class:`~core.models.Application` for the current
   user profile).
4. Runs the :class:`~agents.context_agent.ContextAgent` on each listing to
   extract structured requirements and compute a profile-match score.
5. Creates :class:`~core.models.Application` records in ``DISCOVERED`` state.
6. Emits ``JOB_DISCOVERED`` events so downstream modules (tailoring, browser)
   can pick up new work.

Usage::

    agent = OrchestratorAgent(engine, event_bus=bus)
    await agent.start()
    summary = await agent.run_once()
    await agent.stop()
"""

from __future__ import annotations as _annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from agents.base import BaseAgent
from agents.context_agent import ContextAgent, ContextAnalysis  # used at runtime
from agents.ingestion_agent import IngestionAgent
from core.config import load_config
from core.database import create_engine, get_session
from core.event_bus import EventPriority, EventType
from core.exceptions import GetAJobError
from core.llm_client import LLMClient, get_llm_client
from core.models import Application, JobListing, UserProfile
from core.schemas import SearchVectorConfig
from core.state_machine import ApplicationState

__all__: list[str] = [
    "OrchestratorAgent",
]

logger = structlog.get_logger(__name__)

# ── Orchestrator Agent ─────────────────────────────────────────────────────────


class OrchestratorAgent(BaseAgent):
    """Master orchestrator for the job-application pipeline.

    Drives the full lifecycle: discovers jobs -> analyses fit -> creates
    application records -> emits events for downstream processing.

    The orchestrator owns two child agents (:class:`IngestionAgent` and
    :class:`ContextAgent`) that it spawns, manages, and tears down.  Each
    cycle begins by loading search vectors, then proceeds through discovery,
    analysis, and record creation.
    """

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        *,
        llm_client: LLMClient | None = None,
        event_bus: Any | None = None,
        min_match_score: float = 0.0,
        batch_size: int = 100,
    ) -> None:
        """Initialise the orchestrator.

        Args:
            engine: An optional async SQLAlchemy engine.  A new one is created
                from settings when ``None``.
            llm_client: An optional :class:`~core.llm_client.LLMClient`
                instance.  The global client is resolved when ``None``.
            event_bus: An optional :class:`~core.event_bus.EventBus`.
                Falls back to an in-memory bus in development/testing.
            min_match_score: Minimum context-match score (0.0-1.0) for a
                listing to advance past analysis.
            batch_size: Maximum number of listings to process per cycle.
        """
        super().__init__(name="orchestrator", event_bus=event_bus)

        self._engine: AsyncEngine = engine or create_engine()
        self._llm: LLMClient = llm_client or get_llm_client()
        self._min_match_score: float = min_match_score
        self._batch_size: int = batch_size

        # Child agents -- lazy-initialised so they share the orchestrator's
        # engine and bus rather than creating their own.
        self._ingestion: IngestionAgent | None = None
        self._context: ContextAgent | None = None

        # Resolved during start().
        self._active_profile_id: uuid.UUID | None = None

        # Per-cycle statistics.
        self._stats: dict[str, int] = {
            "vectors_processed": 0,
            "jobs_discovered": 0,
            "jobs_analyzed": 0,
            "applications_created": 0,
            "errors": 0,
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise child-agent references and resolve the active profile."""
        await super().start()

        self._active_profile_id = await self._resolve_active_profile()

        # Initialise child agents with shared engine and event bus.
        self._ingestion = IngestionAgent(
            engine=self._engine,
            event_bus=self._event_bus,
        )
        self._context = ContextAgent(
            engine=self._engine,
            llm_client=self._llm,
            event_bus=self._event_bus,
        )
        await self._ingestion.start()
        await self._context.start()

        self.logger.info(
            "Orchestrator agent initialised",
            active_profile=(
                str(self._active_profile_id) if self._active_profile_id else None
            ),
        )

    async def stop(self) -> None:
        """Tear down child agents gracefully."""
        if self._context is not None:
            await self._context.stop()
        if self._ingestion is not None:
            await self._ingestion.stop()
        await super().stop()

    # ── BaseAgent interface ───────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Run one full orchestration cycle.

        Delegates to :meth:`run_once` so that :class:`OrchestratorAgent`
        satisfies the :class:`BaseAgent` abstract contract while keeping the
        single-cycle API explicit.
        """
        return await self.run_once()

    # ── Core orchestration logic ──────────────────────────────────────────────

    async def run_once(self) -> dict[str, Any]:
        """Execute a single orchestration cycle.

        Steps
        -----
        1. Guard -- abort early if no active profile exists.
        2. Load search vectors from the YAML overlay.
        3. Spawn :class:`IngestionAgent` and run it against all sources.
        4. Fetch listings that do **not** yet have an ``Application`` record
           for the active profile.
        5. For each such listing, run :class:`ContextAgent.analyze` to
           extract requirements and compute a match score.
        6. Create an ``Application`` record in ``DISCOVERED`` state.
        7. Emit a ``JOB_DISCOVERED`` event with analysis metadata.

        Returns
        -------
        dict[str, int]
            A summary with keys:

            - ``vectors_processed`` -- number of search vectors executed.
            - ``jobs_discovered``  -- new listings found during ingestion.
            - ``jobs_analyzed``    -- listings successfully analysed.
            - ``applications_created`` -- ``Application`` records created.
            - ``errors``           -- listings that failed during processing.
        """
        self._reset_stats()
        self.logger.info("Orchestrator run_once starting")

        # ── Step 1: Guard ─────────────────────────────────────────────────────
        if self._active_profile_id is None:
            self.logger.warning(
                "No active profile -- skipping orchestration cycle. "
                "Create a profile via the profile engine first."
            )
            return dict(self._stats)

        # ── Step 2: Load search vectors ───────────────────────────────────────
        vectors = self._load_search_vectors()
        self._stats["vectors_processed"] = len(vectors)
        self.logger.debug("Search vectors loaded", count=len(vectors))

        # ── Step 3: Ingestion ─────────────────────────────────────────────────
        ingestion = await self._get_ingestion_agent()
        try:
            ingestion_result = await ingestion.run()
            self._stats["jobs_discovered"] = ingestion_result.get("new_listings", 0)
        except GetAJobError as exc:
            self._stats["errors"] += 1
            self.logger.error("Ingestion phase failed", error=str(exc))

        # ── Step 4: Fetch unprocessed listings ────────────────────────────────
        listings = await self._fetch_unprocessed_listings()
        if not listings:
            self.logger.info("No unprocessed listings -- cycle complete")
            return dict(self._stats)

        self.logger.debug("Unprocessed listings fetched", count=len(listings))

        # ── Steps 5-7: Analyse, create application, emit event ────────────────
        context = await self._get_context_agent()

        for listing in listings:
            try:
                analysis = await context.analyze(
                    job_id=str(listing.id),
                    job_description=self._extract_description_text(listing),
                    profile_id=(
                        str(self._active_profile_id)
                        if self._active_profile_id
                        else None
                    ),
                )
                self._stats["jobs_analyzed"] += 1

                # Skip low-match listings when threshold is set.
                if self._min_match_score > 0 and analysis.match_score < self._min_match_score:
                    self.logger.debug(
                        "Skipping listing — below min_match_score threshold",
                        job_id=str(listing.id),
                        match_score=analysis.match_score,
                        threshold=self._min_match_score,
                    )
                    continue

                app = await self._create_application(listing, analysis)
                self._stats["applications_created"] += 1

                await self.emit_event(
                    EventType.JOB_DISCOVERED,
                    data={
                        "job_id": str(listing.id),
                        "application_id": str(app.id),
                        "company": listing.company,
                        "title": listing.title,
                        "source": listing.source,
                        "url": listing.url,
                        "match_score": analysis.match_score,
                        "matching_skills": analysis.matching_skills,
                        "missing_skills": analysis.missing_skills,
                        "warnings": analysis.warnings,
                    },
                    priority=EventPriority.NORMAL,
                )

            except GetAJobError as exc:
                self._stats["errors"] += 1
                self.logger.error(
                    "Failed to process listing",
                    job_id=str(listing.id),
                    error=str(exc),
                )
            except Exception:
                self._stats["errors"] += 1
                self.logger.exception(
                    "Unexpected error processing listing",
                    job_id=str(listing.id),
                )

        self.logger.info(
            "Orchestrator run_once complete",
            vectors_processed=self._stats["vectors_processed"],
            jobs_discovered=self._stats["jobs_discovered"],
            jobs_analyzed=self._stats["jobs_analyzed"],
            applications_created=self._stats["applications_created"],
            errors=self._stats["errors"],
        )

        return dict(self._stats)

    # ── Child-agent factories (lazy, shared lifecycle) ────────────────────────

    async def _get_ingestion_agent(self) -> IngestionAgent:
        """Return the shared :class:`IngestionAgent`, initialising on first call.

        The child agent uses the orchestrator's engine and event bus so that
        events published by the ingestion agent flow through the same bus
        instance.
        """
        if self._ingestion is None:
            self._ingestion = IngestionAgent(
                engine=self._engine,
                event_bus=self._event_bus,
            )
            await self._ingestion.start()
        return self._ingestion

    async def _get_context_agent(self) -> ContextAgent:
        """Return the shared :class:`ContextAgent`, initialising on first call."""
        if self._context is None:
            self._context = ContextAgent(
                engine=self._engine,
                llm_client=self._llm,
                event_bus=self._event_bus,
            )
            await self._context.start()
        return self._context

    # ── Profile resolution ────────────────────────────────────────────────────

    async def _resolve_active_profile(self) -> uuid.UUID | None:
        """Query the most recently updated active user profile.

        Returns:
            The profile's UUID, or ``None`` if no active profile exists.
        """
        async with get_session(self._engine) as session:
            result = await session.execute(
                select(UserProfile.id)
                .where(UserProfile.is_active.is_(True))
                .order_by(UserProfile.updated_at.desc())
                .limit(1)
            )
            row = result.one_or_none()
            return row[0] if row else None

    # ── Database queries ──────────────────────────────────────────────────────

    async def _fetch_unprocessed_listings(self) -> list[JobListing]:
        """Fetch active job listings that lack an ``Application`` record.

        Only listings for which no application exists *for the active profile*
        are returned.  This prevents duplicate processing across cycles.

        Returns:
            A list of :class:`JobListing` rows (capped at 100 per cycle).
        """
        if self._active_profile_id is None:
            return []

        async with get_session(self._engine) as session:
            # Sub-query: the set of job_listing_ids that already have an
            # Application for the active profile.
            existing_apps = (
                select(Application.job_listing_id)
                .where(Application.profile_id == self._active_profile_id)
                .scalar_subquery()
            )

            result = await session.execute(
                select(JobListing)
                .where(
                    JobListing.is_active.is_(True),
                    ~JobListing.id.in_(existing_apps),
                )
                .order_by(JobListing.created_at.desc())
                .limit(100)  # Safety cap -- bound memory per cycle.
            )
            return list(result.scalars().all())

    # ── Application creation ──────────────────────────────────────────────────

    async def _create_application(
        self,
        listing: JobListing,
        analysis: ContextAnalysis,
    ) -> Application:
        """Persist a new :class:`Application` in ``DISCOVERED`` state.

        The ``Application`` model defaults its ``state`` column to
        ``ApplicationState.DISCOVERED``, so no explicit state-machine
        transition is needed for the initial creation.

        Args:
            listing: The job listing to create an application for.
            analysis: The context analysis from
                :class:`~agents.context_agent.ContextAgent`.

        Returns:
            The newly-persisted :class:`Application` instance.

        Raises:
            GetAJobError: If no active profile is set (should not happen
                because :meth:`run_once` guards against this).
        """
        if self._active_profile_id is None:
            msg = "No active profile -- cannot create application"
            raise GetAJobError(msg, details={"job_id": str(listing.id)})

        async with get_session(self._engine) as session:
            app = Application(
                job_listing_id=listing.id,
                profile_id=self._active_profile_id,
                state=ApplicationState.DISCOVERED,
                notes=(
                    f"Match score: {analysis.match_score:.2f} | "
                    f"Skills: {len(analysis.matching_skills)} matching, "
                    f"{len(analysis.missing_skills)} missing"
                ),
            )
            session.add(app)
            await session.flush()
            app_id = app.id

        self.logger.debug(
            "Application record created",
            application_id=str(app_id),
            job_id=str(listing.id),
            company=listing.company,
            title=listing.title,
            match_score=analysis.match_score,
        )

        return app

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_description_text(listing: JobListing) -> str:
        """Extract the raw description text from the listing's JSONB column.

        Returns:
            The description text, or an empty string if unavailable.
        """
        if listing.description_json and isinstance(listing.description_json, dict):
            return listing.description_json.get("raw", "") or ""
        return ""

    def _load_search_vectors(self) -> list[SearchVectorConfig]:
        """Load search-vector configurations from the YAML overlay.

        Falls back to the same built-in defaults used by
        :class:`IngestionAgent` when the YAML file is absent.

        Returns:
            A (potentially empty) list of validated search-vector configs.
        """
        overlay = load_config()
        raw_vectors = overlay.get("search_vectors", [])

        if not raw_vectors:
            self.logger.info("No search vectors in config -- using built-in defaults")
            return [
                SearchVectorConfig(
                    roles=["senior software engineer", "staff engineer"],
                    keywords=["rust", "python", "distributed systems"],
                    locations=["remote", "san francisco", "new york"],
                    seniority=["senior", "staff"],
                    sources=["greenhouse", "lever"],
                ),
            ]

        vectors: list[SearchVectorConfig] = []
        for raw in raw_vectors:
            try:
                vectors.append(SearchVectorConfig(**raw))
            except Exception as exc:
                self.logger.warning("Invalid search vector -- skipping", error=str(exc))

        return vectors

    def _reset_stats(self) -> None:
        """Zero out the per-cycle statistics counters."""
        for key in self._stats:
            self._stats[key] = 0

    def __repr__(self) -> str:
        profile = str(self._active_profile_id) if self._active_profile_id else "none"
        return f"<OrchestratorAgent id={self.agent_id} active_profile={profile}>"
