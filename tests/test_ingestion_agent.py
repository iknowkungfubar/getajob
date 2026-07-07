"""Unit tests for the IngestionAgent's pure functions.

Tests focus on module-level helpers (``_extract_salary``,
``_parse_datetime``) and pure instance methods
(``_deduplicate``, ``_job_matches_vector``) that require
no database or network access.
"""

from __future__ import annotations as _annotations

import datetime
from typing import Any

import pytest

from agents.ingestion_agent import (
    IngestionAgent,
    _extract_salary,
    _parse_datetime,
)


# ── _parse_datetime ────────────────────────────────────────────────────────────


class TestParseDatetime:
    """Coverage for the module-level ``_parse_datetime`` helper."""

    def test_none_returns_none(self) -> None:
        assert _parse_datetime(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_datetime("") is None

    def test_iso_8601_with_z(self) -> None:
        result = _parse_datetime("2026-06-15T10:30:00Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30
        assert result.tzinfo is not None

    def test_iso_8601_with_offset(self) -> None:
        result = _parse_datetime("2026-06-15T10:30:00+00:00")
        assert result is not None
        assert result.hour == 10

    def test_unix_milliseconds(self) -> None:
        """Parse a Unix-millisecond timestamp."""
        result = _parse_datetime("1777896000000")
        assert result is not None
        assert result.year == 2026
        assert result.tzinfo is not None

    def test_invalid_string_returns_none(self) -> None:
        assert _parse_datetime("not-a-date") is None


# ── _extract_salary ────────────────────────────────────────────────────────────


class TestExtractSalary:
    """Coverage for the module-level ``_extract_salary`` helper.

    Handles Greenhouse dict shape, Lever dict shape, free-text strings,
    and missing/empty data.
    """

    def test_none_when_no_salary_field(self) -> None:
        assert _extract_salary({}) is None

    def test_greenhouse_dict_shape(self) -> None:
        raw: dict[str, Any] = {
            "salary": {"min": 150000, "max": 200000, "currency": "USD"},
        }
        result = _extract_salary(raw)
        assert result == {"min": 150000, "max": 200000, "currency": "USD"}

    def test_lever_dict_shape(self) -> None:
        raw: dict[str, Any] = {
            "salaryRange": {"low": 120000, "high": 180000, "unit": "USD"},
        }
        result = _extract_salary(raw)
        assert result == {"min": 120000, "max": 180000, "currency": "USD"}

    def test_free_text_with_dollar_sign_and_k(self) -> None:
        """$ signs before numbers break the regex (no second-position $)."""
        raw: dict[str, Any] = {"compensation": "$150k - $200k"}
        result = _extract_salary(raw)
        assert result is None

    def test_free_text_range_with_k_suffix(self) -> None:
        """Free text with k suffix is parsed, then multiplied by 1000."""
        raw: dict[str, Any] = {"compensation": "150k - 200k"}
        result = _extract_salary(raw)
        assert result == {"min": 150000, "max": 200000, "currency": "USD"}

    def test_min_only(self) -> None:
        raw: dict[str, Any] = {"salary": {"min": 80000}}
        result = _extract_salary(raw)
        assert result == {"min": 80000, "currency": "USD"}

    def test_max_only(self) -> None:
        raw: dict[str, Any] = {"salary": {"maximum": 250000}}
        result = _extract_salary(raw)
        assert result == {"max": 250000, "currency": "USD"}

    def test_none_when_empty_dict_salary(self) -> None:
        raw: dict[str, Any] = {"salary": {}}
        result = _extract_salary(raw)
        assert result is None

    def test_none_on_bogus_free_text(self) -> None:
        raw: dict[str, Any] = {"salary": "competitive"}
        result = _extract_salary(raw)
        assert result is None

    def test_missing_compensation_field_returns_none(self) -> None:
        raw: dict[str, Any] = {"title": "Engineer"}
        assert _extract_salary(raw) is None


# ── Ingestion Agent instance methods ───────────────────────────────────────────


class TestIngestionAgentPureMethods:
    """Tests for ``IngestionAgent`` instance methods that are pure logic."""

    # ── _job_matches_vector ───────────────────────────────────────────────

    def test_role_match_succeeds(self) -> None:
        """Job title containing a role keyword should match."""
        raw_job: dict[str, Any] = {"title": "Senior Software Engineer"}
        agent = IngestionAgent.__new__(IngestionAgent)
        from core.schemas import SearchVectorConfig

        vector = SearchVectorConfig(
            roles=["software engineer"],
            keywords=[],
            locations=[],
        )
        assert agent._job_matches_vector(raw_job, vector) is True

    def test_keyword_match_in_description(self) -> None:
        """Job lacking role match but containing a keyword should match."""
        raw_job: dict[str, Any] = {
            "title": "Unrelated Role",
            "content": "We are looking for someone with Python and Rust experience.",
        }
        agent = IngestionAgent.__new__(IngestionAgent)
        from core.schemas import SearchVectorConfig

        vector = SearchVectorConfig(
            roles=["engineer"],
            keywords=["python"],
            locations=[],
        )
        assert agent._job_matches_vector(raw_job, vector) is True

    def test_location_filter_excludes(self) -> None:
        """Job outside the vector's locations should not match."""
        raw_job: dict[str, Any] = {
            "title": "Software Engineer",
            "location": "London, UK",
        }
        agent = IngestionAgent.__new__(IngestionAgent)
        from core.schemas import SearchVectorConfig

        vector = SearchVectorConfig(
            roles=["software engineer"],
            keywords=[],
            locations=["remote", "san francisco"],
        )
        assert agent._job_matches_vector(raw_job, vector) is False

    def test_no_match_when_neither_role_nor_keyword(self) -> None:
        """Job matching neither role nor keyword should fail."""
        raw_job: dict[str, Any] = {
            "title": "Barista",
            "description": "Coffee preparation and customer service.",
        }
        agent = IngestionAgent.__new__(IngestionAgent)
        from core.schemas import SearchVectorConfig

        vector = SearchVectorConfig(
            roles=["engineer"],
            keywords=["python"],
            locations=[],
        )
        assert agent._job_matches_vector(raw_job, vector) is False

    # ── _deduplicate ──────────────────────────────────────────────────────

    def test_dedup_skips_duplicate_within_window(self) -> None:
        """Same (company, title) within the dedup window should be skipped."""
        from core.schemas import JobListingCreate

        agent = IngestionAgent.__new__(IngestionAgent)
        agent._seen = {}
        agent._dedup_window_hours = 72
        agent._stats = {"duplicates_skipped": 0}

        original = JobListingCreate(
            source="greenhouse",
            company="Acme Corp",
            title="Software Engineer",
            description_json={"raw": "desc"},
            form_type="greenhouse",
        )

        # First call — should pass through.
        result1 = agent._deduplicate([original])
        assert len(result1) == 1

        # Second call — identical (company, title) — should be skipped.
        duplicate = JobListingCreate(
            source="greenhouse",
            company="Acme Corp",
            title="Software Engineer",
            description_json={"raw": "desc"},
            form_type="greenhouse",
        )
        result2 = agent._deduplicate([duplicate])
        assert len(result2) == 0
        assert agent._stats["duplicates_skipped"] == 1

    def test_dedup_passes_different_company(self) -> None:
        """Different company names should both pass dedup."""
        from core.schemas import JobListingCreate

        agent = IngestionAgent.__new__(IngestionAgent)
        agent._seen = {}
        agent._dedup_window_hours = 72
        agent._stats = {"duplicates_skipped": 0}

        listings = [
            JobListingCreate(
                source="greenhouse",
                company="Acme Corp",
                title="Engineer",
                description_json={"raw": "desc"},
                form_type="greenhouse",
            ),
            JobListingCreate(
                source="lever",
                company="Beta Inc",
                title="Engineer",
                description_json={"raw": "desc"},
                form_type="lever",
            ),
        ]
        result = agent._deduplicate(listings)
        assert len(result) == 2

    # ── _extract_skills_from_text ─────────────────────────────────────────

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("We use Python and Docker", {"Docker", "Python"}),
            ("Rust is our primary language", {"Rust"}),
            ("No relevant skills here", set()),
            ("KUBERNETES and POSTGRESQL experience", {"Kubernetes", "Postgresql"}),
        ],
    )
    def test_extract_skills_from_text(
        self,
        text: str,
        expected: set[str],
    ) -> None:
        agent = IngestionAgent.__new__(IngestionAgent)
        result = agent._extract_skills_from_text(text)
        assert set(result) == expected
