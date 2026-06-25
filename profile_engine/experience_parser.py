"""Experience and skill parser for the Profile Engine.

Extracts structured information from free-form text: skill mentions, role
titles, date ranges, and responsibilities.  The parser combines regex patterns
with lightweight NLP heuristics and can optionally delegate to an LLM for
ambiguous cases.
"""

from __future__ import annotations as _annotations

import datetime
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import structlog

from core.exceptions import ProfileError
from core.llm_client import LLMClient
from core.schemas import SkillSchema, WorkExperienceSchema

__all__: list[str] = [
    "ExperienceParser",
]

logger = structlog.get_logger(__name__)

# ── Built-in skill lexicon (extensible) ──────────────────────────────────────────

_KNOWN_SKILLS: dict[str, str] = {
    # Languages
    "python": "language",
    "rust": "language",
    "typescript": "language",
    "javascript": "language",
    "go": "language",
    "java": "language",
    "c++": "language",
    "c#": "language",
    "kotlin": "language",
    "swift": "language",
    "scala": "language",
    "ruby": "language",
    "elixir": "language",
    "haskell": "language",
    "sql": "language",
    "graphql": "language",
    "bash": "language",
    "shell": "language",
    # Frameworks & runtimes
    "django": "framework",
    "fastapi": "framework",
    "flask": "framework",
    "spring": "framework",
    "spring boot": "framework",
    "react": "framework",
    "angular": "framework",
    "vue": "framework",
    "next.js": "framework",
    "node.js": "framework",
    "express": "framework",
    "rails": "framework",
    "laravel": "framework",
    "asp.net": "framework",
    "pytorch": "framework",
    "tensorflow": "framework",
    "langchain": "framework",
    "llamaindex": "framework",
    # Databases
    "postgresql": "database",
    "postgres": "database",
    "mysql": "database",
    "mongodb": "database",
    "redis": "database",
    "elasticsearch": "database",
    "dynamodb": "database",
    "cassandra": "database",
    "clickhouse": "database",
    "bigquery": "database",
    "snowflake": "database",
    # Cloud & infra
    "aws": "cloud",
    "gcp": "cloud",
    "azure": "cloud",
    "kubernetes": "cloud",
    "docker": "cloud",
    "terraform": "cloud",
    "ansible": "cloud",
    "helm": "cloud",
    "prometheus": "cloud",
    "grafana": "cloud",
    "istio": "cloud",
    "envoy": "cloud",
    # Tools
    "git": "tool",
    "linux": "tool",
    "ci/cd": "tool",
    "jenkins": "tool",
    "github actions": "tool",
    "gitlab ci": "tool",
    "kafka": "tool",
    "rabbitmq": "tool",
    "nginx": "tool",
    # Concepts
    "distributed systems": "concept",
    "microservices": "concept",
    "rest api": "concept",
    "rest": "concept",
    "grpc": "concept",
    "event-driven": "concept",
    "machine learning": "concept",
    "deep learning": "concept",
    "nlp": "concept",
    "computer vision": "concept",
    "agile": "concept",
    "scrum": "concept",
    "tdd": "concept",
    "ci": "concept",
}

# ── Pre-compiled skill patterns ──────────────────────────────────────────────────

_SKILL_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"\b" + re.escape(s) + r"\b", re.IGNORECASE), s, _KNOWN_SKILLS[s])
    for s in sorted(_KNOWN_SKILLS, key=len, reverse=True)
]

# ── Date patterns ────────────────────────────────────────────────────────────────

_DATE_PATTERNS = [
    re.compile(r"(?P<start>(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4})"
               r"\s*[---to]+\s*"
               r"(?P<end>(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}|Present|Current|Now)",
               re.IGNORECASE),
    re.compile(r"(?P<start>\d{4})\s*[---]\s*(?P<end>\d{4}|Present|Current|Now)"),
]


