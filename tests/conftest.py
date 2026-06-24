"""Shared test fixtures for the GetAJob test suite.

Note: ``core`` module imports trigger SQLAlchemy event-listener setup that
requires a running database.  All ``core`` imports here are made lazy (inside
their fixture functions) so that tests which don't need the database can run
standalone.
"""

from __future__ import annotations as _annotations

from typing import Any

import pytest

from outreach_engine.contact_finder import RecruiterInfo

# ── LLM client fixture (lazy import to avoid core init side effects) ────────


@pytest.fixture
def mock_llm_client() -> Any:
    """Return a :class:`~core.llm_client.MockLLMClient` with canned responses.

    Import is lazy because ``core.llm_client`` triggers SQLAlchemy
    event-listener setup.
    """
    # Lazy import to avoid triggering core.database event listeners.
    from core.llm_client import MockLLMClient

    return MockLLMClient(
        responses={
            "default": (
                "Subject: Applied — Software Engineer at Acme Corp\n"
                "---\n"
                "Hi Jane,\n\n"
                "I just applied for the Senior Engineer role at Acme. "
                "I've been impressed by your team's work on distributed systems.\n\n"
                "My background in Rust and Python seems like a strong match "
                "for what you're building.\n\n"
                "Happy to chat about my experience anytime.\n\n"
                "Best,\nJohn Doe"
            ),
        }
    )


# ── Sample recruiter contacts ──────────────────────────────────────────────


@pytest.fixture
def sample_recruiter() -> RecruiterInfo:
    """A well-formed recruiter contact for use in tests."""
    return RecruiterInfo(
        name="Jane Smith",
        title="Talent Acquisition Manager",
        email="jane.smith@acme.com",
        linkedin_url="https://linkedin.com/in/janesmith",
        confidence_score=0.75,
        source="linkedin",
        company="Acme Corp",
        notes="Found on LinkedIn people page",
    )


@pytest.fixture
def minimal_recruiter() -> RecruiterInfo:
    """A minimal valid contact (name only, no email)."""
    return RecruiterInfo(
        name="Bob Jones",
        title="Recruiter",
        confidence_score=0.5,
        source="website",
        company="Widgets Inc",
    )
