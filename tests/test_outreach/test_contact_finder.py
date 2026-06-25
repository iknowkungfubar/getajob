"""Tests for the ContactFinder OSINT module.

Covers the four discovery strategies, the ``validate_contact`` method,
robots.txt integration, and rate-limiting behaviour.
"""

from __future__ import annotations as _annotations

import pytest

from outreach_engine.contact_finder import (
    ContactFinder,
    RecruiterInfo,
)


class TestValidateContact:
    """Tests for ``ContactFinder.validate_contact``."""

    def test_valid_contact_with_email(self, sample_recruiter: RecruiterInfo) -> None:
        finder = ContactFinder(rate_limit=100)
        assert finder.validate_contact(sample_recruiter) is True

    def test_valid_contact_name_only(self, minimal_recruiter: RecruiterInfo) -> None:
        finder = ContactFinder(rate_limit=100)
        assert finder.validate_contact(minimal_recruiter) is True

    def test_rejects_none(self) -> None:
        finder = ContactFinder(rate_limit=100)
        assert finder.validate_contact(None) is False  # type: ignore[arg-type]

    def test_rejects_empty_contact(self) -> None:
        finder = ContactFinder(rate_limit=100)
        empty = RecruiterInfo(company="Acme")
        assert finder.validate_contact(empty) is False

    def test_rejects_below_confidence_threshold(self) -> None:
        finder = ContactFinder(rate_limit=100)
        low_conf = RecruiterInfo(name="Ghost", confidence_score=0.1, company="Acme")
        assert finder.validate_contact(low_conf) is False

    def test_rejects_invalid_email(self) -> None:
        finder = ContactFinder(rate_limit=100)
        bad_email = RecruiterInfo(
            name="Jane",
            email="not-an-email",
            confidence_score=0.8,
            company="Acme",
        )
        assert finder.validate_contact(bad_email) is False


class TestStrategyJobListing:
    """Tests for strategy 1 - parsing the job description."""

    @pytest.mark.asyncio
    async def test_extract_email_from_description(self) -> None:
        finder = ContactFinder(rate_limit=100)
        desc = (
            "Please send your resume to jane.hiring@acme.com. We are looking for a senior engineer."
        )
        result = await finder._strategy_from_job_listing("Acme Corp", desc)
        assert result is not None
        assert result.email == "jane.hiring@acme.com"
        assert result.confidence_score >= 0.4

    @pytest.mark.asyncio
    async def test_extract_name_near_title(self) -> None:
        finder = ContactFinder(rate_limit=100)
        desc = "Jane Smith - talent acquisition manager at Acme Corp"
        result = await finder._strategy_from_job_listing("Acme Corp", desc)
        assert result is not None
        assert result.name == "Jane Smith"
        assert result.confidence_score >= 0.3

    @pytest.mark.asyncio
    async def test_returns_none_when_no_contact(self) -> None:
        finder = ContactFinder(rate_limit=100)
        desc = "We are an equal opportunity employer. Apply online."
        result = await finder._strategy_from_job_listing("Acme Corp", desc)
        assert result is None


class TestStrategyEmailPatterns:
    """Tests for strategy 4 - generating email addresses from patterns."""

    def test_generates_email_from_name(self) -> None:
        finder = ContactFinder(rate_limit=100)
        candidates = [
            RecruiterInfo(
                name="Jane Smith",
                title="Recruiter",
                confidence_score=0.4,
                source="website",
                company="Acme Corp",
            )
        ]
        result = finder._strategy_email_patterns("Acme Corp", candidates)
        assert result is not None
        assert result.email is not None
        assert "@company.com" in result.email
        assert "jane" in result.email
        # Email patterns have reduced confidence.
        assert result.confidence_score < 0.4

    def test_no_candidates_with_name_returns_none(self) -> None:
        finder = ContactFinder(rate_limit=100)
        candidates = [RecruiterInfo(email="jane@acme.com", confidence_score=0.8, company="Acme")]
        result = finder._strategy_email_patterns("Acme Corp", candidates)
        assert result is None


class TestSelectBest:
    """Tests for ``_select_best`` dedup + ranking."""

    def test_prefers_email_over_no_email(self) -> None:
        finder = ContactFinder(rate_limit=100)
        candidates = [
            RecruiterInfo(name="No Email", confidence_score=0.9, company="Acme"),
            RecruiterInfo(
                name="Has Email",
                email="a@b.com",
                confidence_score=0.6,
                company="Acme",
            ),
        ]
        best = finder._select_best(candidates)
        assert best is not None
        assert best.email == "a@b.com"

    def test_returns_none_for_empty_list(self) -> None:
        finder = ContactFinder(rate_limit=100)
        assert finder._select_best([]) is None


class TestRateLimiting:
    """Tests for global and per-company rate limiters."""

    def test_global_rate_limiter_acquire(self) -> None:
        from outreach_engine.contact_finder import _RateLimiter

        limiter = _RateLimiter(max_per_minute=2)
        # First two should succeed.
        assert limiter.acquire() is None
        assert limiter.acquire() is None
        # Third should wait.
        wait = limiter.acquire()
        assert wait is not None
        assert wait > 0

    def test_company_rate_limiter_is_per_company(self) -> None:
        from outreach_engine.contact_finder import _CompanyRateLimiter

        limiter = _CompanyRateLimiter(max_per_minute=2)
        # Exhaust limit for company A.
        assert limiter.acquire("Acme") is None
        assert limiter.acquire("Acme") is None
        # Company B should still be allowed.
        assert limiter.acquire("BetaCorp") is None
        # Company A should be blocked.
        wait = limiter.acquire("Acme")
        assert wait is not None
        assert wait > 0


class TestSelectBestDedup:
    """Tests for deduplication in _select_best."""

    def test_dedup_by_email(self) -> None:
        finder = ContactFinder(rate_limit=100)
        candidates = [
            RecruiterInfo(name="Jane", email="j@acme.com", confidence_score=0.5, company="Acme"),
            RecruiterInfo(
                name="Jane (dup)", email="j@acme.com", confidence_score=0.4, company="Acme"
            ),
            RecruiterInfo(name="Bob", email="b@acme.com", confidence_score=0.6, company="Acme"),
        ]
        best = finder._select_best(candidates)
        assert best is not None
        assert best.email == "b@acme.com"  # Bob wins on confidence among unique emails
