"""Context Agent - Job Description Analysis & Profile Matching (Module 2 adjunct).

Takes a raw job description, uses the LLM to extract structured requirements,
and maps them against the user's profile to compute a match score.

Key behaviours:
- **Semantic extraction**: Uses the LLM to extract required skills,
  technologies, experience level, and methodologies from free-form text.
- **Profile matching**: Queries the :class:`~profile_engine.profile_store.ProfileStore`
  and :class:`~profile_engine.vector_store.VectorStore` to match the profile
  against job requirements.
- **Structured output**: Returns a :class:`ContextAnalysis` object with match
  score, matching/missing skills, and relevance indicators.
"""

from __future__ import annotations as _annotations

import statistics
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from agents.base import BaseAgent
from core.database import create_engine
from core.exceptions import TailoringError
from core.llm_client import LLMClient, get_llm_client

__all__: list[str] = [
    "ContextAgent",
    "ContextAnalysis",
    "ExtractedRequirements",
]


# ── Output schemas ────────────────────────────────────────────────────────────


class ExtractedRequirements(BaseModel):
    """Structured requirements extracted from a job description by the LLM."""

    required_skills: list[str] = Field(
        default_factory=list,
        description="Technical skills explicitly listed as required",
    )
    preferred_skills: list[str] = Field(
        default_factory=list,
        description="Skills listed as preferred or nice-to-have",
    )
    technologies: list[str] = Field(
        default_factory=list,
        description="Technologies, frameworks, platforms mentioned",
    )
    years_experience: int | None = Field(
        default=None,
        description="Minimum years of experience explicitly stated",
    )
    methodologies: list[str] = Field(
        default_factory=list,
        description="Development methodologies mentioned (agile, scrum, TDD, etc.)",
    )
    role_seniority: str | None = Field(
        default=None,
        description="Inferred seniority level (junior, mid, senior, staff, principal)",
    )
    key_responsibilities: list[str] = Field(
        default_factory=list,
        description="Key responsibilities mentioned in the description",
    )


class ContextAnalysis(BaseModel):
    """Complete analysis of a job listing against the user's profile.

    Returned by :meth:`ContextAgent.analyze`.
    """

    job_id: str
    profile_id: str | None = None

    # Extracted requirements.
    requirements: ExtractedRequirements = Field(default_factory=ExtractedRequirements)

    # Match metrics.
    match_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Overall match score (0.0-1.0)",
    )
    matching_skills: list[str] = Field(
        default_factory=list,
        description="Required skills present in the user's profile",
    )
    missing_skills: list[str] = Field(
        default_factory=list,
        description="Required skills not found in the user's profile",
    )
    partial_skills: list[str] = Field(
        default_factory=list,
        description="Skills the user has at a lower proficiency or adjacent area",
    )

    # Relevance indicators.
    relevance_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Semantic relevance score from vector search (0.0-1.0)",
    )
    top_relevant_chunks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Most relevant profile chunks from vector search",
    )

    # Warnings.
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings about potential issues (e.g. seniority mismatch)",
    )


# ── Context Agent ─────────────────────────────────────────────────────────────


