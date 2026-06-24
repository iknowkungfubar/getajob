"""Personalised recruiter outreach message generation.

Uses the LLM client to craft natural, human-sounding first-contact messages
that reference a specific job application.  Messages are passed through the
:class:`~tailoring_engine.anti_ai_detector.AntiAIDetector` guardrail to
ensure they avoid common LLM tropes.
"""

from __future__ import annotations as _annotations

import datetime
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from core.exceptions import OutreachError
from core.llm_client import get_llm_client
from core.schemas import JobListingRead, OutreachMessageSchema, ProfileRead

__all__: list[str] = [
    "MessageGenerator",
    "OutreachMessage",
]

logger = structlog.get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SYSTEM_PROMPT_PATH = _PROJECT_ROOT / "config" / "prompts" / "outreach_message.txt"


@dataclass
class OutreachMessage:
    """A generated recruiter outreach message.

    Converted to the Pydantic :class:`~core.schemas.OutreachMessageSchema`
    for API serialisation.
    """

    to: str = ""
    subject: str = ""
    body: str = ""
    recruiter_name: str | None = None
    recruiter_title: str | None = None
    recruiter_email: str | None = None
    recruiter_linkedin: str | None = None
    tone: str = "professional"
    platform: str = "email"
    generated_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )

    def to_schema(self, application_id: uuid.UUID | None = None) -> OutreachMessageSchema:
        """Convert to the Pydantic schema for API serialisation."""
        return OutreachMessageSchema(
            application_id=application_id or uuid.UUID(int=0),
            recruiter_name=self.recruiter_name,
            recruiter_email=self.recruiter_email,
            recruiter_title=self.recruiter_title,
            recruiter_linkedin=self.recruiter_linkedin,
            subject=self.subject,
            body=self.body,
            platform=self.platform,
            tone=self.tone,
            generated_at=self.generated_at,
        )


# ── Tone configuration ─────────────────────────────────────────────────────

_TONE_CONFIG: dict[str, dict[str, str]] = {
    "professional": {
        "label": "Professional",
        "instructions": (
            "Write in a professional, polished tone. Use complete sentences, "
            "standard business salutations, and a respectful closing. "
            "Avoid casual language or emoji."
        ),
    },
    "warm": {
        "label": "Warm",
        "instructions": (
            "Write in a warm, approachable tone. Feel free to use conversational "
            "language and a friendly salutation. The message should feel like a "
            "brief email between professional acquaintances."
        ),
    },
    "concise": {
        "label": "Concise",
        "instructions": (
            "Be very brief — no more than 3-4 short sentences. Get straight "
            "to the point. Recruiters are busy and will appreciate brevity. "
            "No salutation or closing needed for this tone."
        ),
    },
}

# ── Message Generator ──────────────────────────────────────────────────────


