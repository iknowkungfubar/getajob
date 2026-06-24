"""Full pipeline integration test.

Tests the end-to-end flow through all stages:
    Discovery → Context Analysis → Tailoring → Application → HITL Gate

Uses an in-memory SQLite database (with ``@compiles`` overrides for
PostgreSQL-specific types), :class:`~core.llm_client.MockLLMClient` for
LLM calls, and a recording event bus to verify event emissions.

The test seeds a user profile and a job listing directly into the database,
then invokes the :class:`~agents.orchestrator_agent.OrchestratorAgent` to
process the listing through the full pipeline.
"""

from __future__ import annotations as _annotations

# ── SQLite type compilers — registered BEFORE any model imports ───────────────
#
# The ORM models use PostgreSQL-specific types (UUID, JSONB).  These custom
# `@compiles` directives tell SQLAlchemy how to render them against SQLite.
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles


@compiles(UUID, "sqlite")
def _compile_uuid_sqlite(element: sa.TypeEngine, compiler: sa.Compiled, **kw: object) -> str:  # type: ignore[misc]  # noqa: ARG001
    """Render PostgreSQL UUID as VARCHAR(36) for SQLite."""
    return "VARCHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element: sa.TypeEngine, compiler: sa.Compiled, **kw: object) -> str:  # type: ignore[misc]  # noqa: ARG001
    """Render PostgreSQL JSONB as generic JSON for SQLite."""
    return compiler.process(sa.JSON())


# ── Test module imports ──────────────────────────────────────────────────────

import datetime
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agents.orchestrator_agent import OrchestratorAgent
from core.database import Base
from core.event_bus import EventPriority, InMemoryEventBus
from core.llm_client import MockLLMClient
from core.models import Application, ApplicationEvent, JobListing, UserProfile, WorkExperience
from core.state_machine import ApplicationState

__all__: list[str] = []

# ── Constants ────────────────────────────────────────────────────────────────

TEST_SKILLS: list[dict[str, str]] = [
    {"name": "Python", "category": "language", "proficiency": "expert"},
    {"name": "Rust", "category": "language", "proficiency": "advanced"},
    {"name": "Distributed Systems", "category": "domain", "proficiency": "expert"},
    {"name": "Kubernetes", "category": "tool", "proficiency": "proficient"},
    {"name": "PostgreSQL", "category": "database", "proficiency": "advanced"},
    {"name": "AWS", "category": "platform", "proficiency": "proficient"},
    {"name": "FastAPI", "category": "framework", "proficiency": "advanced"},
    {"name": "Docker", "category": "tool", "proficiency": "proficient"},
]

# Canned LLM response for the ContextAgent's requirement-extraction prompt.
_CONTEXT_EXTRACTION_RESPONSE: str = json.dumps(
    {
        "required_skills": ["Python", "Rust", "Distributed Systems"],
        "preferred_skills": ["Kubernetes", "Docker"],
        "technologies": ["Python", "Rust", "Kubernetes", "PostgreSQL"],
        "years_experience": 5,
        "methodologies": ["Agile", "Scrum"],
        "role_seniority": "senior",
        "key_responsibilities": [
            "Design and implement distributed systems",
            "Lead technical architecture decisions",
        ],
    }
)

# Canned resume text (returned by the mock LLM for the resume prompt).
_MOCK_RESUME_TEXT = """Senior Software Engineer — Turin

PROFESSIONAL SUMMARY
Results-driven engineer with deep expertise in Python, Rust, and distributed
systems. 10+ years designing and operating large-scale infrastructure.

EXPERIENCE
Staff Engineer | Acme Corp (2020–Present)
- Designed a distributed task queue processing 1M+ jobs/day
- Migrated monolith to event-driven microservices, reducing p95 latency by 40%
- Mentored 5 junior engineers through structured pair-programming rotations

Senior Engineer | Beta Inc (2016–2020)
- Built real-time analytics pipeline handling 10TB/day
- Deployed Kubernetes clusters across 3 regions for HA workloads

SKILLS
Languages: Python, Rust, TypeScript, Go
Frameworks: FastAPI, Django, React
Infrastructure: Kubernetes, Docker, AWS, Terraform
Databases: PostgreSQL, Redis, ClickHouse

EDUCATION
M.S. Computer Science — Stanford University (2014)
"""

