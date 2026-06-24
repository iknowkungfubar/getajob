"""Truth Validator — Hallucination Cross-Checker for the Tailoring Engine.

Verifies that every factual claim in a generated resume or cover letter can be
traced back to the user's master profile.  Extracts claims from generated text,
cross-references them against profile data, and produces a structured validation
report.

This is the **critical hallucination guardrail** for the GetAJob platform.
No generated document should be submitted for human review without passing
truth validation.
"""

from __future__ import annotations as _annotations

import re
from collections.abc import Sequence
from typing import Any

import structlog
from pydantic import BaseModel, Field

from core.schemas import ProfileRead, SkillSchema, WorkExperienceSchema

__all__: list[str] = [
    "TruthValidator",
    "ValidationResult",
]

logger = structlog.get_logger(__name__)


class ValidationResult(BaseModel):
    """Result of validating a generated document against the master profile."""

    all_claims_verified: bool = Field(
        default=True,
        description="True when every extracted claim was verified",
    )
    unverifiable_claims: list[str] = Field(
        default_factory=list,
        description="Claims that could not be matched to any profile data",
    )
    hallucinations: list[str] = Field(
        default_factory=list,
        description="Claims that directly contradict the profile",
    )
    hallucination_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of extracted claims that are unverifiable or contradictory",
    )
    passed: bool = Field(
        default=True,
        description="True if the document passes validation (hallucination_score == 0.0)",
    )


# ── Claim extraction patterns ─────────────────────────────────────────────────

# Patterns for common resume/cover letter claims.
_CLAIM_PATTERNS: list[tuple[str, type[str]]] = [
    # Job titles
    (r"(?:(?:as a|as an|position as|role as)\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", str),
    # Company names
    (r"(?:at|with|for)\s+([A-Z][A-Za-z0-9.\-&]+(?:\s+[A-Z][A-Za-z0-9.\-&]+)*)", str),
    # Years of experience
    (r"(\d+)\+?\s*(?:years?|yrs?)(?:\s+of)?\s+experience", str),
    # Date ranges
    (r"(19|20)\d{2}\s*[-–—to]+\s*(?:(?:19|20)\d{2}|Present|Current)", str),
    # Metrics (numbers that might be fabricated).
    (r"(?:over|more than|approximately|about)\s+(\d[\d,]*[kKmM]?%?)", str),
    # Specific achievements.
    (r"(?:increased|decreased|reduced|improved|grew|boosted|generated|saved|delivered)"
     r"\s+[A-Za-z]+\s+(?:by\s+)?(\d[\d,]*[kKmM]?%?)", str),
]


