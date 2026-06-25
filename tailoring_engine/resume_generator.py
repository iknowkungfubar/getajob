"""Resume Generator - Tailored resume generation for the GetAJob platform.

Uses the configured LLM to rewrite profile experience, highlighting the skills
and achievements most relevant to a specific job listing.  Every generated
claim is subsequently validated by :class:`~tailoring_engine.truth_validator.TruthValidator`
to ensure nothing is fabricated.

Key behaviours:
- Builds a customised prompt from the job listing, profile data, and
  context analysis.
- Supports multiple output formats: chronological, hybrid (default).
- Outputs ATS-friendly plain-text markdown.
- Enforces anti-AI-detection style rules via the ``system`` prompt.
"""

from __future__ import annotations as _annotations

from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

from core.config import get_settings, load_config
from core.exceptions import TailoringError
from core.llm_client import LLMClient, get_llm_client
from core.schemas import JobListingRead, ProfileRead
from tailoring_engine.anti_ai_detector import AnalysisResult, AntiAIDetector
from tailoring_engine.truth_validator import TruthValidator, ValidationResult

__all__: list[str] = [
    "ResumeGenerator",
    "ResumeResult",
]

logger = structlog.get_logger(__name__)

# Default prompt template path.
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "config" / "prompts"
_DEFAULT_SYSTEM_PROMPT = _PROMPTS_DIR / "resume_tailoring.txt"


class ResumeResult(BaseModel):
    """Result of a resume generation operation."""

    resume_text: str = Field(..., description="Generated resume text (markdown / plain text)")
    format_used: str = Field(default="hybrid", description="Resume format: chronological, hybrid, functional")

    # Anti-AI guardrail analysis.
    anti_ai: AnalysisResult = Field(default_factory=lambda: AnalysisResult(score=0.0, flagged_phrases=[], suggestions=[]))

    # Truth validation.
    validation: ValidationResult = Field(
        default_factory=lambda: ValidationResult(
            all_claims_verified=True,
            unverifiable_claims=[],
            hallucinations=[],
            hallucination_score=0.0,
            passed=True,
        )
    )

    # Metadata.
    llm_prompt_tokens: int | None = None
    llm_completion_tokens: int | None = None
    warnings: list[str] = Field(default_factory=list)