def _parse_date(text: str) -> datetime.date | None:
    """Try to parse a free-form date string (e.g. ``"January 2020"``) into a date.

    Returns the first-of-month for month-level precision, or ``None`` if
    parsing fails.
    """
    text = text.strip()
    if not text:
        return None
    if text.lower() in {"present", "current", "now"}:
        return datetime.date.today()

    # Try month-name patterns.
    for fmt in ("%B %Y", "%b %Y", "%B %y", "%b %y"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            pass

    # Try year-only.
    match = re.match(r"^(\d{4})$", text)
    if match:
        return datetime.date(int(match.group(1)), 1, 1)

    return None


# ── Parser class ─────────────────────────────────────────────────────────────────


class ExperienceParser:
    """Parse free-form text into structured skills and work history.

    Usage::

        parser = ExperienceParser()
        skills = parser.extract_skills_from_text("5 years Python, Rust, Kubernetes")
        experiences = parser.parse_resume_text(\"""
            Senior Software Engineer, Acme Corp
            Jan 2020 - Present
            Built distributed systems with Rust and Python.
        \""")
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm: LLMClient | None = llm_client

    # ── Skill extraction ────────────────────────────────────────────────────

    def extract_skills_from_text(
        self,
        text: str,
        *,
        use_llm: bool = False,
    ) -> list[SkillSchema]:
        """Extract known skills from unstructured text.

        Performs a case-insensitive scan of *text* against the built-in skill
        lexicon.  When *use_llm* is ``True`` and an LLM client is configured,
        it also delegates to the LLM to surface skills not in the lexicon.

        Args:
            text: Free-form text (job description, resume, etc.).
            use_llm: Whether to fall back to the LLM for unregistered skills.

        Returns:
            A deduplicated list of :class:`SkillSchema` objects.
        """
        text_lower = text.lower()
        found: dict[str, SkillSchema] = {}

        # Lexicon-based extraction (longest match first - patterns pre-compiled).
        for pattern, skill_name, category in _SKILL_PATTERNS:
            if pattern.search(text_lower) and skill_name not in found:
                found[skill_name] = SkillSchema(
                    name=skill_name.title(),
                    category=category,
                    proficiency=None,
                )

        # LLM-based extraction for skills outside the lexicon.
        if use_llm and self._llm is not None and len(text) > 20:
            try:
                llm_skills = self._extract_skills_via_llm(text)
                for skill in llm_skills:
                    key = skill.name.lower()
                    if key not in found and key not in _KNOWN_SKILLS:
                        found[key] = skill
            except Exception:
                logger.warning("LLM skill extraction failed - falling back to lexicon only")

        return list(found.values())

    async def _extract_skills_via_llm(self, text: str) -> list[SkillSchema]:
        """Use the LLM to extract skills not in the known lexicon."""
        if self._llm is None:
            return []

        prompt = (
            "Extract a list of technical skills, tools, and concepts mentioned "
            "in the following text.  Return a JSON array of objects with keys "
            f'"name", "category" (one of: language, framework, database, cloud, '
            f'tool, concept), and "proficiency" (or null).\n\n'
            f"Text:\n{text[:2000]}"
        )

        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "skills": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "category": {"type": "string", "enum": ["language", "framework", "database", "cloud", "tool", "concept"]},
                            "proficiency": {"type": ["string", "null"]},
                        },
                        "required": ["name", "category"],
                    },
                }
            },
            "required": ["skills"],
        }

        result = await self._llm.generate_structured(prompt, schema, max_tokens=1024)
        return [SkillSchema(**s) for s in result.get("skills", [])]

    # ── Resume / work-history parsing ──────────────────────────────────────────

    def parse_resume_text(
        self,
        text: str,
        *,
        source_name: str = "parsed_resume",
    ) -> list[WorkExperienceSchema]:
        """Parse a free-form resume or work-history block into structured entries.

        Handles common resume formats:
        - **Chronological**: each role starts with a title + company line
        - **Bullet-points**: responsibilities follow under each role

        Args:
            text: The raw resume or work-history text.
            source_name: A label for the source (used in log messages).

        Returns:
            A list of :class:`WorkExperienceSchema` objects.  May be empty if
            no structured roles could be parsed.
        """
        if not text or not text.strip():
            return []

        lines = [line.strip() for line in text.split("\n") if line.strip()]

        # Strategy 1: Try date-pair-based splitting (most reliable).
        experiences = self._parse_by_dates(lines)

        # Strategy 2: Fall back to LLM-based parsing if available and regex failed.
        if not experiences and self._llm is not None and len(text) > 100:
            try:
                experiences = self._parse_via_llm(text)
            except Exception:
                logger.warning("LLM resume parsing failed", source=source_name)

        if not experiences:
            logger.info(
                "Resume text did not yield structured entries - all text aggregated into one experience",
                source=source_name,
            )
            # Last resort: treat the whole text as a single entry.
            skills = self.extract_skills_from_text(text)
            experiences.append(
                WorkExperienceSchema(
                    company="Unknown",
                    title="Professional Experience",
                    description=text[:2000],
                    skills_used=[s.name for s in skills],
                )
            )

        logger.debug(
            "Parsed resume text",
            source=source_name,
            entries=len(experiences),
            characters=len(text),
        )
        return experiences

    def _parse_by_dates(self, lines: Sequence[str]) -> list[WorkExperienceSchema]:
        """Split lines by date-range delimiters and build experience entries."""
        experiences: list[WorkExperienceSchema] = []
        current_block: list[str] = []
        current_dates: tuple[datetime.date | None, datetime.date | None] = (None, None)

        for line in lines:
            # Look for date markers.
            date_found = False
            for pattern in _DATE_PATTERNS:
                match = pattern.search(line)
                if match:
                    # Save the previous block if non-empty.
                    if current_block:
                        exp = self._build_experience(current_block, current_dates[0], current_dates[1])
                        if exp is not None:
                            experiences.append(exp)

                    start = _parse_date(match.group("start"))
                    end = _parse_date(match.group("end"))
                    current_block = [line]
                    current_dates = (start, end)
                    date_found = True
                    break

            if not date_found:
                current_block.append(line)

        # Don't forget the last block.
        if current_block:
            exp = self._build_experience(current_block, current_dates[0], current_dates[1])
            if exp is not None:
                experiences.append(exp)

        return experiences

    def _build_experience(
        self,
        lines: Sequence[str],
        start_date: datetime.date | None,
        end_date: datetime.date | None,
    ) -> WorkExperienceSchema | None:
        """Convert a block of lines into a single experience entry.

        Heuristic: the first line typically contains the title and company
        separated by "at", "@", "-", ",", or "|".
        """
        if not lines:
            return None

        header = lines[0]
        description = "\n".join(lines[1:]) if len(lines) > 1 else None

        # Parse header: "Title at Company", "Title @ Company", "Title - Company", etc.
        title = header
        company = "Unknown"

        for sep in [" at ", " @ ", " - ", " - ", " - ", " | ", ", "]:
            if sep in header:
                parts = header.split(sep, 1)
                title = parts[0].strip()
                company = parts[1].strip()
                break

        # Extract skills from the full block.
        full_text = " ".join(lines)
        skills = self.extract_skills_from_text(full_text)
        is_current = (
            end_date is not None
            and end_date >= datetime.date.today() - datetime.timedelta(days=60)
        )

        return WorkExperienceSchema(
            company=company,
            title=title,
            start_date=start_date,
            end_date=end_date,
            description=description[:2000] if description else None,
            skills_used=[s.name for s in skills],
            is_current=is_current or (end_date is None and start_date is not None),
        )

    async def _parse_via_llm(self, text: str) -> list[WorkExperienceSchema]:
        """Delegate full resume parsing to the LLM for complex formats."""
        if self._llm is None:
            return []

        prompt = (
            "Parse the following resume or work history into structured JSON. "
            "Return a JSON array of objects with keys: company, title, "
            "start_date (ISO date string or null), end_date (ISO date string or null), "
            "description, skills_used (array of strings), is_current (bool).\n\n"
            f"Text:\n{text[:4000]}"
        )

        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "experiences": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "company": {"type": "string"},
                            "title": {"type": "string"},
                            "start_date": {"type": ["string", "null"]},
                            "end_date": {"type": ["string", "null"]},
                            "description": {"type": ["string", "null"]},
                            "skills_used": {"type": "array", "items": {"type": "string"}},
                            "is_current": {"type": "boolean"},
                        },
                        "required": ["company", "title"],
                    },
                }
            },
            "required": ["experiences"],
        }

        result = await self._llm.generate_structured(prompt, schema, max_tokens=2048)

        entries: list[WorkExperienceSchema] = []
        for item in result.get("experiences", []):
            start = _parse_date(item["start_date"]) if item.get("start_date") else None
            end = _parse_date(item["end_date"]) if item.get("end_date") else None
            entries.append(
                WorkExperienceSchema(
                    company=item["company"],
                    title=item["title"],
                    start_date=start,
                    end_date=end,
                    description=item.get("description"),
                    skills_used=item.get("skills_used", []),
                    is_current=item.get("is_current", False),
                )
            )
        return entries

    # ── Utility ──────────────────────────────────────────────────────────────

    def parse_resume_file(self, file_path: str | Path) -> list[WorkExperienceSchema]:
        """Read a text file and parse its contents as a resume.

        For PDF or DOCX files, only the raw text content is extracted (no
        complex format preservation - use a dedicated library for that).
        """
        path = Path(file_path)
        if not path.exists():
            msg = f"Resume file not found: {path}"
            raise ProfileError(msg)

        text = path.read_text(encoding="utf-8", errors="replace")
        return self.parse_resume_text(text, source_name=path.name)