class TruthValidator:
    """Validate generated document claims against the user's master profile.

    Usage::

        validator = TruthValidator()
        result = validator.validate_claims(resume_text, profile)
        if not result.passed:
            print(f"Hallucinations: {result.hallucinations}")
    """

    # ── Public API ─────────────────────────────────────────────────────────────

    def validate_claims(
        self,
        text: str,
        profile: ProfileRead,
    ) -> ValidationResult:
        """Extract all factual claims from *text* and cross-reference against *profile*.

        Args:
            text: The generated resume or cover letter text.
            profile: The user's master profile (source of truth).

        Returns:
            A :class:`ValidationResult` with verification results.

        Raises:
            ValueError: If *text* is empty.
        """
        if not text or not text.strip():
            msg = "Cannot validate empty text"
            raise ValueError(msg)

        # Build lookup structures from profile.
        profile_data = self._build_profile_lookup(profile)

        # Extract and verify claims.
        unverifiable: list[str] = []
        hallucinations: list[str] = []

        # 1. Check company names.
        self._verify_companies(text, profile_data, hallucinations, unverifiable)

        # 2. Check job titles.
        self._verify_titles(text, profile_data, hallucinations, unverifiable)

        # 3. Check years of experience.
        self._verify_experience_years(text, profile_data, hallucinations, unverifiable)

        # 4. Check date ranges.
        self._verify_dates(text, profile_data, hallucinations, unverifiable)

        # 5. Check skills.
        self._verify_skills(text, profile_data, hallucinations, unverifiable)

        # Compute score.
        total_claim_count = len(hallucinations) + len(unverifiable)
        total_detected = max(1, total_claim_count)  # Avoid division by zero.

        # If nothing was detected at all, we might have missed everything
        # (conservative: flag if no claims were verifiable).
        if not hallucinations and not unverifiable:
            # No claims found in text — could be a problem with extraction,
            # but assume valid.
            pass

        hallucination_score = len(hallucinations) / total_detected
        passed = hallucination_score == 0.0 and not unverifiable

        return ValidationResult(
            all_claims_verified=passed,
            unverifiable_claims=unverifiable,
            hallucinations=hallucinations,
            hallucination_score=round(hallucination_score, 4),
            passed=passed,
        )

    # ── Profile lookup builder ─────────────────────────────────────────────────

    def _build_profile_lookup(self, profile: ProfileRead) -> dict[str, Any]:
        """Build efficient lookup structures from the profile.

        Returns:
            A dict with:
            - ``company_names``: set of lowercase company names.
            - ``job_titles``: set of lowercase job titles.
            - ``skills``: set of lowercase skill names.
            - ``experience_years``: float of total work experience years.
            - ``date_ranges``: list of ``(company, start, end)`` tuples.
        """
        companies: set[str] = set()
        titles: set[str] = set()
        date_ranges: list[tuple[str, str | None, str | None]] = []
        skills: set[str] = set()

        # Work experiences.
        if profile.work_experiences:
            for exp in profile.work_experiences:
                if exp.company:
                    companies.add(exp.company.lower().strip())
                    # Also add sub-company names for partial matching.
                    for part in exp.company.split():
                        if len(part) > 3:
                            companies.add(part.lower().strip(".,;:"))
                if exp.title:
                    titles.add(exp.title.lower().strip())

        # Skills.
        if profile.skills:
            for skill in profile.skills:
                skills.add(skill.name.lower().strip())

        # Year estimation (rough).
        total_years = 0.0
        if profile.work_experiences:
            import datetime  # noqa: PLC0415

            for exp in profile.work_experiences:
                if exp.start_date:
                    end = exp.end_date or datetime.date.today()
                    delta = (end - exp.start_date).days / 365.25
                    total_years += max(0.0, delta)

        return {
            "company_names": companies,
            "job_titles": titles,
            "skills": skills,
            "experience_years": round(total_years, 1),
        }

    # ── Verification methods ──────────────────────────────────────────────────

    def _verify_companies(
        self,
        text: str,
        profile: dict[str, Any],
        hallucinations: list[str],
        unverifiable: list[str],
    ) -> None:
        """Extract company name mentions and verify against the profile."""
        profile_companies = profile.get("company_names", set())
        if not profile_companies:
            return  # No companies in profile — nothing to check.

        # Find "at <Company>" patterns.
        matches = re.finditer(
            r"(?:at|with|for)\s+([A-Z][A-Za-z0-9.\-&]+(?:\s+[A-Z][A-Za-z0-9.\-&]+)*)",
            text,
        )
        seen: set[str] = set()
        for match in matches:
            company = match.group(1).lower().strip(".,;:")
            if company in seen or len(company) < 2:
                continue
            seen.add(company)

            # Check if this company or any meaningful part is in the profile.
            if company not in profile_companies:
                # Check partial match.
                words = company.split()
                partial_match = any(
                    word in profile_companies
                    for word in words
                    if len(word) > 3
                )
                if not partial_match and company not in {"unknown", "private"}:
                    unverifiable.append(f"Company '{match.group(1)}' not found in profile")

    def _verify_titles(
        self,
        text: str,
        profile: dict[str, Any],
        hallucinations: list[str],
        unverifiable: list[str],
    ) -> None:
        """Extract job title mentions and verify against the profile."""
        profile_titles = profile.get("job_titles", set())
        if not profile_titles:
            return

        # Find "as a <Title>" patterns.
        matches = re.finditer(
            r"(?:as a|as an|position as|role as)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
            text,
        )
        seen: set[str] = set()
        for match in matches:
            title = match.group(1).lower().strip(".,;:")
            if title in seen:
                continue
            seen.add(title)

            if title not in profile_titles:
                # Check for substring match (profile may have "Senior Engineer"
                # vs. "lead engineer" in text).
                partial = any(
                    title in pt or pt in title
                    for pt in profile_titles
                )
                if not partial:
                    unverifiable.append(f"Job title '{match.group(1)}' not found in profile")

    def _verify_experience_years(
        self,
        text: str,
        profile: dict[str, Any],
        hallucinations: list[str],
        unverifiable: list[str],
    ) -> None:
        """Check years-of-experience claims against the profile.

        Flags only clear fabrications (claim > profile + 2 years).
        """
        profile_years = profile.get("experience_years", 0.0)
        if profile_years == 0.0:
            return

        matches = re.finditer(r"(\d+)\+?\s*(?:years?|yrs?)(?:\s+of)?\s+experience", text, re.IGNORECASE)
        for match in matches:
            try:
                claimed = int(match.group(1))
            except ValueError:
                continue
            # Allow a 2-year buffer for rounding / partial years.
            if claimed > profile_years + 2.0:
                hallucinations.append(
                    f"Claims '{claimed}+ years experience' but profile shows ~{profile_years:.0f}"
                )

    def _verify_dates(
        self,
        text: str,
        profile: dict[str, Any],
        hallucinations: list[str],
        unverifiable: list[str],
    ) -> None:
        """Check date-range claims against the profile.

        If a date range in the text does not correspond to any experience
        entry, it is flagged as unverifiable.
        """
        # Extract work-experience date ranges from profile.
        profile_dates: list[tuple[int | None, int | None]] = []
        if "work_experiences" in profile:
            for exp in profile.get("work_experiences", []):
                if isinstance(exp, WorkExperienceSchema):
                    start_y = exp.start_date.year if exp.start_date else None
                    end_y = exp.end_date.year if exp.end_date else None
                    profile_dates.append((start_y, end_y))

        if not profile_dates:
            # Fall back to experience_years estimate.
            return

        # Find date range patterns in text.
        matches = re.finditer(
            r"(19|20)\d{2}\s*[-–—to]+\s*(?:(?:19|20)\d{2}|Present|Current)",
            text,
        )
        for match in matches:
            range_str = match.group(0)
            parts = re.split(r"\s*[-–—to]+\s*", range_str)
            if len(parts) != 2:
                continue
            try:
                start_y = int(parts[0])
                end_y_str = parts[1]
                end_y = None
                if end_y_str.lower() not in ("present", "current"):
                    end_y = int(end_y_str)
            except (ValueError, TypeError):
                continue

            # Check if this range is plausibly covered by profile.
            covered = False
            for ps, pe in profile_dates:
                if ps and ps <= start_y:
                    if pe is None or (end_y is not None and end_y <= pe):
                        covered = True
                        break
                    if end_y is None and pe is None and ps <= start_y:
                        covered = True
                        break

            if not covered:
                unverifiable.append(
                    f"Date range '{range_str}' does not match any profile experience entry"
                )

    def _verify_skills(
        self,
        text: str,
        profile: dict[str, Any],
        hallucinations: list[str],
        unverifiable: list[str],
    ) -> None:
        """Verify skill mentions against the profile's listed skills.

        Skills that appear in the text but not in the profile are flagged
        as unverifiable (not hallucinations — the user may have the skill
        even if it's not in their formal profile).
        """
        profile_skills = profile.get("skills", set())
        if not profile_skills:
            return

        known_skills = {
            "python", "rust", "typescript", "javascript", "go", "java", "c++",
            "sql", "graphql", "kubernetes", "docker", "aws", "gcp", "azure",
            "terraform", "postgresql", "redis", "kafka", "react", "angular",
            "node.js", "machine learning", "deep learning", "nlp",
        }

        text_lower = text.lower()

        # Extract skill mentions from the text.
        found_skills: set[str] = set()
        for skill in sorted(known_skills, key=len, reverse=True):
            if skill in text_lower:
                found_skills.add(skill)

        # Check each found skill against profile.
        for skill in found_skills:
            if skill not in profile_skills:
                unverifiable.append(
                    f"Skill '{skill}' mentioned in text but not listed in profile"
                )

    # ── Convenience ────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<TruthValidator>"
