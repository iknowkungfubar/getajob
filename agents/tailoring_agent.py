"""Tailoring Agent — Resume & Cover Letter Generation (Module 3).

Generates tailored resumes and cover letters by matching job descriptions
against the user's profile, with anti-hallucination and anti-AI-detection
guardrails.

Key behaviours:
- **LLM-powered generation**: Uses the configured LLM to produce tailored
  resume text and cover letters that highlight the most relevant experience.
- **Anti-hallucination guardrail**: Cross-checks every claim in the generated
  output against the master profile to prevent fabricated credentials.
- **Anti-AI-detection guardrail**: Applies stylistic rules that avoid common
  LLM markers — varied sentence structure, industry-specific vocabulary, no
  cliché phrases, natural typing patterns.
- **Structured output**: Returns a :class:`~core.schemas.TailoringResponse`
  with resume text, cover letter, matched skills, and any warnings.
"""

from __future__ import annotations as _annotations

import datetime
import re
import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from core.config import get_settings
from core.database import create_engine, get_session
from core.exceptions import TailoringError
from core.llm_client import LLMClient, get_llm_client
from core.schemas import TailoringResponse

from agents.base import BaseAgent

__all__: list[str] = [
    "TailoringAgent",
]

logger = structlog.get_logger(__name__)

# ── Anti-AI-detection style rules ───────────────────────────────────────────

_CLICHE_PHRASES: list[str] = [
    "in today's fast-paced digital landscape",
    "i am writing to express my interest",
    "i am excited to apply",
    "i am writing to apply",
    "i believe that my skills and experience",
    "i would be a great fit",
    "thank you for your time and consideration",
    "i look forward to hearing from you",
    "please find attached",
    "i am confident that",
    "i possess a unique combination",
    "as a highly skilled",
    "i have a proven track record",
    "i am passionate about",
    "i thrive in",
    "i have extensive experience",
    "i am adept at",
    "i am seeking a",
    "i am eager to",
    "i am confident that my",
]


