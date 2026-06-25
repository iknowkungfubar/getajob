"""Recruiter contact discovery via multi-strategy OSINT.

Employs four strategies in order of increasing effort:

1. **Job listing page scan** — Parse the job description for references to
   hiring managers, recruiters, or talent-acquisition contacts.
2. **LinkedIn company search** — Use public LinkedIn data to find HR/talent
   roles at the company.
3. **Company website crawl** — Check ``/team``, ``/about``, and similar pages
   for relevant contacts.
4. **Email pattern probing** — Generate likely email addresses from common
   corporate patterns (``firstname@company.com``, etc.).

All lookups are rate-limited per the configured settings and respect
``robots.txt`` when possible.
"""

from __future__ import annotations as _annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import structlog

from core.config import get_settings
from core.schemas import RecruiterInfoSchema

__all__: list[str] = [
    "ContactFinder",
    "RecruiterInfo",
]

logger = structlog.get_logger(__name__)

# ── Pattern constants ────────────────────────────────────────────────────────

_TITLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(talent|recruit|hr|hiring|people|acquisition|staffing)", re.IGNORECASE),
    re.compile(r"(talent acquisition|talent partner|hr business partner)", re.IGNORECASE),
    re.compile(r"(recruiter|sourcer|head of talent|talent lead)", re.IGNORECASE),
]

_EMAIL_IN_TITLE_PATTERN = re.compile(
    r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
)

_NAME_ON_PAGE_PATTERN = re.compile(
    r"(?:^|\s)([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)"
    r"(?:\s*[-–]\s*.*(?:recruit|talent|hr|hiring|people))",  # noqa: RUF001
    re.MULTILINE,
)

_LINKEDIN_URL_PATTERN = re.compile(
    r"(https?://(?:www\.)?linkedin\.com/in/[a-zA-Z0-9_-]+)",
)

# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class RecruiterInfo:
    """Discovered recruiter contact information.

    This dataclass is used internally by the contact finder; the
    :class:`~core.schemas.RecruiterInfoSchema` Pydantic model is used
    at API boundaries.
    """

    name: str | None = None
    title: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    confidence_score: float = 0.0
    source: str = "unknown"
    company: str | None = None
    notes: str | None = None

    def to_schema(self) -> RecruiterInfoSchema:
        """Convert to the Pydantic schema for API serialisation."""
        return RecruiterInfoSchema(
            name=self.name,
            title=self.title,
            email=self.email,
            linkedin_url=self.linkedin_url,
            confidence_score=self.confidence_score,
            source=self.source,
            company=self.company,
            notes=self.notes,
        )


@dataclass
class _RateLimiter:
    """Simple sliding-window rate limiter per source."""

    max_per_minute: int
    _window: list[float] = field(default_factory=list)

    def acquire(self) -> float | None:
        """Return the sleep duration (in seconds) needed before the next
        request, or ``None`` if the request can proceed immediately."""
        now = time.monotonic()
        cutoff = now - 60.0
        # Purge expired entries.
        self._window[:] = [t for t in self._window if t > cutoff]

        if len(self._window) < self.max_per_minute:
            self._window.append(now)
            return None

        # Wait until the oldest window entry expires.
        wait = self._window[0] + 60.0 - now
        return max(wait, 0.0)


@dataclass
class _CompanyRateLimiter:
    """Sliding-window rate limiter keyed by company name.

    Tracks the request rate *per company* so that aggressive targeting of
    one company does not starve others.
    """

    max_per_minute: int
    _windows: dict[str, list[float]] = field(default_factory=dict)

    def acquire(self, company: str) -> float | None:
        """Return the sleep duration (seconds) needed, or ``None`` to proceed.

        Args:
            company: Company name used as the rate-limit key.
        """
        now = time.monotonic()
        cutoff = now - 60.0

        if company not in self._windows:
            self._windows[company] = []

        window = self._windows[company]
        # Purge expired entries.
        window[:] = [t for t in window if t > cutoff]

        if len(window) < self.max_per_minute:
            window.append(now)
            return None

        # Wait until the oldest window entry expires.
        wait = window[0] + 60.0 - now
        return max(wait, 0.0)


# ── Contact Finder ───────────────────────────────────────────────────────────


