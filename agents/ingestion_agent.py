"""Ingestion Agent — Job Discovery & Ingestion Engine (Module 1).

Discovers job listings from multiple sources (LinkedIn, Indeed, Greenhouse,
Workday, Lever) by executing search vectors defined in ``config/settings.yaml``.

Key behaviours:
- **Multi-source dispatch**: API-based sources (Greenhouse, Lever) use ``httpx``;
  browser-based sources (LinkedIn, Indeed, Workday) delegate to the
  Browser Execution Engine stub.
- **Standardised schema**: Every listing is normalised to
  :class:`~core.schemas.JobListingCreate`.
- **Deduplication**: Listings are deduplicated by ``(company, title)`` within a
  configurable time window.
- **Rate limiting**: A token-bucket throttle per source prevents platform bans.
- **Event emission**: Each new listing fires a ``JOB_DISCOVERED`` event on
  the event bus.
"""

from __future__ import annotations as _annotations

import asyncio
import datetime
import json
import re
import statistics
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from core.config import get_settings
from core.database import get_session, create_engine
from core.event_bus import EventType, EventPriority
from core.exceptions import IngestionError, ConfigurationError
from core.models import JobListing
from core.schemas import JobListingCreate, SearchVectorConfig

from agents.base import BaseAgent

__all__: list[str] = [
    "IngestionAgent",
]

logger = structlog.get_logger(__name__)

# ── Source kind classification ────────────────────────────────────────────────

_API_SOURCES = {"greenhouse", "lever"}
_BROWSER_SOURCES = {"linkedin", "indeed", "workday"}

# ── Rate-limit token bucket ───────────────────────────────────────────────────