class ResumeGenerator:
    """Generate tailored resumes by rewriting profile experience for a specific job.

    Usage::

        generator = ResumeGenerator()
        result = await generator.generate_resume(
            job_listing=job_data,
            profile=profile_data,
            context_analysis=analysis,
        )
        print(result.resume_text)
    """

    FORMATS = ("chronological", "hybrid", "functional")

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        *,
        anti_ai_detector: AntiAIDetector | None = None,
        truth_validator: TruthValidator | None = None,
        system_prompt_path: str | Path | None = None,
    ) -> None:
        self._llm: LLMClient = llm_client or get_llm_client()
        self._anti_ai: AntiAIDetector = anti_ai_detector or AntiAIDetector()
        self._truth: TruthValidator = truth_validator or TruthValidator()

        # Load system prompt.
        prompt_path = Path(system_prompt_path) if system_prompt_path else _DEFAULT_SYSTEM_PROMPT
        self._system_prompt: str = ""
        if prompt_path.exists():
            self._system_prompt = prompt_path.read_text(encoding="utf-8")
        else:
            self._system_prompt = self._default_system_prompt()
            logger.warning("System prompt file not found - using built-in default", path=str(prompt_path))

        self._settings = get_settings()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def generate_resume(
        self,
        job_listing: JobListingRead,
        profile: ProfileRead,
        context_analysis: dict[str, Any] | None = None,
        *,
        format_style: str = "hybrid",
        style_instructions: str | None = None,
    ) -> ResumeResult:
        """Generate a tailored resume for the given job and profile.

        Args:
            job_listing: The target job's listing data.
            profile: The user's master profile.
            context_analysis: Optional output from :class:`~agents.context_agent.ContextAgent`
                containing match scores and skill gaps.
            format_style: One of ``"chronological"``, ``"hybrid"``, ``"functional"``.
            style_instructions: Optional free-form instructions for tone or emphasis.

        Returns:
            A :class:`ResumeResult` containing the generated text and all
            guardrail results.

        Raises:
            TailoringError: If generation fails or the output fails validation.
        """
        if format_style not in self.FORMATS:
            msg = f"Unknown resume format: {format_style!r} (choose from {self.FORMATS})"
            raise TailoringError(msg)

        # Build the user prompt.
        user_prompt = self._build_prompt(job_listing, profile, context_analysis, format_style, style_instructions)

        # Generate.
        try:
            resume_text = await self._llm.generate_text(
                prompt=user_prompt,
                system=self._system_prompt,
                max_tokens=self._settings.llm.max_tokens,
                temperature=0.5,  # Lower temperature for factual accuracy.
            )
        except Exception as exc:
            msg = f"Resume generation failed: {exc}"
            raise TailoringError(msg, details={"job_id": str(job_listing.id)}) from exc

        # Load tailoring config from YAML overlay.
        tailoring_cfg = self._load_tailoring_config()

        # Run anti-AI guardrail.
        anti_ai_result = self._anti_ai.scan_text(resume_text)
        if anti_ai_result.score > tailoring_cfg.get("anti_ai_threshold", 0.3) and anti_ai_result.suggestions:
            # Apply suggested replacements.
            resume_text = self._apply_anti_ai_fixes(resume_text, anti_ai_result)

        # Run truth validation.
        validation_result = self._truth.validate_claims(resume_text, profile)

        warnings: list[str] = []
        if not validation_result.passed:
            warnings.append(
                f"Truth validation found {len(validation_result.hallucinations)} hallucination(s) "
                f"and {len(validation_result.unverifiable_claims)} unverifiable claim(s)"
            )

        return ResumeResult(
            resume_text=resume_text,
            format_used=format_style,
            anti_ai=anti_ai_result,
            validation=validation_result,
            warnings=warnings,
        )

    # ── Prompt construction ────────────────────────────────────────────────────

    def _build_prompt(
        self,
        job_listing: JobListingRead,
        profile: ProfileRead,
        context_analysis: dict[str, Any] | None,
        format_style: str,
        style_instructions: str | None,
    ) -> str:
        """Build the user prompt sent to the LLM.

        Args:
            job_listing: The job listing data.
            profile: The user profile.
            context_analysis: Optional context analysis.
            format_style: Resume format requested.
            style_instructions: Optional style overrides.

        Returns:
            A formatted prompt string.
        """
        sections: list[str] = []

        # ── Job description ──────────────────────────────────────────────
        sections.append("=== TARGET JOB ===")
        sections.append(f"Company: {job_listing.company}")
        sections.append(f"Title: {job_listing.title}")
        sections.append(f"Location: {job_listing.location or 'N/A'}")
        if job_listing.required_skills:
            sections.append(f"Required Skills: {', '.join(job_listing.required_skills)}")
        if job_listing.description_json:
            raw = job_listing.description_json.get("raw", "")
            if raw:
                # Trim to avoid token overflow.
                sections.append(f"Description:\n{raw[:3000]}")

        # ── Context analysis ─────────────────────────────────────────────
        if context_analysis:
            sections.append("=== MATCH ANALYSIS ===")
            sections.append(f"Match Score: {context_analysis.get('match_score', 'N/A')}")
            matching = context_analysis.get("matching_skills", [])
            missing = context_analysis.get("missing_skills", [])
            if matching:
                sections.append(f"Strongest matching skills: {', '.join(matching[:8])}")
            if missing:
                sections.append(f"Skill gaps (de-emphasise): {', '.join(missing[:5])}")

        # ── Profile data ─────────────────────────────────────────────────
        sections.append("=== PROFILE ===")
        sections.append(f"Name: {profile.name}")
        sections.append(f"Location: {profile.location or 'N/A'}")
        sections.append(f"Work Authorization: {profile.work_authorization or 'N/A'}")

        if profile.skills:
            skills_text = ", ".join(s.name for s in profile.skills)
            sections.append(f"Skills: {skills_text}")

        if profile.work_experiences:
            sections.append("\n=== WORK EXPERIENCE ===")
            for exp in profile.work_experiences:
                dates = ""
                if exp.start_date:
                    dates = f"{exp.start_date.year} - {exp.end_date.year if exp.end_date else 'Present'}"
                sections.append(f"\n## {exp.title} at {exp.company}  ({dates})")
                if exp.description:
                    sections.append(exp.description[:500])
                if exp.skills_used:
                    sections.append(f"Skills: {', '.join(exp.skills_used)}")

        # ── Output instruction ───────────────────────────────────────────
        sections.append("\n=== INSTRUCTIONS ===")
        sections.append(f"Generate a {format_style}-style resume targeting this specific job.")
        sections.append(
            "IMPORTANT: Never fabricate experience, titles, dates, or metrics. "
            "Only use information present in the PROFILE section above."
        )
        if style_instructions:
            sections.append(f"Style notes: {style_instructions}")

        return "\n\n".join(sections)

    # ── Format-specific helpers ────────────────────────────────────────────────

    async def generate_chronological(self, job_listing: JobListingRead, profile: ProfileRead) -> ResumeResult:
        """Convenience wrapper for chronological-format resume."""
        return await self.generate_resume(job_listing, profile, format_style="chronological")

    async def generate_functional(self, job_listing: JobListingRead, profile: ProfileRead) -> ResumeResult:
        """Convenience wrapper for functional-format resume."""
        return await self.generate_resume(job_listing, profile, format_style="functional")

    # ── Anti-AI fix application ────────────────────────────────────────────────

    def _apply_anti_ai_fixes(self, text: str, analysis: AnalysisResult) -> str:
        """Apply suggested replacements from the anti-AI detector.

        Args:
            text: The generated resume text.
            analysis: The analysis result with flagged phrases and suggestions.

        Returns:
            The cleaned text with replacements applied.
        """
        if not analysis.suggestions:
            return text

        fixed = text
        # suggestions are tuples of (original_phrase, replacement) or just strings.
        for suggestion in analysis.suggestions:
            if isinstance(suggestion, tuple) and len(suggestion) == 2:
                original, replacement = suggestion
                if original in fixed:
                    fixed = fixed.replace(original, replacement)

        return fixed

    # ── Config loading ─────────────────────────────────────────────────────────

    @staticmethod
    def _load_tailoring_config() -> dict[str, Any]:
        """Load the ``tailoring`` section from the YAML config overlay.

        Returns:
            A dict with tailoring defaults (empty dict if not configured).
        """
        overlay = load_config()
        return overlay.get("tailoring", {})

    # ── Default system prompt (fallback) ────────────────────────────────────────

    def _default_system_prompt(self) -> str:
        """Return a hardcoded system prompt when the file is missing."""
        return (
            "You are an expert resume writer specialised in ATS-optimised, "
            "honest resume tailoring.  Your task is to rewrite the user's "
            "profile to highlight the experience most relevant to the target "
            "job, using only information present in the provided profile.\n\n"
            "STYLE RULES:\n"
            "- Write in plain text / markdown (no HTML, no PDF formatting)\n"
            "- Use strong action verbs (led, designed, built, optimised)\n"
            "- Quantify achievements where the profile provides data\n"
            "- Keep bullet points to 1-2 lines each\n"
            "- Avoid clichés: 'passionate about', 'team player', 'think outside the box'\n"
            "- Use ATS-friendly section headings: Summary, Skills, Experience, Education\n"
            "- Never fabricate or embellish experience, titles, dates, or metrics\n"
            "- Do not include an 'References available upon request' section"
        )