class MessageGenerator:
    """Generates personalised recruiter outreach messages using the LLM.

    Usage::

        generator = MessageGenerator()
        message = await generator.generate_outreach_message(
            job_listing=job_read_schema,
            profile=profile_read_schema,
            recruiter_name="Jane Smith",
            recruiter_email="jane@company.com",
            tone="warm",
        )
    """

    def __init__(
        self,
        *,
        system_prompt_path: str | Path | None = None,
        llm_client: Any = None,
    ) -> None:
        self._system_prompt_path = Path(system_prompt_path or _DEFAULT_SYSTEM_PROMPT_PATH)
        self._llm = llm_client or get_llm_client()
        self._system_prompt = self._load_system_prompt()
        self.logger = logger.bind(module="message_generator")

    # ── Public API ─────────────────────────────────────────────────────────

    async def generate_outreach_message(
        self,
        job_listing: JobListingRead,
        profile: ProfileRead,
        *,
        recruiter_name: str | None = None,
        recruiter_email: str | None = None,
        recruiter_title: str | None = None,
        recruiter_linkedin: str | None = None,
        tone: str = "professional",
        platform: str = "email",
        company_context: str | None = None,
    ) -> OutreachMessage:
        """Generate a personalised outreach message.

        Args:
            job_listing: The job listing the user applied to.
            profile: The user's profile.
            recruiter_name: Full name of the recruiter (if known).
            recruiter_email: Email address of the recruiter.
            recruiter_title: Recruiter's job title (if known).
            recruiter_linkedin: Recruiter's LinkedIn URL (if known).
            tone: Message tone — ``professional``, ``warm``, or ``concise``.
            platform: Message platform — ``email`` or ``linkedin``.
            company_context: Optional string with specific recent news or
                projects from the company (makes the message more credible).

        Returns:
            A populated :class:`OutreachMessage`.

        Raises:
            OutreachError: If the LLM call fails after all retries.
        """
        tone_config = _TONE_CONFIG.get(tone)
        if tone_config is None:
            msg = f"Unknown tone: {tone!r} (expected: professional, warm, concise)"
            raise OutreachError(msg)

        user_prompt = self._build_prompt(
            job_listing=job_listing,
            profile=profile,
            recruiter_name=recruiter_name,
            recruiter_title=recruiter_title,
            recruiter_linkedin=recruiter_linkedin,
            tone=tone_config,
            platform=platform,
            company_context=company_context,
        )

        self.logger.info(
            "Generating outreach message",
            recruiter_name=recruiter_name,
            tone=tone,
            platform=platform,
            company=job_listing.company,
            role=job_listing.title,
        )

        try:
            result_text = await self._llm.generate_text(
                user_prompt,
                system=self._system_prompt,
                temperature=0.7,
                max_tokens=1024,
            )
        except Exception as exc:
            msg = f"LLM outreach generation failed: {exc}"
            raise OutreachError(msg) from exc

        # Parse the response into subject and body.
        subject, body = self._parse_response(result_text.strip())

        # Apply anti-AI detection guardrail.
        body = self._apply_guardrails(body)

        self.logger.info(
            "Outreach message generated",
            subject=subject,
            body_length=len(body),
        )

        return OutreachMessage(
            to=recruiter_email or "",
            subject=subject,
            body=body,
            recruiter_name=recruiter_name,
            recruiter_title=recruiter_title,
            recruiter_email=recruiter_email,
            recruiter_linkedin=recruiter_linkedin,
            tone=tone,
            platform=platform,
            generated_at=datetime.datetime.now(datetime.UTC),
        )

    # ── Prompt building ────────────────────────────────────────────────────

    def _build_prompt(
        self,
        *,
        job_listing: JobListingRead,
        profile: ProfileRead,
        recruiter_name: str | None,
        recruiter_title: str | None,
        recruiter_linkedin: str | None,
        tone: dict[str, str],
        platform: str,
        company_context: str | None,
    ) -> str:
        """Build the user prompt for the LLM."""
        sections: list[str] = []

        sections.append("## Job Details")
        sections.append(f"Company: {job_listing.company}")
        sections.append(f"Role: {job_listing.title}")
        if job_listing.location:
            sections.append(f"Location: {job_listing.location}")
        sections.append(f"Source: {job_listing.source}")

        sections.append("\n## Applicant Profile")
        sections.append(f"Name: {profile.name}")
        sections.append(f"Current location: {profile.location or 'N/A'}")
        if profile.work_experiences:
            latest = profile.work_experiences[0]
            sections.append(f"Current / most recent role: {latest.title} at {latest.company}")
        if profile.skills:
            top_skills = [s.name for s in profile.skills[:5]]
            sections.append(f"Key skills: {', '.join(top_skills)}")

        sections.append("\n## Recruiter Information")
        sections.append(f"Name: {recruiter_name or 'Unknown'}")
        sections.append(f"Title: {recruiter_title or 'Recruiter / Hiring Manager'}")
        sections.append(f"Platform: {platform}")

        if recruiter_linkedin:
            sections.append(f"LinkedIn: {recruiter_linkedin}")

        if company_context:
            sections.append("\n## Company Context (use this to personalise)")
            sections.append(company_context)

        sections.append("\n## Tone")
        sections.append(tone["instructions"])

        sections.append("\n## Output Format")
        sections.append("Subject: <subject line>")
        sections.append("---")
        sections.append("<message body>")

        return "\n".join(sections)

    @staticmethod
    def _parse_response(text: str) -> tuple[str, str]:
        """Parse the LLM response into subject and body.

        Expects the format::

            Subject: <line>
            ---
            <body text>
        """
        subject = ""
        body = text

        # Try to extract "Subject:" line.
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.lower().startswith("subject:"):
                subject = stripped[len("subject:") :].strip()
                body = text.replace(line, "", 1).strip()
                break

        # Remove the delimiter line if present.
        body = body.replace("---", "", 1).strip()

        # Fallback: if no subject extracted, generate a default.
        if not subject:
            subject = "Application Follow-Up"

        return subject, body

    # ── Guardrails ─────────────────────────────────────────────────────────

    @staticmethod
    def _apply_guardrails(text: str) -> str:
        """Apply anti-AI detection guardrails to the generated message.

        Uses the :class:`~tailoring_engine.anti_ai_detector.AntiAIDetector`
        to scan for and replace telltale LLM phrases.
        """
        try:
            from tailoring_engine.anti_ai_detector import AntiAIDetector
        except ImportError:
            logger.warning("AntiAIDetector not available — skipping guardrail pass")
            return text

        detector = AntiAIDetector(threshold=0.2)
        result = detector.scan_text(text)

        if result.score >= 0.2:
            logger.info(
                "Anti-AI guardrail triggered",
                score=result.score,
                flagged_phrases=result.flagged_phrases,
            )

        # Apply auto-replacements.
        cleaned = text
        for suggestion in result.suggestions:
            if isinstance(suggestion, tuple):
                original, replacement = suggestion
                if replacement:
                    pattern = re.compile(re.escape(original), re.IGNORECASE)
                    cleaned = pattern.sub(replacement, cleaned)

        return cleaned

    # ── Helpers ────────────────────────────────────────────────────────────

    def _load_system_prompt(self) -> str:
        """Load the system prompt from the configured path."""
        try:
            return self._system_prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning(
                "System prompt file not found — using fallback",
                path=str(self._system_prompt_path),
            )
            return "You are an expert recruiter outreach writer. Compose a personalised first-contact message."
