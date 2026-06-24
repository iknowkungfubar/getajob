"""Tests for the MessageGenerator module.

Covers prompt building, response parsing, anti-AI guardrail application,
and integration with mock LLM client.
"""

from __future__ import annotations as _annotations

import datetime
import uuid

import pytest

from core.schemas import JobListingRead, OutreachMessageSchema, ProfileRead, SkillSchema, WorkExperienceSchema
from outreach_engine.message_generator import MessageGenerator, OutreachMessage


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_job_listing() -> JobListingRead:
    """A basic job listing for use in tests."""
    return JobListingRead(
        id=uuid.uuid4(),
        source="linkedin",
        company="Acme Corp",
        title="Senior Software Engineer",
        location="San Francisco, CA",
        description_json=None,
        url="https://linkedin.com/jobs/123",
        posted_date=datetime.datetime.now(datetime.UTC),
        required_skills=["Rust", "Python", "Distributed Systems"],
        salary_range=None,
        form_type="greenhouse",
        is_active=True,
        created_at=datetime.datetime.now(datetime.UTC),
        updated_at=datetime.datetime.now(datetime.UTC),
    )


@pytest.fixture
def sample_profile() -> ProfileRead:
    """A minimal user profile for testing."""
    return ProfileRead(
        id=uuid.uuid4(),
        version=1,
        name="John Doe",
        email="john@example.com",
        phone="+1-555-0100",
        location="San Francisco, CA",
        linkedin_url="https://linkedin.com/in/johndoe",
        portfolio_url=None,
        work_authorization="US Citizen",
        skills=[
            SkillSchema(name="Rust", category="language", proficiency="expert"),
            SkillSchema(name="Python", category="language", proficiency="expert"),
            SkillSchema(name="Kubernetes", category="tool", proficiency="proficient"),
        ],
        work_experiences=[
            WorkExperienceSchema(
                company="TechCo",
                title="Senior Engineer",
                start_date=datetime.date(2020, 1, 1),
                description="Built distributed systems",
                skills_used=["Rust", "Python"],
                is_current=True,
            ),
        ],
        answers=None,
        is_active=True,
        created_at=datetime.datetime.now(datetime.UTC),
        updated_at=datetime.datetime.now(datetime.UTC),
    )


# ── Tests ───────────────────────────────────────────────────────────────────


class TestGenerateOutreachMessage:
    """Tests for the main ``generate_outreach_message`` method."""

    @pytest.mark.asyncio
    async def test_generates_message_with_all_fields(
        self,
        sample_job_listing: JobListingRead,
        sample_profile: ProfileRead,
        mock_llm_client,
    ) -> None:
        generator = MessageGenerator(
            system_prompt_path="/dev/null",
            llm_client=mock_llm_client,
        )
        message = await generator.generate_outreach_message(
            job_listing=sample_job_listing,
            profile=sample_profile,
            recruiter_name="Jane Smith",
            recruiter_email="jane@acme.com",
            recruiter_title="Talent Acquisition Manager",
            tone="professional",
        )

        assert isinstance(message, OutreachMessage)
        assert message.to == "jane@acme.com"
        assert message.subject
        assert message.body
        assert message.tone == "professional"

    @pytest.mark.asyncio
    async def test_raises_on_unknown_tone(
        self,
        sample_job_listing: JobListingRead,
        sample_profile: ProfileRead,
        mock_llm_client,
    ) -> None:
        generator = MessageGenerator(
            system_prompt_path="/dev/null",
            llm_client=mock_llm_client,
        )
        from core.exceptions import OutreachError

        with pytest.raises(OutreachError, match="Unknown tone"):
            await generator.generate_outreach_message(
                job_listing=sample_job_listing,
                profile=sample_profile,
                tone="nonexistent",
            )


class TestParseResponse:
    """Tests for parsing LLM output into subject + body."""

    def test_parses_subject_and_body(self) -> None:
        response = "Subject: My Subject\n---\nHello world"
        subject, body = MessageGenerator._parse_response(response)
        assert subject == "My Subject"
        assert "Hello world" in body

    def test_fallback_subject_when_missing(self) -> None:
        response = "Just a body without a subject line"
        subject, body = MessageGenerator._parse_response(response)
        assert subject == "Application Follow-Up"


class TestGuardrails:
    """Tests for the anti-AI detection post-processing."""

    def test_applies_replacement(self) -> None:
        cleaned = MessageGenerator._apply_guardrails(
            "I am writing to express my interest in this role."
        )
        assert "I am writing to express my interest" not in cleaned
        # Should be replaced with something.
        assert len(cleaned) > 0

    def test_unchanged_when_clean(self) -> None:
        text = "Hi Jane, I just applied for the role. Happy to chat."
        cleaned = MessageGenerator._apply_guardrails(text)
        assert cleaned == text


class TestBuildPrompt:
    """Tests for prompt construction."""

    def test_includes_job_details(
        self,
        sample_job_listing: JobListingRead,
        sample_profile: ProfileRead,
    ) -> None:
        generator = MessageGenerator(
            system_prompt_path="/dev/null",
            llm_client=None,
        )
        tone_config = {
            "label": "Professional",
            "instructions": "Be professional.",
        }
        prompt = generator._build_prompt(
            job_listing=sample_job_listing,
            profile=sample_profile,
            recruiter_name="Jane Smith",
            recruiter_title="Recruiter",
            recruiter_linkedin=None,
            tone=tone_config,
            platform="email",
            company_context=None,
        )
        assert "Acme Corp" in prompt
        assert "Senior Software Engineer" in prompt
        assert "John Doe" in prompt
        assert "Be professional." in prompt