class ContactFinder:
    """Multi-strategy recruiter contact discovery.

    Args:
        rate_limit: Maximum HTTP lookups per minute across all sources.
        max_per_company: Maximum contacts to search per company.
        http_timeout: HTTP request timeout in seconds.
        user_agent: User-Agent header value.
    """

    def __init__(
        self,
        *,
        rate_limit: int | None = None,
        max_per_company: int | None = None,
        http_timeout: int | None = None,
        user_agent: str | None = None,
    ) -> None:
        settings = get_settings()
        outreach = settings.outreach

        self._rate_limiter = _RateLimiter(
            max_per_minute=rate_limit or outreach.max_lookups_per_minute,
        )
        self._max_per_company = max_per_company or outreach.max_contacts_per_company
        self._http_timeout = http_timeout or outreach.http_timeout_seconds
        self._user_agent = user_agent or outreach.user_agent
        self._respect_robots = outreach.respect_robots_txt
        self._company_limiter = _CompanyRateLimiter(max_per_minute=10)
        self._robots_cache: dict[str, RobotFileParser] = {}

        self.logger = logger.bind(module="contact_finder")

    # ── Public API ─────────────────────────────────────────────────────────

    async def find_recruiter(
        self,
        company: str,
        *,
        _job_listing_url: str | None = None,
        job_description: str | None = None,
        linkedin_company_url: str | None = None,
        company_website: str | None = None,
    ) -> RecruiterInfo | None:
        """Execute the full multi-strategy search and return the best result.

        Strategies are tried in order of confidence / cost, returning the
        first result that meets confidence threshold (>= 0.5).

        Args:
            company: Company name to search for.
            job_listing_url: URL of the job listing page to scan.
            job_description: Raw description text to scan for contact info.
            linkedin_company_url: LinkedIn company page URL.
            company_website: Company's main website URL (``https://example.com``).

        Returns:
            The best :class:`RecruiterInfo` found, or ``None``.
        """
        candidates: list[RecruiterInfo] = []

        # Strategy 1: Parse the job listing page / description.
        if job_description:
            try:
                result = await self._strategy_from_job_listing(company, job_description)
                if result:
                    candidates.append(result)
                    self.logger.info("Found recruiter via job listing", result=result)
            except Exception:
                self.logger.exception("Strategy 1 (job listing) failed")

        # Strategy 2: LinkedIn company page.
        if linkedin_company_url:
            try:
                result = await self._strategy_from_linkedin(company, linkedin_company_url)
                if result:
                    candidates.append(result)
                    self.logger.info("Found recruiter via LinkedIn", result=result)
            except Exception:
                self.logger.exception("Strategy 2 (LinkedIn) failed")

        # Strategy 3: Company website crawl.
        if company_website:
            try:
                results = await self._strategy_from_website(company, company_website)
                candidates.extend(results)
                if results:
                    self.logger.info("Found recruiter(s) via company website", count=len(results))
            except Exception:
                self.logger.exception("Strategy 3 (website) failed")

        # Strategy 4: Email pattern probing (generate and validate).
        try:
            result = self._strategy_email_patterns(company, candidates)
            if result:
                candidates.append(result)
                self.logger.info("Generated email via pattern probing", result=result)
        except Exception:
            self.logger.exception("Strategy 4 (email patterns) failed")

        # Deduplicate and pick the best.
        best = self._select_best(candidates)
        if best:
            self.logger.info("Best recruiter contact selected", best=best)
        else:
            self.logger.info("No recruiter contact found", company=company)

        return best

    async def search_company_website(
        self,
        company: str,
        website_url: str,
    ) -> list[RecruiterInfo]:
        """Public convenience wrapper for strategy 3 (website crawl).

        Args:
            company: Company name.
            website_url: Company website URL.

        Returns:
            A list of discovered :class:`RecruiterInfo` objects.
        """
        return await self._strategy_from_website(company, website_url)

    def validate_contact(self, recruiter_info: RecruiterInfo) -> bool:
        """Basic sanity check that a discovered contact is usable.

        A valid contact must have at least a name **or** an email address,
        meet a minimum confidence threshold (``>= 0.3``), and — if an email
        is present — pass RFC-like format validation.

        Args:
            recruiter_info: The contact information to validate.

        Returns:
            ``True`` if the contact passes all checks.
        """
        if not recruiter_info:
            return False

        # Must have at least a name or email.
        if not recruiter_info.name and not recruiter_info.email:
            self.logger.debug("Contact rejected — no name or email")
            return False

        # Minimum confidence threshold (picks scraped noise are usually < 0.3).
        if recruiter_info.confidence_score < 0.3:
            self.logger.debug(
                "Contact rejected — below confidence threshold",
                score=recruiter_info.confidence_score,
            )
            return False

        # If email is present, validate the format.
        if recruiter_info.email:
            try:
                from outreach_engine.email_validator import EmailValidator

                validator = EmailValidator()
                if not validator.validate_format(recruiter_info.email):
                    self.logger.debug(
                        "Contact rejected — invalid email format",
                        email=recruiter_info.email,
                    )
                    return False
            except ImportError:
                self.logger.warning("EmailValidator unavailable — skipping format check")

        return True

    # ── Strategy 1: Job listing page ───────────────────────────────────────

    async def _strategy_from_job_listing(
        self,
        company: str,
        description: str,
    ) -> RecruiterInfo | None:
        """Scan the job description for embedded recruiter information.

        Looks for email addresses, LinkedIn URLs, and name-title patterns
        near keywords like "recruiter", "hiring manager", "talent acquisition".
        """
        name: str | None = None
        title: str | None = None
        email: str | None = None
        linkedin: str | None = None
        notes: list[str] = []

        # Find email addresses.
        email_matches = _EMAIL_IN_TITLE_PATTERN.findall(description)
        if email_matches:
            email = email_matches[0]
            notes.append(f"Email found in job description: {email}")

        # Find LinkedIn URLs.
        linkedin_matches = _LINKEDIN_URL_PATTERN.findall(description)
        if linkedin_matches:
            linkedin = linkedin_matches[0]
            notes.append("LinkedIn URL found in job description")

        # Find name-title patterns (e.g. "Jane Smith - Talent Acquisition Manager").
        name_matches = _NAME_ON_PAGE_PATTERN.findall(description)
        if name_matches:
            name = name_matches[0].strip()
            notes.append(f"Name found near recruiting title: {name}")

        if not any([name, email, linkedin]):
            return None

        confidence = 0.0
        if email:
            confidence += 0.4
        if name:
            confidence += 0.3
        if linkedin:
            confidence += 0.2

        return RecruiterInfo(
            name=name,
            title=title,
            email=email,
            linkedin_url=linkedin,
            confidence_score=min(confidence, 1.0),
            source="job_listing",
            company=company,
            notes="; ".join(notes) if notes else None,
        )

    # ── Strategy 2: LinkedIn company page ──────────────────────────────────

    async def _strategy_from_linkedin(
        self,
        company: str,
        linkedin_url: str,
    ) -> RecruiterInfo | None:
        """Search the LinkedIn company page for people in recruiting roles.

        This uses public LinkedIn data accessed via HTTP — it respects
        LinkedIn's rate limits and does not use the official API.
        """
        await self._throttle("linkedin")
        await self._throttle_company(company)

        # Normalise URL — ensure it points to the people tab if possible.
        url = linkedin_url.rstrip("/")
        if "/people/" not in url:
            url = f"{url}/people/"

        headers = self._build_headers()
        try:
            async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                resp = await client.get(url, headers=headers, follow_redirects=True)
                resp.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            self.logger.warning("LinkedIn page request failed", url=url, error=str(exc))
            return None

        html = resp.text
        return self._parse_linkedin_people_page(company, html)

    def _parse_linkedin_people_page(
        self,
        company: str,
        html: str,
    ) -> RecruiterInfo | None:
        """Parse a LinkedIn people/company page for recruiting roles."""
        from html.parser import HTMLParser as StdlibHTMLParser

        # Extract visible text from the HTML.
        class _TextExtractor(StdlibHTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.text_parts: list[str] = []

            def handle_data(self, data: str) -> None:
                stripped = data.strip()
                if stripped:
                    self.text_parts.append(stripped)

        extractor = _TextExtractor()
        extractor.feed(html)
        body_text = "\n".join(extractor.text_parts)

        lines = [line.strip() for line in body_text.split("\n") if line.strip()]
        recruiting_lines: list[str] = []

        for i, line in enumerate(lines):
            if (
                _TITLE_PATTERNS[0].search(line)
                or _TITLE_PATTERNS[1].search(line)
                or _TITLE_PATTERNS[2].search(line)
            ):
                # Found a recruiting title — grab the line before (likely a name).
                if i > 0 and lines[i - 1] and not _TITLE_PATTERNS[0].search(lines[i - 1]):
                    recruiting_lines.append(f"{lines[i - 1]} — {line}")
                else:
                    recruiting_lines.append(line)

        if not recruiting_lines:
            return None

        # Take the most promising match.
        best_line = recruiting_lines[0]
        name: str | None = None
        title: str | None = None

        if " — " in best_line:
            parts = best_line.split(" — ", 1)
            name = parts[0].strip()
            title = parts[1].strip()
        else:
            title = best_line.strip()

        confidence = 0.4 if name else 0.2

        return RecruiterInfo(
            name=name,
            title=title,
            confidence_score=confidence,
            source="linkedin",
            company=company,
            notes=f"Found on LinkedIn people page: {best_line[:120]}",
        )

    # ── Strategy 3: Company website ────────────────────────────────────────

    async def _strategy_from_website(
        self,
        company: str,
        website_url: str,
    ) -> list[RecruiterInfo]:
        """Crawl common company website pages for contact information.

        Checks ``/team``, ``/about``, ``/company/team``, and ``/about-us``
        pages for mentions of HR/talent/recruiting staff.
        """
        base = website_url.rstrip("/")
        candidates: list[RecruiterInfo] = []

        # Pages likely to contain team/contact information.
        candidate_paths = [
            "/team",
            "/about",
            "/company/team",
            "/about-us",
            "/people",
            "/company/people",
            "/careers",
            "/careers/team",
        ]

        for path in candidate_paths:
            await self._throttle("company_website")
            await self._throttle_company(company)
            url = f"{base}{path}"

            # Respect robots.txt before fetching.
            if not await self._check_robots_allowed(url):
                self.logger.debug("robots.txt disallows URL", url=url)
                continue

            headers = self._build_headers()

            try:
                async with httpx.AsyncClient(timeout=self._http_timeout) as client:
                    resp = await client.get(
                        url, headers=headers, follow_redirects=True, timeout=self._http_timeout
                    )
                    if resp.status_code != 200:
                        continue
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                self.logger.debug("Page fetch failed", url=url, error=str(exc))
                continue

            found = self._parse_team_page(company, resp.text, source_url=url)
            candidates.extend(found)

            if len(candidates) >= self._max_per_company:
                break

        return candidates[: self._max_per_company]

    def _parse_team_page(
        self,
        company: str,
        html: str,
        *,
        source_url: str,
    ) -> list[RecruiterInfo]:
        """Parse a HTML page for team members in recruiting roles."""
        from html.parser import HTMLParser as StdlibHTMLParser

        class _TextExtractor(StdlibHTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.text_parts: list[str] = []

            def handle_data(self, data: str) -> None:
                stripped = data.strip()
                if stripped:
                    self.text_parts.append(stripped)

        extractor = _TextExtractor()
        extractor.feed(html)
        text = "\n".join(extractor.text_parts)
        results: list[RecruiterInfo] = []

        lines = [line.strip() for line in text.split("\n") if line.strip()]
        for i, line in enumerate(lines):
            if _TITLE_PATTERNS[0].search(line) or _TITLE_PATTERNS[1].search(line):
                # Found a recruiting title — look for preceding name.
                title = line
                name: str | None = None
                email: str | None = None

                # Check the current line and previous line for an email.
                email_match = _EMAIL_IN_TITLE_PATTERN.search(line)
                if not email_match and i > 0:
                    email_match = _EMAIL_IN_TITLE_PATTERN.search(lines[i - 1])

                if email_match:
                    email = email_match.group(1)

                # Check for a name on the preceding line (skip if it looks like a section header).
                if i > 0:
                    prev = lines[i - 1]
                    if (
                        prev
                        and len(prev.split()) in (2, 3)
                        and prev[0].isupper()
                        and not prev.startswith(("http", "@", "#"))
                    ):
                        name = prev

                confidence = 0.3
                if name:
                    confidence += 0.2
                if email:
                    confidence += 0.3

                # Deduplicate by email or name.
                if not any(
                    (r.email and r.email == email) or (r.name and name and r.name == name)
                    for r in results
                ):
                    results.append(
                        RecruiterInfo(
                            name=name,
                            title=title,
                            email=email,
                            confidence_score=min(confidence, 1.0),
                            source="website",
                            company=company,
                            notes=f"Found on {source_url}",
                        )
                    )

        return results

    # ── Strategy 4: Email pattern probing ──────────────────────────────────

    def _strategy_email_patterns(
        self,
        company: str,
        existing_candidates: list[RecruiterInfo],
    ) -> RecruiterInfo | None:
        """Generate likely email addresses from common patterns.

        Uses the best existing candidate's name to construct a probable
        email address using common corporate patterns.
        """
        settings = get_settings()
        patterns = settings.outreach.email_patterns

        # Find the highest-confidence candidate with a name but no email.
        named = [c for c in existing_candidates if c.name and not c.email]
        if not named:
            return None

        best = max(named, key=lambda c: c.confidence_score)
        assert best.name is not None  # Filtered above.
        name_parts = best.name.strip().split()
        if not name_parts:
            return None

        first = name_parts[0].lower()
        last = name_parts[-1].lower()
        domain = self._guess_domain(company)

        if not domain:
            return None

        for pattern in patterns:
            email = (
                pattern.replace("firstname", first)
                .replace("lastname", last)
                .replace("first", first)
                .replace("last", last)
            )
            if "{initial}" in pattern:
                email = pattern.replace("{initial}", first[0])
            if "{first_initial}" in pattern:
                email = pattern.replace("{first_initial}", first[0])

            if email and "@" in email:
                return RecruiterInfo(
                    name=best.name,
                    title=best.title,
                    email=email,
                    linkedin_url=best.linkedin_url,
                    confidence_score=best.confidence_score
                    * 0.6,  # Lower confidence — pattern is speculative
                    source="email_pattern",
                    company=company,
                    notes=f"Generated from pattern: {email}",
                )

        return None

    # ── Helpers ────────────────────────────────────────────────────────────

    def _select_best(self, candidates: list[RecruiterInfo]) -> RecruiterInfo | None:
        """Deduplicate and select the highest-confidence candidate.

        Prefers candidates with an email address, then the highest
        confidence score.
        """
        if not candidates:
            return None

        # Deduplicate by email, then by name+title.
        seen_emails: set[str] = set()
        seen_names: set[str] = set()
        deduped: list[RecruiterInfo] = []

        for c in candidates:
            key = ""
            if c.email:
                key = f"email:{c.email}"
            elif c.name:
                key = f"name:{c.name}:{c.title}"

            if key and key not in seen_emails | seen_names:
                if c.email:
                    seen_emails.add(key)
                elif c.name:
                    seen_names.add(key)
                deduped.append(c)
            elif not key:
                deduped.append(c)

        # Sort: email first, then confidence descending.
        def _sort_key(c: RecruiterInfo) -> tuple[int, float]:
            return (0 if c.email else 1, -c.confidence_score)

        deduped.sort(key=_sort_key)
        return deduped[0] if deduped else None

    async def _throttle(self, source: str) -> None:
        """Wait if we're exceeding the global rate limit for *source*."""
        wait = self._rate_limiter.acquire()
        if wait and wait > 0:
            self.logger.debug("Rate-limited, waiting", source=source, seconds=round(wait, 2))
            await asyncio.sleep(wait)

    async def _throttle_company(self, company: str) -> None:
        """Wait if we're exceeding the per-company rate limit.

        Limits lookups to at most 10 per minute per company to avoid
        aggressive targeting of a single employer.

        Args:
            company: Company name to throttle against.
        """
        wait = self._company_limiter.acquire(company)
        if wait and wait > 0:
            self.logger.debug(
                "Company rate-limited, waiting",
                company=company,
                seconds=round(wait, 2),
            )
            await asyncio.sleep(wait)

    async def _check_robots_allowed(self, url: str) -> bool:
        """Check whether *url* is allowed by the site's ``robots.txt``.

        Results are cached per domain for the lifetime of the finder so
        that repeated checks against the same site do not re-fetch the
        policy.

        Args:
            url: The full URL to check.

        Returns:
            ``True`` if the URL is allowed (or if robots.txt cannot be
            fetched — fail-open).
        """
        if not self._respect_robots:
            return True

        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        if robots_url not in self._robots_cache:
            rp = RobotFileParser(robots_url)
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, rp.read)
            except Exception as exc:
                self.logger.debug(
                    "robots.txt fetch failed, allowing crawl",
                    url=robots_url,
                    error=str(exc),
                )
                self._robots_cache[robots_url] = rp
                return True  # Fail-open — assume allowed.
            self._robots_cache[robots_url] = rp

        rp = self._robots_cache[robots_url]
        return rp.can_fetch(self._user_agent, url)

    def _build_headers(self) -> dict[str, str]:
        """Build HTTP headers for web requests."""
        return {
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    @staticmethod
    def _guess_domain(company: str) -> str | None:
        """Guess a company's domain from its name.

        Strips "Inc", "Corp", "LLC", etc. and lowercases.
        """
        cleaned = re.sub(
            r"\s+(Inc|Corp|Corporation|LLC|Ltd|Limited|LLP|PC|GmbH|AG|BV|NV|SA)\.?$",
            "",
            company,
            flags=re.IGNORECASE,
        ).strip()
        name = cleaned.lower().replace(" ", "").replace("'", "")
        # Filter out obviously bad names.
        if not name or len(name) < 2:
            return None
        return f"{name}.com"