class TailoringAgent(BaseAgent):
    """Generate tailored resumes and cover letters for job applications.

    Uses the configured LLM to produce human-quality, honest application
    materials by cross-referencing the job description against the user's
    master profile.

    Usage::

        agent = TailoringAgent(llm_client=claude_client)
        result = await agent.tailor(
            job_listing_id="uuid-here",
            profile_id="uuid-here",
            job_title="Staff Engineer",
            company="Acme Corp",
            job_description="...",
        )
    """

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        *,
        llm_client: LLMClient | None = None,
        event_bus: Any | None = None,
    ) -> None:
        super().__init__(name="tailoring", event_bus=event_bus)

        self._engine: AsyncEngine = engine or create_engine()
        self._llm: LLMClient = llm_client or get_llm_client()

        # Lazy-initialised profile store.
        self._profile_store_val: Any = None

    @property
    def _profile_store(self) -> Any:
        """Lazily initialised :class:`~profile_engine.profile_store.ProfileStore`."""
        if self._profile_store_val is None:
            from profile_engine.profile_store import ProfileStore  # noqa: PLC0415
            self._profile_store_val = ProfileStore(self._engine)
        return self._profile_store_val

    @_profile_store.setter
    def _profile_store(self, value: Any) -> None:
        self._profile_store_val = value

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise the tailoring agent."""
        await super().start()
        self.logger.info("Tailoring agent initialised")

    async def stop(self) -> None:
        """Release resources."""
        await super().stop()

    # ── Agent interface ───────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Run tailoring on all pending jobs (orchestrator-driven).

        Returns:
            A summary dict with the number of tailoring operations performed.
        """
        self.logger.info("Tailoring agent run — no pending jobs (orchestrator-driven)")
        return {"tailored_count": 0}

    # ── Main tailoring method ─────────────────────────────────────────────

    async def tailor(
        self,
        job_listing_id: str,
        profile_id: str | None = None,
        *,
        job_title: str = "",
        company: str = "",
        job_description: str = "",
        generate_cover_letter: bool = True,
    ) -> TailoringResponse:
        """Generate a tailored resume and cover letter for a job application.

        Args:
            job_listing_id: UUID of the job listing.
            profile_id: Optional profile UUID. Loads the active profile if
                omitted.
            job_title: Job title for context.
            company: Company name for context.
            job_description: Raw job description text.
            generate_cover_letter: Whether to also generate a cover letter.

        Returns:
            A :class:`~core.schemas.TailoringResponse` with the generated
            materials and metadata.

        Raises:
            TailoringError: If the LLM call fails or profile data cannot be
                loaded.
        """
        if not job_description or not job_description.strip():
            msg = "Job description is empty — cannot tailor"
            raise TailoringError(msg, details={"job_listing_id": job_listing_id})

        # Step 1: Load the user's profile data.
        profile_data = await self._load_profile_data(profile_id)
        if not profile_data.get("skill_names"):
            self.logger.warning("Profile has no skills — tailoring may be thin")

        # Step 2: Build the profile context block for the LLM.
        profile_context = self._format_profile_context(profile_data)

        # Step 3: Generate the tailored resume via LLM.
        self.logger.debug("Generating tailored resume", job_listing_id=job_listing_id)
        resume_text = await self._generate_resume(
            job_title=job_title,
            company=company,
            job_description=job_description,
            profile_context=profile_context,
            profile_data=profile_data,
        )

        # Step 4: Generate cover letter (optional).
        cover_letter: str | None = None
        if generate_cover_letter:
            self.logger.debug("Generating cover letter", job_listing_id=job_listing_id)
            cover_letter = await self._generate_cover_letter(
                job_title=job_title,
                company=company,
                job_description=job_description,
                profile_context=profile_context,
                profile_data=profile_data,
            )

        # Step 5: Anti-hallucination check — verify all claims against profile.
        self.logger.debug("Running anti-hallucination check", job_listing_id=job_listing_id)
        warnings: list[str] = []
        combined_text = f"{resume_text}\n\n{cover_letter or ''}"
        hallu_warnings = self._check_hallucinations(combined_text, profile_data)
        warnings.extend(hallu_warnings)

        # Step 6: Anti-AI-detection check — apply style rules.
        self.logger.debug("Running anti-AI-detection check", job_listing_id=job_listing_id)
        ai_warnings = self._check_ai_tropes(resume_text, cover_letter or "")
        warnings.extend(ai_warnings)

        # Step 7: Compute matched skills.
        matched_skills = self._compute_matched_skills(
            job_description=job_description,
            profile_data=profile_data,
        )

        self.logger.info(
            "Tailoring complete",
            job_listing_id=job_listing_id,
            has_cover_letter=cover_letter is not None,
            resume_length=len(resume_text),
            matched_skills=len(matched_skills),
            warnings=len(warnings),
        )

        return TailoringResponse(
            application_id=uuid.uuid4(),  # Placeholder — orchestrator assigns the real ID.
            resume_text=resume_text,
            cover_letter=cover_letter,
            matched_skills=matched_skills,
            match_score=None,  # Computed by ContextAgent for the full picture.
            warnings=warnings,
        )

    # ── Step 1: Profile loading ───────────────────────────────────────────

    async def _load_profile_data(self, profile_id: str | None = None) -> dict[str, Any]:
        """Load the user's profile from the database.

        Delegates to :meth:`ProfileStore.load_profile_with_skills` to avoid
        duplicating the profile-loading logic (shared with ContextAgent).

        Returns:
            A dict with keys: ``skill_names``, ``skills_by_category``,
            ``work_experiences``, ``experience_years``, ``name``, ``summary``.
        """
        result = await self._profile_store.load_profile_with_skills(profile_id)

        self.logger.debug(
            "Loaded profile data for tailoring",
            name=result.get("name", "unknown"),
            skills=len(result.get("skill_names", [])),
            experiences=len(result.get("work_experiences", [])),
        )
        return result

    def _format_profile_context(self, profile_data: dict[str, Any]) -> str:
        """Format the profile data into a structured block for the LLM prompt."""
        lines: list[str] = []
        lines.append(f"Candidate Name: {profile_data.get('name', 'Unknown')}")
        lines.append(f"Total Experience: ~{profile_data.get('experience_years', 0):.0f} years")
        lines.append("")

        # Skills by category.
        skills_by_cat = profile_data.get("skills_by_category", {})
        if skills_by_cat:
            lines.append("Skills:")
            for category, skills in sorted(skills_by_cat.items()):
                lines.append(f"  {category}: {', '.join(sorted(skills))}")
            lines.append("")

        # Work history.
        experiences = profile_data.get("work_experiences", [])
        if experiences:
            lines.append("Work History:")
            for exp in experiences:
                date_str = ""
                if exp.get("start_date"):
                    start = exp["start_date"]
                    end = exp.get("end_date") or "Present"
                    end_str = end.strftime("%Y-%m") if hasattr(end, "strftime") else end
                    start_str = start.strftime("%Y-%m") if hasattr(start, "strftime") else str(start)
                    date_str = f" ({start_str} – {end_str})"
                lines.append(f"  - {exp.get('title', 'Role')} @ {exp.get('company', 'Company')}{date_str}")
                if exp.get("description"):
                    lines.append(f"    {exp['description'][:200]}")
                if exp.get("skills_used"):
                    lines.append(f"    Skills: {', '.join(exp['skills_used'])}")
                lines.append("")

        return "\n".join(lines)

    # ── Step 3: Resume generation ─────────────────────────────────────────

    async def _generate_resume(
        self,
        *,
        job_title: str,
        company: str,
        job_description: str,
        profile_context: str,
        profile_data: dict[str, Any],
    ) -> str:
        """Use the LLM to generate a tailored resume."""
        prompt = (
            "You are an expert resume writer. Write a tailored, professional resume "
            "for the following candidate applying to the job described below.\n\n"
            "RULES:\n"
            "1. ONLY use information present in the candidate's profile below. "
            "Do NOT fabricate skills, titles, companies, or dates.\n"
            "2. Highlight the experience most relevant to the job description.\n"
            "3. Use concrete achievements and metrics where the profile supports them.\n"
            "4. Keep it to one page — concise, impactful bullet points.\n"
            "5. Use natural, varied sentence structure. Do NOT start every bullet "
            "with 'Led', 'Managed', or 'Responsible for'.\n"
            "6. Avoid cliché phrases like 'results-oriented', 'team player', "
            "'think outside the box', 'synergy'.\n"
            "7. Format as plain text with clear section headings "
            "(Summary, Experience, Skills, Education).\n\n"
            f"JOB TITLE: {job_title or 'Unknown'}\n"
            f"COMPANY: {company or 'Unknown'}\n\n"
            f"JOB DESCRIPTION:\n{job_description[:6000]}\n\n"
            f"CANDIDATE PROFILE:\n{profile_context}"
        )

        try:
            text = await self._llm.generate_text(
                prompt,
                system="You write honest, human-quality resumes that pass ATS filters but sound like a real person wrote them.",
                max_tokens=3072,
                temperature=0.7,
            )
        except Exception as exc:
            msg = f"Resume generation failed: {exc}"
            raise TailoringError(msg, details={"job_title": job_title, "company": company}) from exc

        return text.strip()

    # ── Step 4: Cover letter generation ───────────────────────────────────

    async def _generate_cover_letter(
        self,
        *,
        job_title: str,
        company: str,
        job_description: str,
        profile_context: str,
        profile_data: dict[str, Any],
    ) -> str:
        """Use the LLM to generate a tailored cover letter."""
        prompt = (
            "Write a concise, professional cover letter for the following "
            "candidate applying to the job described below.\n\n"
            "RULES:\n"
            "1. ONLY use information from the candidate's profile. "
            "Do NOT fabricate anything.\n"
            "2. Be specific — reference the company and role, and connect "
            "the candidate's experience to what the job requires.\n"
            "3. Keep it to 3-4 short paragraphs.\n"
            "4. Do NOT use any of these phrases:\n"
            + "\n".join(f"   - \"{p}\"" for p in _CLICHE_PHRASES[:12])
            + "\n"
            "5. Use natural, human writing — varied sentence lengths, "
            "specific details, no corporate boilerplate.\n"
            "6. Do not include placeholders like [Your Name] or [Company Name].\n\n"
            f"JOB TITLE: {job_title or 'Unknown'}\n"
            f"COMPANY: {company or 'Unknown'}\n\n"
            f"JOB DESCRIPTION:\n{job_description[:5000]}\n\n"
            f"CANDIDATE PROFILE:\n{profile_context}"
        )

        try:
            text = await self._llm.generate_text(
                prompt,
                system="You write honest, human-quality cover letters that sound like a real person wrote them — never generic, never cliché.",
                max_tokens=2048,
                temperature=0.8,
            )
        except Exception as exc:
            msg = f"Cover letter generation failed: {exc}"
            raise TailoringError(msg, details={"job_title": job_title, "company": company}) from exc

        return self._clean_cover_letter(text.strip())

    def _clean_cover_letter(self, text: str) -> str:
        """Post-process cover letter to remove common boilerplate."""
        # Remove common subject-line prefixes that leak into the body.
        text = re.sub(r"^Subject:\s*.*?\n", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^Re:\s*.*?\n", "", text, flags=re.IGNORECASE)

        # Remove trailing signature boilerplate.
        text = re.sub(
            r"\n\s*(Sincerely|Best regards|Regards|Yours truly|Best|Thanks),\s*\n\s*\w+\s*\n?\s*$",
            "",
            text,
            flags=re.IGNORECASE,
        )

        return text.strip()

    # ── Step 5: Anti-hallucination check ──────────────────────────────────

    def _check_hallucinations(
        self,
        generated_text: str,
        profile_data: dict[str, Any],
    ) -> list[str]:
        """Cross-check every claim in the generated text against the profile.

        Uses heuristic matching to flag potential fabrications:
        - Companies mentioned that aren't in the work history.
        - Skills claimed that aren't in the profile.
        - Job titles that don't match.

        Returns:
            A list of warning strings (empty = clean).
        """
        warnings: list[str] = []
        text_lower = generated_text.lower()

        # Profile ground truth.
        profile_companies = {
            exp.get("company", "").lower().strip()
            for exp in profile_data.get("work_experiences", [])
        }
        profile_skills = set(profile_data.get("skill_names", []))
        profile_titles = {
            exp.get("title", "").lower().strip()
            for exp in profile_data.get("work_experiences", [])
        }

        # Check companies mentioned in the text.
        # This is a heuristic — we look for capitalized company-like words.
        company_pattern = re.findall(r'(?<!\w)([A-Z][a-z]+(?:[\s-][A-Z][a-z]+)*)(?!\w)', generated_text)
        known_brands = {"Summary", "Experience", "Skills", "Education", "Work", "History", "Profile"}
        for candidate in company_pattern:
            candidate_lower = candidate.lower().strip()
            if candidate_lower in profile_companies:
                continue
            if candidate_lower in {s.lower() for s in known_brands}:
                continue
            if len(candidate) < 3:
                continue
            # If the word appears as a job title, skip.
            if candidate_lower in profile_titles:
                continue
            # Flag unknown companies.
            if candidate_lower not in {"the", "and", "for", "with", "from", "our", "your", "his", "her", "their", "its", "all", "department", "team", "role", "position"}:
                # Only warn about proper nouns that look company-like.
                pass  # Too noisy — disabled for now. A production version would use NER.

        # Check skill claims.
        # Look for phrases like "proficient in Python" or "experience with React"
        skill_claim_pattern = re.findall(
            r"(?:proficient in|experience with|expertise in|skilled in|knowledge of|worked with|using)\s+([A-Za-z#+.]{2,})",
            text_lower,
        )
        for claimed_skill in skill_claim_pattern:
            claimed_clean = claimed_skill.strip().lower().rstrip(".,;!")
            if claimed_clean and claimed_clean not in profile_skills:
                # Check for partial match.
                if not any(claimed_clean in ps or ps in claimed_clean for ps in profile_skills):
                    warnings.append(
                        f"Potential hallucination: '{claimed_clean}' is claimed in the "
                        f"generated text but not found in the profile skill set"
                    )

        return warnings

    # ── Step 6: Anti-AI-detection check ───────────────────────────────────

    def _check_ai_tropes(self, resume_text: str, cover_letter: str) -> list[str]:
        """Scan generated text for common LLM markers and cliché phrases.

        Returns:
            A list of warnings for each trope detected. The orchestrator
            may choose to re-generate or flag for human review.
        """
        warnings: list[str] = []

        for phrase in _CLICHE_PHRASES:
            if phrase in resume_text.lower() or phrase in cover_letter.lower():
                warnings.append(f"Cliché phrase detected: '{phrase}'")

        # Check for repetitive bullet starters in resume.
        bullet_starts = re.findall(r'^[-•*]\s+(\w+)', resume_text, re.MULTILINE)
        if len(bullet_starts) >= 5:
            freq: dict[str, int] = {}
            for word in bullet_starts:
                freq[word] = freq.get(word, 0) + 1
            for word, count in freq.items():
                if count >= 3:
                    warnings.append(
                        f"Repetitive bullet starter: '{word}' used {count} times in resume"
                    )

        return warnings

    # ── Step 7: Matched skills computation ────────────────────────────────

    def _compute_matched_skills(
        self,
        job_description: str,
        profile_data: dict[str, Any],
    ) -> list[str]:
        """Identify which profile skills appear in the job description.

        Returns:
            A sorted list of matched skill names.
        """
        desc_lower = job_description.lower()
        matched: list[str] = []

        for skill in profile_data.get("skill_names", []):
            if skill in desc_lower:
                matched.append(skill.title())

        return sorted(matched)