class ContextAgent(BaseAgent):
    """Analyse a job description and match it against the user's profile.

    Uses the configured LLM to extract structured requirements from raw job
    text, then cross-references them with the user's profile (stored skills,
    work history, and vector-store embeddings) to produce a rich
    :class:`ContextAnalysis`.

    Usage::

        agent = ContextAgent(llm_client=claude_client)
        analysis = await agent.analyze(
            job_id="uuid-here",
            job_description="...",
            profile_id="uuid-here",
        )
    """

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        *,
        llm_client: LLMClient | None = None,
        event_bus: Any | None = None,
    ) -> None:
        super().__init__(name="context", event_bus=event_bus)

        self._engine: AsyncEngine = engine or create_engine()
        self._llm: LLMClient = llm_client or get_llm_client()

        # Lazy-loaded profile store (needs engine).
        self._profile_store_val: Any = None

    @property
    def _profile_store(self) -> Any:
        """Lazily initialised :class:`~profile_engine.profile_store.ProfileStore`."""
        if self._profile_store_val is None:
            from profile_engine.profile_store import ProfileStore
            self._profile_store_val = ProfileStore(self._engine)
        return self._profile_store_val

    @_profile_store.setter
    def _profile_store(self, value: Any) -> None:
        self._profile_store_val = value

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise profile-store reference."""
        await super().start()
        self._profile_store = None  # Set on first use via lazy import.

    # ── Main analysis method ──────────────────────────────────────────────

    async def analyze(
        self,
        job_id: str,
        job_description: str,
        profile_id: str | None = None,
    ) -> ContextAnalysis:
        """Analyse a job description and return a structured match against the profile.

        Args:
            job_id: The UUID of the job listing to analyse.
            job_description: Raw job description text (any length).
            profile_id: Optional profile UUID.  If omitted, the active profile
                is loaded automatically.

        Returns:
            A :class:`ContextAnalysis` with extraction and matching results.

        Raises:
            TailoringError: If the LLM call fails or the result cannot be parsed.
        """
        if not job_description or not job_description.strip():
            msg = "Job description is empty"
            raise TailoringError(msg, details={"job_id": job_id})

        # Step 1: Extract structured requirements via LLM.
        requirements = await self._extract_requirements(job_description)

        # Step 2: Load the user's profile.
        profile_skills = await self._load_profile_skills(profile_id)

        # Step 3: Compute skill match.
        matching, missing, partial = self._match_skills(requirements, profile_skills)

        # Step 4: Compute vector relevance (semantic search).
        relevance_score, top_chunks = await self._semantic_relevance(job_description, profile_id)

        # Step 5: Compute overall match score.
        match_score = self._compute_match_score(
            matching=matching,
            missing=missing,
            partial=partial,
            relevance_score=relevance_score,
        )

        # Step 6: Generate warnings.
        warnings = self._generate_warnings(requirements, profile_skills, match_score)

        analysis = ContextAnalysis(
            job_id=job_id,
            profile_id=profile_id,
            requirements=requirements,
            match_score=round(match_score, 4),
            matching_skills=matching,
            missing_skills=missing,
            partial_skills=partial,
            relevance_score=round(relevance_score, 4),
            top_relevant_chunks=top_chunks,
            warnings=warnings,
        )

        self.logger.debug(
            "Context analysis complete",
            job_id=job_id,
            match_score=match_score,
            matching=len(matching),
            missing=len(missing),
        )

        return analysis

    # ─── Agent interface ──────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Run analysis on all pending job listings (intended for orchestrator mode).

        Returns:
            A summary dict with the number of analyses performed.
        """
        self.logger.info("Context agent run - no pending jobs to analyse (orchestrator-driven)")
        return {"analyses_performed": 0}

    # ── Step 1: LLM extraction ─────────────────────────────────────────────

    async def _extract_requirements(self, text: str) -> ExtractedRequirements:
        """Use the LLM to extract structured requirements from job description text.

        Args:
            text: Raw job description text.

        Returns:
            A structured :class:`ExtractedRequirements` object.

        Raises:
            TailoringError: If the LLM response cannot be parsed.
        """
        prompt = (
            "Extract structured requirements from the following job description. "
            "Return a JSON object with these keys:\n"
            "- required_skills: list of technical skills explicitly listed as required\n"
            "- preferred_skills: list of skills listed as preferred or nice-to-have\n"
            "- technologies: list of technologies, frameworks, or platforms mentioned\n"
            "- years_experience: integer of minimum years of experience stated (or null)\n"
            "- methodologies: list of development methodologies mentioned (e.g. agile, scrum, TDD)\n"
            "- role_seniority: string of inferred seniority level (junior, mid, senior, staff, principal, lead, or null)\n"
            "- key_responsibilities: list of key responsibilities mentioned\n\n"
            "Be precise - only extract what is explicitly stated or clearly implied.\n\n"
            f"JOB DESCRIPTION:\n{text[:8000]}"
        )

        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "required_skills": {"type": "array", "items": {"type": "string"}},
                "preferred_skills": {"type": "array", "items": {"type": "string"}},
                "technologies": {"type": "array", "items": {"type": "string"}},
                "years_experience": {"type": ["integer", "null"]},
                "methodologies": {"type": "array", "items": {"type": "string"}},
                "role_seniority": {"type": ["string", "null"]},
                "key_responsibilities": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "required_skills",
                "preferred_skills",
                "technologies",
                "years_experience",
                "methodologies",
                "role_seniority",
                "key_responsibilities",
            ],
        }

        try:
            result = await self._llm.generate_structured(prompt, schema, max_tokens=2048, temperature=0.3)
        except Exception as exc:
            msg = f"LLM requirement extraction failed: {exc}"
            raise TailoringError(msg, details={"text_length": len(text)}) from exc

        return ExtractedRequirements(**result)

    # ── Step 2: Profile loading ────────────────────────────────────────────

    async def _load_profile_skills(self, profile_id: str | None = None) -> dict[str, Any]:
        """Load the user's skills and work history from the profile store.

        Delegates to :meth:`ProfileStore.load_profile_with_skills` to avoid
        duplicating the profile-loading logic (shared with TailoringAgent).

        Returns:
            A dict with keys: ``skill_names`` (flat list), ``skills_by_category``
            (dict), ``work_experiences`` (list), ``experience_years`` (float).
        """
        result = await self._profile_store.load_profile_with_skills(profile_id)

        self.logger.debug(
            "Loaded profile skills",
            profile_id=profile_id or "(active)",
            skill_count=len(result.get("skill_names", [])),
            experience_years=round(result.get("experience_years", 0.0), 1),
        )
        return result

    # ── Step 3: Skill matching ─────────────────────────────────────────────

    def _match_skills(
        self,
        requirements: ExtractedRequirements,
        profile: dict[str, Any],
    ) -> tuple[list[str], list[str], list[str]]:
        """Compare extracted requirements against the profile's skill set.

        Returns:
            Tuple of ``(matching_skills, missing_skills, partial_skills)``.
        """
        profile_skills = set(profile.get("skill_names", []))

        matching: list[str] = []
        missing: list[str] = []
        partial: list[str] = []

        # Match required skills (exact + substring).
        for skill in requirements.required_skills:
            skill_lower = skill.lower()
            if skill_lower in profile_skills:
                matching.append(skill)
            elif self._is_partial_match(skill_lower, profile_skills):
                partial.append(skill)
            else:
                missing.append(skill)

        return matching, missing, partial

    @staticmethod
    def _is_partial_match(skill: str, profile_skills: set[str]) -> bool:
        """Check if *skill* is a partial match against the profile.

        Handles compound skill names: e.g. "machine learning" might appear
        as "ml" in the profile, or "react.js" as "react".
        """
        for ps in profile_skills:
            # Profile skill contains the required skill.
            if skill in ps:
                return True
            # Required skill contains a profile skill.
            if ps in skill:
                return True
        return False

    # ── Step 4: Semantic relevance ─────────────────────────────────────────

    async def _semantic_relevance(
        self,
        text: str,
        profile_id: str | None = None,
    ) -> tuple[float, list[dict[str, Any]]]:
        """Query the vector store for semantically relevant profile chunks.

        Args:
            text: The job description text.
            profile_id: Optional profile ID to scope the search.

        Returns:
            Tuple of ``(relevance_score, top_chunks)`` where *relevance_score*
            is a float 0.0-1.0 and *top_chunks* are the raw VectorStore results.
        """
        try:
            from profile_engine.vector_store import VectorStore
        except ImportError:
            self.logger.warning("VectorStore not available - skipping semantic search")
            return 0.0, []

        store = VectorStore()
        try:
            await store.start()
            results = await store.semantic_search(
                query=text[:2000],
                n_results=5,
                profile_id=profile_id,
            )
            await store.stop()
        except Exception as exc:
            self.logger.warning("Vector store search failed - skipping", error=str(exc))
            return 0.0, []

        if not results:
            return 0.0, []

        # Convert cosine distance to a 0-1 similarity score.
        scores = [max(0.0, 1.0 - r.get("score", 0.0)) for r in results]
        avg_score = statistics.mean(scores) if scores else 0.0

        return avg_score, results

    # ── Step 5: Match score computation ────────────────────────────────────

    def _compute_match_score(
        self,
        matching: list[str],
        missing: list[str],
        partial: list[str],
        relevance_score: float,
    ) -> float:
        """Compute an overall match score (0.0-1.0).

        Formula:
            skill_match = (matching + 0.5 * partial) / max(1, required)
            overall = 0.6 * skill_match + 0.3 * relevance_score + 0.1 * coverage_bonus
        """
        total_required = len(matching) + len(missing) + len(partial)
        if total_required == 0:
            # No explicit requirements → rely on relevance.
            return min(1.0, relevance_score * 1.5)

        skill_match = (len(matching) + 0.5 * len(partial)) / total_required

        # Coverage bonus: having any matching skills at all.
        coverage_bonus = 0.2 if matching else 0.0

        score = 0.6 * skill_match + 0.3 * relevance_score + 0.1 * coverage_bonus
        return max(0.0, min(1.0, score))

    # ── Step 6: Warnings ──────────────────────────────────────────────────

    def _generate_warnings(
        self,
        requirements: ExtractedRequirements,
        profile: dict[str, Any],
        match_score: float,
    ) -> list[str]:
        """Generate human-readable warnings about potential mismatches.

        Args:
            requirements: Extracted job requirements.
            profile: Loaded profile data.
            match_score: Computed match score (0.0-1.0).

        Returns:
            A list of warning strings (may be empty).
        """
        warnings: list[str] = []

        # Low overall match.
        if match_score < 0.3:
            warnings.append("Overall match score is low - consider whether this role is worth pursuing")

        # Experience level mismatch.
        if requirements.years_experience:
            profile_years = profile.get("experience_years", 0.0)
            if profile_years < requirements.years_experience * 0.7:
                warnings.append(
                    f"Profile shows ~{profile_years:.0f} years of experience, "
                    f"but job asks for {requirements.years_experience}+ years"
                )

        # Seniority mismatch.
        if requirements.role_seniority:
            seniority_map = {
                "junior": 0, "mid": 1, "mid-level": 1, "senior": 2,
                "staff": 3, "principal": 4, "lead": 2,
            }
            profile_years = profile.get("experience_years", 0.0)
            expected_seniority = "senior" if profile_years >= 5 else "mid" if profile_years >= 2 else "junior"
            job_level = seniority_map.get(requirements.role_seniority.lower(), 1)
            profile_level = seniority_map.get(expected_seniority, 1)
            if job_level > profile_level + 1:
                warnings.append(
                    f"Role expects '{requirements.role_seniority}' seniority, "
                    f"profile aligns with '{expected_seniority}' level"
                )

        # Many missing skills.
        missing_count = len(set(requirements.required_skills) - set(profile.get("skill_names", [])))
        if missing_count >= 3:
            warnings.append(
                f"Job requires {missing_count} skills not present in profile"
            )

        return warnings

    # ── Batch mode ─────────────────────────────────────────────────────────

    async def analyze_batch(
        self,
        job_descriptions: list[tuple[str, str]],
        profile_id: str | None = None,
    ) -> list[ContextAnalysis]:
        """Analyse multiple job descriptions in sequence.

        Args:
            job_descriptions: A list of ``(job_id, description)`` tuples.
            profile_id: Optional profile UUID.

        Returns:
            A list of :class:`ContextAnalysis` results (same order as input).
        """
        results: list[ContextAnalysis] = []
        for job_id, desc in job_descriptions:
            try:
                analysis = await self.analyze(job_id, desc, profile_id=profile_id)
                results.append(analysis)
            except TailoringError as exc:
                self.logger.warning("Batch analysis failed for job", job_id=job_id, error=str(exc))
                results.append(
                    ContextAnalysis(
                        job_id=job_id,
                        profile_id=profile_id,
                        warnings=[f"Analysis failed: {exc}"],
                    )
                )
        return results