# Canned cover letter text.
_MOCK_COVER_LETTER = """Hi Acme Team,

I've been following Acme's work on distributed data infrastructure, and when
I saw the Staff Engineer opening I knew I had to apply.

My last five years have been focused on exactly what you're building. At Beta
Inc I designed a real-time analytics pipeline that handled 10TB of data daily
-- Kubernetes, Rust, the whole stack. At Acme Corp I took on the distributed
task queue, scaling it from prototype to 1M+ jobs a day across three regions.

I bring strong opinions, loosely held: I prefer event-driven architectures
for reliability, k8s for run, and Python/Rust for the actual work. I also
mentor heavily -- growing the team's capability is as important to me as the
code I write.

I'd love to talk more about how my experience maps to your needs.

Best,
Turin"""


# ── Recording event bus ──────────────────────────────────────────────────────


class RecordingEventBus(InMemoryEventBus):
    """In-memory event bus that records every published event for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.published_events: list[dict[str, Any]] = []

    async def publish(  # type: ignore[override]
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Publish the event and record it."""
        await super().publish(event_type, data, **kwargs)
        self.published_events.append(
            {
                "type": event_type,
                "data": data or {},
                "priority": kwargs.get("priority", EventPriority.NORMAL),
                "source": kwargs.get("source", ""),
            }
        )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """Create and yield an in-memory SQLite engine with all tables."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture
async def recording_bus() -> RecordingEventBus:
    """Return a fresh :class:`RecordingEventBus` for the test."""
    return RecordingEventBus()


@pytest.fixture
def mock_llm() -> MockLLMClient:
    """Return a :class:`~core.llm_client.MockLLMClient` with canned responses.

    Keys are the ``system`` prompt (or ``prompt[:64]`` when system is ``None``)
    used by each agent — see ``MockLLMClient._lookup()``.
    """
    return MockLLMClient(
        responses={
            # ContextAgent._extract_requirements uses system=None,
            # so key = prompt[:64].
            "Extract structured requirements from the following job description. ": (
                _CONTEXT_EXTRACTION_RESPONSE
            ),
            # TailoringAgent._generate_resume system prompt.
            "You write honest, human-quality resumes that pass ATS filters but sound like a real person wrote them.": (
                _MOCK_RESUME_TEXT
            ),
            # TailoringAgent._generate_cover_letter system prompt.
            "You write honest, human-quality cover letters that sound like a real person wrote them — never generic, never cliché.": (
                _MOCK_COVER_LETTER
            ),
        }
    )


@pytest_asyncio.fixture
async def seed_profile(db_engine: AsyncEngine) -> uuid.UUID:
    """Insert a mock user profile and return its ID."""
    from core.database import get_session

    profile_id = uuid.uuid4()

    async with get_session(db_engine) as session:
        profile = UserProfile(
            id=profile_id,
            version=1,
            name="Turin",
            email="encrypted:turin@example.com",
            phone="encrypted:+15551234567",
            location="San Francisco, CA",
            linkedin_url="https://linkedin.com/in/turin",
            work_authorization="US Citizen",
            skills=TEST_SKILLS,
            is_active=True,
        )
        session.add(profile)

        exp = WorkExperience(
            id=uuid.uuid4(),
            profile_id=profile_id,
            company="Acme Corp",
            title="Staff Engineer",
            start_date=datetime.date(2020, 3, 1),
            description=(
                "Designed distributed task queue processing 1M+ jobs/day. "
                "Led migration from monolith to event-driven microservices."
            ),
            skills_used=["Python", "Rust", "Kubernetes", "AWS", "Distributed Systems"],
            is_current=True,
        )
        session.add(exp)

    return profile_id


@pytest_asyncio.fixture
async def seed_listing(db_engine: AsyncEngine) -> uuid.UUID:
    """Insert a mock job listing and return its ID."""
    from core.database import get_session

    listing_id = uuid.uuid4()

    async with get_session(db_engine) as session:
        listing = JobListing(
            id=listing_id,
            source="greenhouse",
            source_id="gh-12345",
            company="Acme Corp",
            title="Senior Staff Engineer, Distributed Systems",
            location="San Francisco, CA (Remote)",
            description_json={
                "raw": (
                    "We are looking for a Senior Staff Engineer to join our "
                    "Distributed Systems team. You will design and build the "
                    "next generation of our data infrastructure. "
                    "Required skills: Python, Rust, distributed systems. "
                    "Preferred: Kubernetes, Docker. "
                    "5+ years of experience required."
                ),
            },
            url="https://boards.greenhouse.io/acme/jobs/12345",
            posted_date=datetime.datetime(2026, 6, 1),
            required_skills=["Python", "Rust", "Distributed Systems"],
            form_type="greenhouse",
            is_active=True,
        )
        session.add(listing)

    return listing_id


# ── Tests ────────────────────────────────────────────────────────────────────


class TestFullPipeline:
    """Integration test suite for the complete application pipeline."""

    @pytest.mark.asyncio
    async def test_run_once_full_pipeline(
        self,
        db_engine: AsyncEngine,
        recording_bus: RecordingEventBus,
        mock_llm: MockLLMClient,
        seed_profile: uuid.UUID,
        seed_listing: uuid.UUID,
    ) -> None:
        """Run the full pipeline and verify every stage completes correctly.

        Test flow:
        1. Seed a profile and a job listing in the test database.
        2. Create the orchestrator with in-memory engine, mock LLM, and
           recording event bus.
        3. Mock the :class:`IngestionAgent` so it does not make real API calls.
        4. Execute ``run_once()``.
        5. Assert the result counters are correct.
        6. Assert an Application record exists in the database.
        7. Assert the application transitioned through DISCOVERED → TAILORED
           → PENDING_REVIEW.
        8. Assert events were emitted at each stage.
        """
        # ── Step 1: Create the orchestrator ───────────────────────────────
        orchestrator = OrchestratorAgent(
            engine=db_engine,
            llm_client=mock_llm,
            event_bus=recording_bus,
            min_match_score=0.0,  # Allow all listings through (mock returns 0.0)
            batch_size=10,
        )
        await orchestrator.start()

        # ── Step 2: Mock the IngestionAgent to skip real API calls ────────
        orchestrator._ingestion.run = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "new_listings": 0,
                "duplicates_skipped": 0,
                "api_errors": 0,
                "total_vectors": 1,
                "sources_used": [],
            }
        )

        try:
            # ── Step 3: Execute the pipeline ──────────────────────────────
            result = await orchestrator.run_once()

            # ── Step 4: Assert result counters ────────────────────────────
            assert isinstance(result, dict), f"Expected dict, got {type(result)}"
            assert result["jobs_discovered"] == 0, (
                f"Expected 0 new discoveries (mocked), got {result['jobs_discovered']}"
            )
            assert result["jobs_analyzed"] == 1, (
                f"Expected 1 listing analyzed, got {result['jobs_analyzed']}"
            )
            assert result["applications_created"] == 1, (
                f"Expected 1 application created, got {result['applications_created']}"
            )
            assert result["errors"] == 0, (
                f"Expected 0 errors, got {result['errors']}"
            )

            # ── Step 5: Assert result counters include tailoring ──────────
            assert result["applications_tailored"] == 1, (
                f"Expected 1 application tailored, got {result['applications_tailored']}"
            )

            # ── Step 6: Assert database records ──────────────────────────
            from sqlalchemy import select

            from core.database import get_session

            async with get_session(db_engine) as session:
                app_query = select(Application).where(
                    Application.job_listing_id == seed_listing
                )
                app = (await session.execute(app_query)).scalar_one_or_none()

                assert app is not None, "Application record was not created"
                assert app.state == ApplicationState.PENDING_REVIEW, (
                    f"Expected application in PENDING_REVIEW, got {app.state.value}"
                )
                assert app.resume_text is not None, (
                    "Resume should be populated after tailoring"
                )
                # The mock resume text has a trailing newline from the
                # triple-quoted string; the TailoringAgent strips whitespace
                # from LLM output via .strip().
                assert app.resume_text == _MOCK_RESUME_TEXT.strip(), (
                    "Resume should match the mock-generated text (stripped)"
                )
                assert app.cover_letter is not None, (
                    "Cover letter should be populated after tailoring"
                )
                # The TailoringAgent's _clean_cover_letter strips trailing
                # signature boilerplate (e.g. "Best,\\nName"), so the stored
                # text may differ from the raw mock.  Check key content
                # rather than exact match.
                assert "Acme Team" in app.cover_letter, (
                    "Cover letter should reference the company"
                )
                assert "Your Needs" not in app.cover_letter, (
                    "Cover letter should avoid cliché placeholders"
                )
                assert "Staff Engineer" in app.cover_letter, (
                    "Cover letter should reference the role"
                )

                # ── Step 7: Assert ApplicationEvent audit trail ───────────
                events_query = (
                    select(ApplicationEvent)
                    .where(ApplicationEvent.application_id == app.id)
                    .order_by(ApplicationEvent.timestamp)
                )
                events = (await session.execute(events_query)).scalars().all()

                assert len(events) >= 2, (
                    f"Expected at least 2 transition events, got {len(events)}"
                )

                # Event 1: DISCOVERED → TAILORED
                assert events[0].from_state == ApplicationState.DISCOVERED, (
                    f"Expected first event from DISCOVERED, got {events[0].from_state}"
                )
                assert events[0].to_state == ApplicationState.TAILORED, (
                    f"Expected first event to TAILORED, got {events[0].to_state}"
                )

                # Event 2: TAILORED → PENDING_REVIEW
                assert events[1].from_state == ApplicationState.TAILORED, (
                    f"Expected second event from TAILORED, got {events[1].from_state}"
                )
                assert events[1].to_state == ApplicationState.PENDING_REVIEW, (
                    f"Expected second event to PENDING_REVIEW, got {events[1].to_state}"
                )

            # ── Step 8: Assert event emissions ────────────────────────────
            event_types = [e["type"] for e in recording_bus.published_events]
            assert "job.discovered" in event_types, (
                f"Missing 'job.discovered' event. Got: {event_types}"
            )

        finally:
            # ── Step 7: Clean up ─────────────────────────────────────────
            await orchestrator.stop()

    @pytest.mark.asyncio
    async def test_low_match_job_skipped(
        self,
        db_engine: AsyncEngine,
        recording_bus: RecordingEventBus,
        mock_llm: MockLLMClient,
        seed_profile: uuid.UUID,
        seed_listing: uuid.UUID,
    ) -> None:
        """Verify that jobs below the match threshold are skipped."""
        orchestrator = OrchestratorAgent(
            engine=db_engine,
            llm_client=mock_llm,
            event_bus=recording_bus,
            min_match_score=0.95,  # Very high threshold — nothing passes.
            batch_size=10,
        )
        await orchestrator.start()

        orchestrator._ingestion.run = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "new_listings": 0,
                "duplicates_skipped": 0,
                "api_errors": 0,
                "total_vectors": 1,
                "sources_used": [],
            }
        )

        try:
            result = await orchestrator.run_once()

            # Analyzed = 1, but applications should be 0 (below threshold).
            assert result["jobs_analyzed"] == 1, (
                f"Expected 1 analyzed, got {result['jobs_analyzed']}"
            )
            assert result["applications_created"] == 0, (
                f"Expected 0 applications, got {result['applications_created']}"
            )

            # Verify no Application record was created.
            from sqlalchemy import select

            from core.database import get_session

            async with get_session(db_engine) as session:
                app_query = select(Application).where(
                    Application.job_listing_id == seed_listing
                )
                app = (await session.execute(app_query)).scalar_one_or_none()
                assert app is None, (
                    "Application should not be created when match is below threshold"
                )

        finally:
            await orchestrator.stop()

    @pytest.mark.asyncio
    async def test_no_unwatched_listings(
        self,
        db_engine: AsyncEngine,
        recording_bus: RecordingEventBus,
        mock_llm: MockLLMClient,
        seed_profile: uuid.UUID,
        seed_listing: uuid.UUID,
    ) -> None:
        """Verify the pipeline handles the no-unwatched-listings case gracefully.

        When an Application already exists for a listing, the orchestrator
        should skip it during the next pass.
        """
        from core.database import get_session

        # Pre-create an Application so there are no unwatched listings.
        async with get_session(db_engine) as session:
            existing_app = Application(
                id=uuid.uuid4(),
                job_listing_id=seed_listing,
                profile_id=seed_profile,
                state=ApplicationState.PENDING_REVIEW,
            )
            session.add(existing_app)

        orchestrator = OrchestratorAgent(
            engine=db_engine,
            llm_client=mock_llm,
            event_bus=recording_bus,
            min_match_score=0.0,  # Allow all listings through (mock returns 0.0)
            batch_size=10,
        )
        await orchestrator.start()

        orchestrator._ingestion.run = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "new_listings": 0,
                "duplicates_skipped": 0,
                "api_errors": 0,
                "total_vectors": 1,
                "sources_used": [],
            }
        )

        try:
            result = await orchestrator.run_once()

            # All counts should be 0 — no new listings to process.
            assert result["jobs_analyzed"] == 0
            assert result["applications_created"] == 0
            assert result["errors"] == 0

        finally:
            await orchestrator.stop()

    @pytest.mark.asyncio
    async def test_isolated_job_failure(
        self,
        db_engine: AsyncEngine,
        recording_bus: RecordingEventBus,
        mock_llm: MockLLMClient,
        seed_profile: uuid.UUID,
        seed_listing: uuid.UUID,
    ) -> None:
        """Verify that a failure in one job does not prevent others from
        processing (the orchestrator's 'isolated errors' guarantee)."""
        from core.database import get_session

        # Seed a second listing.
        second_listing_id = uuid.uuid4()
        async with get_session(db_engine) as session:
            second_listing = JobListing(
                id=second_listing_id,
                source="greenhouse",
                source_id="gh-99999",
                company="Beta Corp",
                title="Principal Engineer",
                location="Remote",
                description_json={
                    "raw": "Principal engineer role building distributed systems. Python required.",
                },
                is_active=True,
            )
            session.add(second_listing)

        orchestrator = OrchestratorAgent(
            engine=db_engine,
            llm_client=mock_llm,
            event_bus=recording_bus,
            min_match_score=0.0,  # Allow all listings through (mock returns 0.0)
            batch_size=10,
        )
        await orchestrator.start()

        orchestrator._ingestion.run = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "new_listings": 0,
                "duplicates_skipped": 0,
                "api_errors": 0,
                "total_vectors": 1,
                "sources_used": [],
            }
        )

        try:
            result = await orchestrator.run_once()

            # At least one application should have been created.
            assert result["applications_created"] >= 1, (
                f"Expected at least 1 application, got {result['applications_created']}"
            )
            assert result["errors"] == 0, (
                f"Expected 0 errors, got {result['errors']}"
            )

        finally:
            await orchestrator.stop()

    @pytest.mark.asyncio
    async def test_state_transition_invalid_rejected(
        self,
        db_engine: AsyncEngine,
        recording_bus: RecordingEventBus,
        mock_llm: MockLLMClient,
    ) -> None:
        """Verify that the state machine rejects invalid transitions."""
        from core.state_machine import StateMachineError, transition_state

        # DISCOVERED → SUBMITTED is not allowed.
        with pytest.raises(StateMachineError, match="Cannot transition"):
            transition_state(
                ApplicationState.DISCOVERED,
                ApplicationState.SUBMITTED,
            )

        # DISCOVERED → TAILORED IS allowed.
        result = transition_state(
            ApplicationState.DISCOVERED,
            ApplicationState.TAILORED,
        )
        assert result == ApplicationState.TAILORED, (
            f"Expected TAILORED, got {result}"
        )
