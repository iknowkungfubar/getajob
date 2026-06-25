"""Cover Letter Generator — Tailored cover letters for the GetAJob platform.

Generates personalised cover letters that reference the specific company, role,
and the user's relevant experience.  Enforces anti-AI-detection style rules
throughout.

Key behaviours:
- Company-specific personalisation (research company context if available).
- Anti-AI guardrails enforced (no clichés, varied sentence structure).
- Multiple tone variants: professional (default), conversational, concise.
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

__all__: list[str] = [
    "CoverLetterGenerator",
    "CoverLetterResult",
]

logger = structlog.get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "config" / "prompts"
_DEFAULT_SYSTEM_PROMPT = _PROMPTS_DIR / "cover_letter.txt"

# Phrases that should automatically trigger a rewrite pass.
_CLICHE_BLOCKLIST = {
    "I am writing to express my interest",
    "I am writing to apply",
    "I am excited to submit",
    "I am thrilled to apply",
    "in today's fast-paced",
    "in today's digital landscape",
    "in today's ever-evolving",
    "the attached resume",
    "please find attached",
    "I believe that my skills",
    "I am confident that I",
    "I would welcome the opportunity",
    "thank you for your time and consideration",
    "I look forward to hearing from you",
    "the opportunity to work",
    "I am passionate about",
}


class CoverLetterResult(BaseModel):
    """Result of a cover letter generation operation."""

    cover_letter: str = Field(..., description="Generated cover letter text")
    tone: str = Field(default="professional", description="Tone variant used")
    subject: str = Field(default="", description="Email subject line (if applicable)")

    # Anti-AI guardrail analysis.
    anti_ai: AnalysisResult = Field(
        default_factory=lambda: AnalysisResult(score=0.0, flagged_phrases=[], suggestions=[])
    )

    # Warnings.
    warnings: list[str] = Field(default_factory=list)


class CoverLetterGenerator:
    """Generate personalised cover letters for job applications.

    Usage::

        generator = CoverLetterGenerator()
        result = await generator.generate_cover_letter(
            job_listing=job_data,
            profile=profile_data,
        )
        print(result.cover_letter)
    """

    TONES = ("professional", "conversational", "concise")

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        *,
        anti_ai_detector: AntiAIDetector | None = None,
        system_prompt_path: str | Path | None = None,
    ) -> None:
        self._llm: LLMClient = llm_client or get_llm_client()
        self._anti_ai: AntiAIDetector = anti_ai_detector or AntiAIDetector()

        prompt_path = Path(system_prompt_path) if system_prompt_path else _DEFAULT_SYSTEM_PROMPT
        self._system_prompt: str = ""
        if prompt_path.exists():
            self._system_prompt = prompt_path.read_text(encoding="utf-8")
        else:
            self._system_prompt = self._default_system_prompt()

        self._settings = get_settings()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def generate_cover_letter(
        self,
        job_listing: JobListingRead,
        profile: ProfileRead,
        *,
        tone: str = "professional",
        style_instructions: str | None = None,
        company_context: str | None = None,
    ) -> CoverLetterResult:
        """Generate a tailored cover letter for the given job and profile.

        Args:
            job_listing: The target job's listing data.
            profile: The user's master profile.
            tone: One of ``"professional"``, ``"conversational"``, ``"concise"``.
            style_instructions: Optional free-form instructions for tone or emphasis.
            company_context: Optional info about the company (mission, products, news).

        Returns:
            A :class:`CoverLetterResult` containing the generated letter and
            guardrail analysis.

        Raises:
            TailoringError: If generation fails.
        """
        if tone not in self.TONES:
            msg = f"Unknown tone: {tone!r} (choose from {self.TONES})"
            raise TailoringError(msg)

        # Build the prompt.
        user_prompt = self._build_prompt(
            job_listing, profile, tone, style_instructions, company_context
        )

        # Generate.
        try:
            cover_text = await self._llm.generate_text(
                prompt=user_prompt,
                system=self._system_prompt,
                max_tokens=self._settings.llm.max_tokens,
                temperature=0.7,
            )
        except Exception as exc:
            msg = f"Cover letter generation failed: {exc}"
            raise TailoringError(msg, details={"job_id": str(job_listing.id)}) from exc

        # Post-process: strip clichés.
        cover_text = self._strip_cliches(cover_text)

        # Generate a subject line.
        subject = self._generate_subject(job_listing, tone)

        # Load tailoring config from YAML overlay.
        tailoring_cfg = self._load_tailoring_config()

        # Run anti-AI guardrail.
        anti_ai_result = self._anti_ai.scan_text(cover_text)
        if anti_ai_result.score > tailoring_cfg.get("anti_ai_threshold", 0.3):
            # Apply suggested replacements.
            cover_text = self._apply_anti_ai_fixes(cover_text, anti_ai_result)

        warnings: list[str] = []
        if anti_ai_result.flagged_phrases:
            warnings.append(f"Anti-AI scan flagged {len(anti_ai_result.flagged_phrases)} phrase(s)")

        return CoverLetterResult(
            cover_letter=cover_text.strip(),
            tone=tone,
            subject=subject,
            anti_ai=anti_ai_result,
            warnings=warnings,
        )

    # ── Prompt construction ────────────────────────────────────────────────────

    def _build_prompt(
        self,
        job_listing: JobListingRead,
        profile: ProfileRead,
        tone: str,
        style_instructions: str | None,
        company_context: str | None,
    ) -> str:
        """Build the LLM user prompt."""
        sections: list[str] = []

        # ── Job & company ────────────────────────────────────────────────
        sections.append("=== TARGET JOB ===")
        sections.append(f"Company: {job_listing.company}")
        sections.append(f"Title: {job_listing.title}")
        sections.append(f"Location: {job_listing.location or 'N/A'}")

        if company_context:
            sections.append(f"\nCompany Context:\n{company_context[:1000]}")

        if job_listing.description_json:
            raw = job_listing.description_json.get("raw", "")
            if raw:
                sections.append(f"\nJob Description:\n{raw[:2000]}")

        # ── Profile ──────────────────────────────────────────────────────
        sections.append("\n=== YOUR PROFILE ===")
        sections.append(f"Name: {profile.name}")
        if profile.skills:
            sections.append(f"Key Skills: {', '.join(s.name for s in profile.skills[:10])}")

        if profile.work_experiences:
            sections.append("\nRelevant Experience:")
            for exp in profile.work_experiences[:3]:
                sections.append(f"- {exp.title} at {exp.company}")

        # ── Tone instruction ─────────────────────────────────────────────
        tone_guide = {
            "professional": "Write in a professional, confident tone. "
            "Be direct but courteous.  Use standard paragraph structure.",
            "conversational": "Write in a warm, conversational tone as if writing to a colleague. "
            "Use contractions, varied sentence openings, and natural flow.",
            "concise": "Keep the letter brief — no more than 3 short paragraphs. "
            "Get straight to the point.",
        }

        sections.append(f"\n=== TONE ===\n{tone_guide.get(tone, tone_guide['professional'])}")

        sections.append(
            "\n=== RULES ===\n"
            "- Never use these phrases:\n"
            + "\n".join(f'  ✗ "{p}"' for p in sorted(_CLICHE_BLOCKLIST)[:8])
            + "\n- Start the letter with something specific to the role or company, not a generic intro\n"
            "- Show, don't tell — use specific examples from your experience\n"
            "- Keep it to 3-4 paragraphs maximum\n"
            "- Varie sentence structure and length naturally\n"
            "- Never fabricate experience or qualifications"
        )

        if style_instructions:
            sections.append(f"\nAdditional notes: {style_instructions}")

        return "\n\n".join(sections)

    # ── Post-processing ────────────────────────────────────────────────────────

    def _strip_cliches(self, text: str) -> str:
        """Remove or rewrite common cover letter clichés.

        Args:
            text: The generated cover letter.

        Returns:
            The cleaned text with clichés replaced or removed.
        """
        result = text
        for cliche in sorted(_CLICHE_BLOCKLIST, key=len, reverse=True):
            replacement = self._cliches_replacement(cliche)
            if cliche in result:
                result = result.replace(cliche, replacement)
        return result

    @staticmethod
    def _cliches_replacement(phrase: str) -> str:
        """Return a replacement for a cliché phrase."""
        mapping: dict[str, str] = {
            "I am writing to express my interest": "I am interested",
            "I am writing to apply": "I am applying",
            "I am excited to submit": "I welcome the chance",
            "I am thrilled to apply": "I welcome the chance",
            "in today's fast-paced": "in the current",
            "in today's digital landscape": "in technology",
            "in today's ever-evolving": "in the evolving",
            "the attached resume": "my resume",
            "please find attached": "I have attached",
            "I believe that my skills": "My skills",
            "I am confident that I": "I am",
            "I would welcome the opportunity": "I would welcome",
            "thank you for your time and consideration": "Thank you for your consideration",
        }
        return mapping.get(phrase, phrase)

    @staticmethod
    def _generate_subject(job_listing: JobListingRead, tone: str) -> str:
        """Generate an email subject line for the cover letter.

        Args:
            job_listing: The job listing data.
            tone: The tone variant.

        Returns:
            A subject line string.
        """
        company = job_listing.company
        title = job_listing.title
        if tone == "concise":
            return f"Application — {title}, {company}"
        return f"Application for {title} at {company}"

    # ── Anti-AI fix application ────────────────────────────────────────────────

    def _apply_anti_ai_fixes(self, text: str, analysis: AnalysisResult) -> str:
        """Apply suggested replacements from the anti-AI detector."""
        if not analysis.suggestions:
            return text

        fixed = text
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
        return (
            "You are an expert cover letter writer.  Your task is to write "
            "personalised, human-sounding cover letters for job applications.\n\n"
            "CRITICAL RULES:\n"
            "- Never use the phrase 'I am writing to express my interest'\n"
            "- Never use 'in today's fast-paced [anything]'\n"
            "- Start with a specific, engaging opening — not a template\n"
            "- Use varied sentence structure and natural paragraph breaks\n"
            "- Reference the specific company and role naturally\n"
            "- Keep the reader's attention: be specific, not generic\n"
            "- Never fabricate experience or qualifications"
        )