@dataclass
class _TokenBucket:
    """Simple token-bucket rate limiter per source.

    ``max_tokens`` tokens are available per minute (quota).  Tokens refill
    at a steady rate every ``refill_interval_s`` seconds.
    """

    max_tokens: float
    refill_interval_s: float = 1.0

    tokens: float = field(init=False)
    _last_refill: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.tokens = self.max_tokens

    async def acquire(self, tokens: float = 1.0) -> float:
        """Wait asynchronously until *tokens* are available and return the wait time (seconds)."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * (self.max_tokens / 60.0))
        self._last_refill = now

        if self.tokens < tokens:
            needed = tokens - self.tokens
            sleep_s = needed / (self.max_tokens / 60.0) if self.max_tokens > 0 else 1.0
            await asyncio.sleep(sleep_s)
            self.tokens = 0.0
            self._last_refill = time.monotonic()
            return sleep_s

        self.tokens -= tokens
        return 0.0


# ── Cache entry ───────────────────────────────────────────────────────────────


@dataclass
class _SeenEntry:
    """A previously-discovered listing for deduplication."""

    company: str
    title: str
    source: str
    discovered_at: datetime.datetime


# ── Ingestion Agent ───────────────────────────────────────────────────────────


class IngestionAgent(BaseAgent):
    """Discover job listings by executing configured search vectors.

    The agent iterates over every vector in ``config/settings.yaml`` →
    ``search_vectors`` and, for each source named in the vector, calls the
    appropriate discovery method.  Results are normalised, deduplicated, and
    persisted, and a ``JOB_DISCOVERED`` event is emitted for each new listing.

    Usage::

        agent = IngestionAgent(engine, event_bus=bus)
        await agent.run()  # scans all vectors, returns count of new listings
    """

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        *,
        event_bus: Any | None = None,
    ) -> None:
        super().__init__(name="ingestion", event_bus=event_bus)

        self._engine: AsyncEngine = engine or create_engine()
        self._http_client: httpx.AsyncClient | None = None

        # Rate-limit buckets keyed by source name.
        self._buckets: dict[str, _TokenBucket] = {}

        # In-memory dedup cache: {(company_lower, title_lower): _SeenEntry}
        self._seen: dict[tuple[str, str], _SeenEntry] = {}

        self._dedup_window_hours: int = 72
        self._cache_ttl_hours: int = 24

        # Stats
        self._stats: dict[str, int] = {
            "api_requests": 0,
            "api_errors": 0,
            "new_listings": 0,
            "duplicates_skipped": 0,
            "sources_queried": 0,
        }

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise the HTTP client and rate-limit buckets."""
        await super().start()
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
            },
        )

        # Build token buckets from rate_limits config.
        rate_limits = self.config.job_discovery.rate_limits
        for source, rpm in rate_limits.items():
            self._buckets[source] = _TokenBucket(max_tokens=float(rpm))

        self._dedup_window_hours = self.config.job_discovery.cache_ttl_hours or 72
        self._cache_ttl_hours = self.config.job_discovery.cache_ttl_hours or 24

        # Pre-load existing listings into the dedup cache.
        await self._load_existing_listings()

        self.logger.info(
            "Ingestion agent initialised",
            sources=list(rate_limits.keys()),
            dedup_window_hours=self._dedup_window_hours,
        )

    async def stop(self) -> None:
        """Tear down the HTTP client."""
        if self._http_client is not None:
            await self._http_client.aclose()
        await super().stop()

    # ── Main run loop ─────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Execute all search vectors and return a summary.

        Returns:
            A dict with keys:
            - ``total_vectors``: number of search vectors processed.
            - ``new_listings``: total unique new listings saved.
            - ``duplicates_skipped``: listings filtered by dedup.
            - ``api_errors``: per-source error counts.
            - ``sources_used``: list of sources that returned results.
        """
        vectors = await self._load_search_vectors()
        self.logger.info("Starting ingestion run", vector_count=len(vectors))

        all_new: list[JobListingCreate] = []
        sources_used: set[str] = set()

        for idx, vector in enumerate(vectors, start=1):
            self.logger.debug(
                "Processing search vector",
                vector_idx=idx,
                roles=vector.roles,
                sources=vector.sources,
            )

            for source in vector.sources:
                if source in _BROWSER_SOURCES:
                    # Browser-based sources are stubbed until Module 4.
                    self._stats.setdefault(f"stub:{source}", 0)
                    self._stats[f"stub:{source}"] += 1
                    self.logger.info(
                        "Browser-based source requires Module 4 (stub)",
                        source=source,
                        roles=vector.roles[:3],
                    )
                    self.logger.info(
                        "Browser-based discovery not yet available — stub",
                        source=source,
                        module="browser_engine.ats_profiles",
                    )
                    continue

                if source not in _API_SOURCES:
                    self.logger.warning("Unknown source — skipping", source=source)
                    continue

                try:
                    listings = await self.discover_from_source(source, vector)
                    all_new.extend(listings)
                    sources_used.add(source)
                    self._stats["sources_queried"] += 1
                except IngestionError as exc:
                    self._stats["api_errors"] += 1
                    self.logger.error("Source discovery failed", source=source, error=str(exc))

        # Deduplicate across all vectors.
        unique = self._deduplicate(all_new)

        # Persist unique listings.
        for listing in unique:
            await self._save_listing(listing)
            self._stats["new_listings"] += 1

        self.logger.info(
            "Ingestion run complete",
            total_vectors=len(vectors),
            new_listings=self._stats["new_listings"],
            duplicates_skipped=self._stats["duplicates_skipped"],
            sources_used=sorted(sources_used),
        )

        return {
            "total_vectors": len(vectors),
            "new_listings": self._stats["new_listings"],
            "duplicates_skipped": self._stats["duplicates_skipped"],
            "api_errors": self._stats["api_errors"],
            "sources_used": sorted(sources_used),
        }

    # ── Per-source discovery ─────────────────────────────────────────────

    async def discover_from_source(
        self,
        source: str,
        vector: SearchVectorConfig,
    ) -> list[JobListingCreate]:
        """Dispatch to the correct discovery method for *source*.

        Args:
            source: One of ``"greenhouse"``, ``"lever"``.
            vector: The search-vector configuration to execute.

        Returns:
            A list of standardised :class:`~core.schemas.JobListingCreate`
            objects.  May be empty.

        Raises:
            IngestionError: If the HTTP request fails repeatedly.
        """
        source_normalised = source.lower()

        if source_normalised == "greenhouse":
            return await self._discover_greenhouse(vector)
        if source_normalised == "lever":
            return await self._discover_lever(vector)

        self.logger.warning("No discovery implementation for source", source=source)
        return []

    # ── Greenhouse Discovery ─────────────────────────────────────────────

    async def _discover_greenhouse(self, vector: SearchVectorConfig) -> list[JobListingCreate]:
        """Query the Greenhouse public job-board API.

        Greenhouse has a per-company public API at
        ``https://boards-api.greenhouse.io/v1/boards/{company}/jobs``.
        We iterate over a set of known companies that match the search
        keywords, or use keyword-based search via the ``content`` parameter.
        """
        listings: list[JobListingCreate] = []
        companies = await self._resolve_companies_for_keywords("greenhouse", vector)

        if not companies:
            # Fall back to keyword-based discovery on the generic board.
            companies = self._default_greenhouse_companies()

        for company in companies:
            await self._rate_limit("greenhouse")

            try:
                jobs = await self._fetch_greenhouse_jobs(company)
            except IngestionError:
                self._stats["api_errors"] += 1
                continue

            for job in jobs:
                if self._job_matches_vector(job, vector):
                    listings.append(self._to_listing_create("greenhouse", company, job))

        return listings

    async def _fetch_greenhouse_jobs(self, company: str) -> list[dict[str, Any]]:
        """Fetch all active jobs for a Greenhouse company board.

        Returns:
            A list of raw job dicts from the Greenhouse API.
        """
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        params: dict[str, Any] = {"content": "true", "per_page": 100}

        client = self._require_http_client()
        self._stats["api_requests"] += 1

        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            msg = f"Greenhouse API error for company {company!r}: {exc.response.status_code}"
            raise IngestionError(msg) from exc
        except httpx.RequestError as exc:
            msg = f"Greenhouse request failed for company {company!r}: {exc}"
            raise IngestionError(msg) from exc

        return data.get("jobs", [])

    # ── Lever Discovery ────────────────────────────────────────────────

    async def _discover_lever(self, vector: SearchVectorConfig) -> list[JobListingCreate]:
        """Query the Lever public job-board API.

        Lever exposes per-company postings at
        ``https://api.lever.co/v0/postings/{company}``.
        """
        listings: list[JobListingCreate] = []
        companies = await self._resolve_companies_for_keywords("lever", vector)

        if not companies:
            companies = self._default_lever_companies()

        for company in companies:
            await self._rate_limit("lever")

            try:
                jobs = await self._fetch_lever_postings(company)
            except IngestionError:
                self._stats["api_errors"] += 1
                continue

            for job in jobs:
                if self._job_matches_vector(job, vector):
                    listings.append(self._to_listing_create("lever", company, job))

        return listings

    async def _fetch_lever_postings(self, company: str) -> list[dict[str, Any]]:
        """Fetch all active postings for a Lever company.

        Returns:
            A list of raw job dicts from the Lever API.
        """
        url = f"https://api.lever.co/v0/postings/{company}"

        client = self._require_http_client()
        self._stats["api_requests"] += 1

        try:
            response = await client.get(url, params={"mode": "json"})
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            msg = f"Lever API error for company {company!r}: {exc.response.status_code}"
            raise IngestionError(msg) from exc
        except httpx.RequestError as exc:
            msg = f"Lever request failed for company {company!r}: {exc}"
            raise IngestionError(msg) from exc

        # Lever returns an array at the top level.
        return data if isinstance(data, list) else []

    # ── Generic job board discovery (future use) ────────────────────────

    async def discover_generic(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> list[JobListingCreate]:
        """Scrape a generic career page for job listings.

        This is a placeholder that uses basic HTML parsing.  The Browser
        Execution Engine (Module 4) will handle complex single-page apps.

        Args:
            url: Career page URL.
            headers: Optional HTTP headers.

        Returns:
            A list of listing objects (may be empty if parsing fails).
        """
        client = self._require_http_client()
        self._stats["api_requests"] += 1

        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
        except httpx.RequestError as exc:
            msg = f"Generic fetch failed for {url}: {exc}"
            raise IngestionError(msg) from exc

        # TODO: Implement HTML/job-listing microdata parsing.
        # For now, return empty — the Browser Engine will handle this.
        self.logger.info("Generic discovery — HTML parsing not yet implemented", url=url)
        return []

    # ── Deduplication ───────────────────────────────────────────────────

    def _deduplicate(self, listings: list[JobListingCreate]) -> list[JobListingCreate]:
        """Remove listings that match seen entries within the dedup window.

        Dedup key: ``(company.lower(), title.lower())``.

        Args:
            listings: Raw listings (possibly with duplicates).

        Returns:
            Listings not seen before.
        """
        unique: list[JobListingCreate] = []
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=self._dedup_window_hours)

        for listing in listings:
            key = (listing.company.lower().strip(), listing.title.lower().strip())
            seen = self._seen.get(key)

            if seen and seen.discovered_at > cutoff:
                self._stats["duplicates_skipped"] += 1
                continue

            # Update cache.
            self._seen[key] = _SeenEntry(
                company=listing.company,
                title=listing.title,
                source=listing.source,
                discovered_at=datetime.datetime.now(datetime.UTC),
            )
            unique.append(listing)

        return unique

    # ── Persistence ─────────────────────────────────────────────────────

    async def _save_listing(self, listing_data: JobListingCreate) -> None:
        """Persist a single listing and emit a ``JOB_DISCOVERED`` event.

        Args:
            listing_data: The standardised listing to save.
        """
        async with get_session(self._engine) as session:
            # Check if this exact source_id already exists.
            if listing_data.source_id:
                existing = await session.execute(
                    select(JobListing).where(
                        JobListing.source == listing_data.source,
                        JobListing.source_id == listing_data.source_id,
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    self.logger.debug("Listing already exists — skipping save", source_id=listing_data.source_id)
                    return

            job = JobListing(
                source=listing_data.source,
                source_id=listing_data.source_id,
                company=listing_data.company,
                title=listing_data.title,
                location=listing_data.location,
                description_json=listing_data.description_json,
                url=listing_data.url,
                posted_date=listing_data.posted_date,
                required_skills=listing_data.required_skills,
                salary_range=listing_data.salary_range,
                form_type=listing_data.form_type,
                is_active=True,
            )
            session.add(job)
            await session.flush()

            # Emit event for the new listing.
            await self.emit_event(
                EventType.JOB_DISCOVERED,
                data={
                    "job_id": str(job.id),
                    "source": job.source,
                    "company": job.company,
                    "title": job.title,
                    "url": job.url,
                },
                priority=EventPriority.NORMAL,
            )

            self.logger.debug(
                "Job listing saved and event emitted",
                job_id=str(job.id),
                company=job.company,
                title=job.title,
            )

    # ── Normalisation helpers ───────────────────────────────────────────

    def _to_listing_create(
        self,
        source: str,
        company: str,
        raw_job: dict[str, Any],
    ) -> JobListingCreate:
        """Normalise a raw API job dict into a :class:`~core.schemas.JobListingCreate`.

        Handles both Greenhouse and Lever API shapes via field heuristics.
        """
        # Greenhouse shape.
        title = raw_job.get("title", raw_job.get("name", "Unknown Position"))
        job_id = raw_job.get("id", raw_job.get("postingId", ""))
        absolute_url = (
            raw_job.get("absolute_url")
            or raw_job.get("hostedUrl")
            or raw_job.get("applyUrl")
        )

        # Lever shape.
        if isinstance(raw_job.get("description"), dict):
            description_text = raw_job["description"].get("text", raw_job["description"].get("plain", ""))
        else:
            description_text = raw_job.get("description", raw_job.get("content", ""))

        # Lever wraps categories.
        categories = raw_job.get("categories", raw_job.get("metadata", {}))
        location_raw = raw_job.get("location", categories.get("location", ""))
        if isinstance(location_raw, dict):
            location = location_raw.get("name", "")
        else:
            location = str(location_raw) if location_raw else None

        # Extract skills from description.
        skills = self._extract_skills_from_text(description_text)

        return JobListingCreate(
            source=source,
            source_id=str(job_id) if job_id else None,
            company=company,
            title=title,
            location=location,
            description_json={
                "raw": description_text[:10000] if description_text else "",
            },
            url=absolute_url,
            posted_date=_parse_datetime(raw_job.get("updatedAt", raw_job.get("createdAt"))),
            required_skills=skills or None,
            salary_range=_extract_salary(raw_job),
            form_type=source,
        )

    # ── Skill extraction ──────────────────────────────────────────────────

    def _extract_skills_from_text(self, text: str) -> list[str]:
        """Simple keyword-based skill extraction for normalisation.

        Uses the same lexicon as :class:`~profile_engine.experience_parser.ExperienceParser`.
        """
        # Known skill keywords (lowercase).
        known = {
            "python", "rust", "typescript", "javascript", "go", "java", "c++", "c#",
            "kotlin", "swift", "scala", "ruby", "elixir", "sql", "graphql", "bash",
            "django", "fastapi", "flask", "spring", "react", "angular", "vue",
            "next.js", "node.js", "express", "pytorch", "tensorflow", "langchain",
            "postgresql", "postgres", "mysql", "mongodb", "redis", "elasticsearch",
            "dynamodb", "cassandra", "clickhouse", "bigquery", "snowflake",
            "aws", "gcp", "azure", "kubernetes", "docker", "terraform", "ansible",
            "helm", "prometheus", "grafana", "kafka", "rabbitmq", "nginx",
            "distributed systems", "microservices", "grpc", "rest", "event-driven",
            "machine learning", "deep learning", "nlp", "rag",
        }

        text_lower = text.lower()
        found: list[str] = []
        for skill in sorted(known, key=len, reverse=True):
            pattern = re.compile(r"\b" + re.escape(skill) + r"\b", re.IGNORECASE)
            if pattern.search(text_lower) and skill not in found:
                found.append(skill.title())
        return found

    # ── Matching ──────────────────────────────────────────────────────────

    def _job_matches_vector(self, raw_job: dict[str, Any], vector: SearchVectorConfig) -> bool:
        """Check if a raw job dict matches at least one criterion in *vector*.

        Performs keyword matching on title, description, and categories.
        Returns ``True`` if the job title contains any of the vector's role
        keywords *or* if the description mentions any of the vector's
        technology keywords.
        """
        title = (raw_job.get("title", "") or "").lower()
        description_text = ""
        desc_raw = raw_job.get("description", raw_job.get("content", ""))
        if isinstance(desc_raw, dict):
            description_text = (desc_raw.get("text", "") or "").lower()
        else:
            description_text = (str(desc_raw) or "").lower()
        location = str(raw_job.get("location", "") or "").lower()
        categories = raw_job.get("categories", {})
        if isinstance(categories, dict):
            cat_text = " ".join(str(v).lower() for v in categories.values())
        else:
            cat_text = ""

        combined = f"{title} {description_text} {location} {cat_text}"

        # Check role match.
        role_match = any(role.lower() in title for role in vector.roles)

        # Check keyword match.
        keyword_match = any(kw.lower() in combined for kw in vector.keywords)

        # Check location match (if vector specifies locations).
        location_match = True
        if vector.locations:
            location_match = any(loc.lower() in location or loc.lower() in combined for loc in vector.locations)

        return (role_match or keyword_match) and location_match

    # ── Rate limiting ──────────────────────────────────────────────────

    async def _rate_limit(self, source: str) -> None:
        """Block if the token bucket for *source* is empty."""
        bucket = self._buckets.get(source)
        if bucket is not None:
            waited = await bucket.acquire(1.0)
            if waited > 0.01:
                self.logger.debug("Rate-limited", source=source, waited_s=round(waited, 2))

    # ── Company resolution ─────────────────────────────────────────────

    async def _resolve_companies_for_keywords(
        self,
        source: str,
        vector: SearchVectorConfig,
    ) -> list[str]:
        """Query the source API to find companies with matching jobs.

        For API sources we maintain a default list of well-known companies
        to query.  A production implementation would maintain a company
        index or use a search endpoint.
        """
        return []  # Return empty to trigger default company list.

    def _default_greenhouse_companies(self) -> list[str]:
        """Return a list of notable companies using Greenhouse for ATS."""
        return [
            "airbnb", "datadog", "discord", "dropbox", "gitlab",
            "hashicorp", "hubspot", "instacart", "lyft", "notion",
            "palantir", "pinterest", "reddit", "snowflake", "spotify",
            "square", "stripe", "twitter", "uber", "vercel",
            "zapier", "zendesk", "zillow",
        ]

    def _default_lever_companies(self) -> list[str]:
        """Return a list of notable companies using Lever for ATS."""
        return [
            "asana", "atlassian", "box", "brex", "coinbase",
            "confluent", "databricks", "deel", "doordash",
            "doximity", "dbt-labs", "figma", "fivetran", "gong",
            "grafana", "intercom", "looker", "mux", "netlify",
            "okta", "opensea", "ora", "pagerduty", "ramp",
            "retool", "revolut", "robinhood", "sentry", "shopify",
            "squarespace", "substack", "supabase", "twilio",
            "typeform", "vercel", "webflow", "wework",
        ]

    # ── Internal helpers ─────────────────────────────────────────────────

    def _require_http_client(self) -> httpx.AsyncClient:
        """Return the HTTP client, raising if not initialised."""
        if self._http_client is None:
            msg = "HTTP client not initialised — call await agent.start() first."
            raise IngestionError(msg)
        return self._http_client

    async def _load_existing_listings(self) -> int:
        """Pre-populate the in-memory dedup cache from the database.

        Returns:
            Number of entries loaded.
        """
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=self._dedup_window_hours)

        async with get_session(self._engine) as session:
            result = await session.execute(
                select(JobListing).where(JobListing.created_at >= cutoff).order_by(JobListing.created_at.desc())
            )
            rows = result.scalars().all()
            for row in rows:
                key = (row.company.lower().strip(), row.title.lower().strip())
                if key not in self._seen:
                    self._seen[key] = _SeenEntry(
                        company=row.company,
                        title=row.title,
                        source=row.source,
                        discovered_at=row.created_at,
                    )

        self.logger.debug("Pre-loaded dedup cache", entries=len(self._seen))
        return len(self._seen)

    async def _load_search_vectors(self) -> list[SearchVectorConfig]:
        """Load search vectors from ``config/settings.yaml``.

        Returns:
            A list of validated search-vector configs.  Falls back to
            sensible defaults if the YAML overlay is missing or empty.
        """
        from core.config import load_config  # noqa: PLC0415

        overlay = load_config()
        raw_vectors = overlay.get("search_vectors", [])

        if not raw_vectors:
            self.logger.info("No search vectors in config — using built-in defaults")
            return [
                SearchVectorConfig(
                    roles=["senior software engineer", "staff engineer"],
                    keywords=["rust", "python", "distributed systems"],
                    locations=["remote", "san francisco", "new york"],
                    seniority=["senior", "staff"],
                    sources=["greenhouse", "lever"],
                )
            ]

        vectors: list[SearchVectorConfig] = []
        for raw in raw_vectors:
            try:
                vector = SearchVectorConfig(**raw)
                vectors.append(vector)
            except Exception as exc:
                self.logger.warning("Invalid search vector — skipping", error=str(exc))

        return vectors


# ── Module-level helpers ──────────────────────────────────────────────────────


def _parse_datetime(value: str | None) -> datetime.datetime | None:
    """Parse an ISO-8601 or Unix-millis timestamp string into a datetime."""
    if not value:
        return None

    # ISO-8601.
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass

    # Unix milliseconds.
    try:
        ts = int(value)
        return datetime.datetime.fromtimestamp(ts / 1000, tz=datetime.UTC)
    except (ValueError, TypeError, OSError):
        pass

    return None


def _extract_salary(raw_job: dict[str, Any]) -> dict[str, Any] | None:
    """Attempt to extract salary info from a raw job dict.

    Handles Greenhouse's ``"salary"``, Lever's ``"salaryRange"``, and
    free-text compensation fields.
    """
    salary = raw_job.get("salary", raw_job.get("salaryRange", raw_job.get("compensation", {})))

    if isinstance(salary, dict):
        min_val = salary.get("min", salary.get("minimum", salary.get("low")))
        max_val = salary.get("max", salary.get("maximum", salary.get("high")))
        currency = salary.get("currency", salary.get("unit", "USD"))

        if min_val is not None or max_val is not None:
            result: dict[str, Any] = {"currency": currency or "USD"}
            if min_val is not None:
                try:
                    result["min"] = int(float(str(min_val).replace(",", "").replace("$", "")))
                except (ValueError, TypeError):
                    pass
            if max_val is not None:
                try:
                    result["max"] = int(float(str(max_val).replace(",", "").replace("$", "")))
                except (ValueError, TypeError):
                    pass
            return result if ("min" in result or "max" in result) else None

    if isinstance(salary, str):
        # Free-text: "$150k - $200k" etc.
        match = re.search(r"(\d{3,})\s*k?\s*[-–to]+\s*(\d{3,})\s*k?", salary, re.IGNORECASE)
        if match:
            try:
                return {
                    "min": int(match.group(1)) * 1000,
                    "max": int(match.group(2)) * 1000,
                    "currency": "USD",
                }
            except (ValueError, TypeError):
                pass

    return None
