"""Job source protocol and implementations for getajob discovery.

Extracted from IngestionAgent to create independently testable
discovery modules per source type.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from core.schemas import JobListingCreate

logger = logging.getLogger("getajob.sources")


class JobSource(Protocol):
    """Protocol for job discovery sources."""

    name: str
    """Source identifier (e.g. 'greenhouse', 'lever', 'generic')."""

    async def discover(self, config: dict[str, Any]) -> list[JobListingCreate]:
        """Discover job listings from this source based on config."""
        ...


class GreenhouseSource:
    """Job discovery via Greenhouse ATS API."""

    name = "greenhouse"

    async def discover(self, config: dict[str, Any]) -> list[JobListingCreate]:
        """Fetch jobs from Greenhouse for a company."""
        import httpx

        company = config.get("company", "")
        board_token = config.get("board_token", "")
        if not company:
            logger.warning("Greenhouse source missing company name")
            return []

        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        params: dict[str, Any] = {"content": "true"}
        if board_token:
            params["board_token"] = board_token

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                jobs = data.get("jobs", [])
                return [
                    JobListingCreate(
                        title=job.get("title", "Unknown"),
                        company=company,
                        url=job.get("absolute_url", ""),
                        source="greenhouse",
                        description_json={"text": job.get("content", "")},
                        source_id=str(job.get("id", "")),
                    )
                    for job in jobs
                ]
            except Exception as e:
                logger.error("Greenhouse fetch failed for %s: %s", company, e)
                return []


class LeverSource:
    """Job discovery via Lever ATS API."""

    name = "lever"

    async def discover(self, config: dict[str, Any]) -> list[JobListingCreate]:
        """Fetch jobs from Lever for a company."""
        import httpx

        company = config.get("company", "")
        if not company:
            logger.warning("Lever source missing company name")
            return []

        url = f"https://api.lever.co/v0/postings/{company}?mode=json"
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                postings = resp.json()
                return [
                    JobListingCreate(
                        title=p.get("text", "Unknown"),
                        company=company,
                        url=p.get("hostedUrl", ""),
                        source="lever",
                        description_json={"text": p.get("descriptionText", "")},
                        source_id=p.get("id"),
                    )
                    for p in postings
                    if isinstance(p, dict)
                ]
            except Exception as e:
                logger.error("Lever fetch failed for %s: %s", company, e)
                return []


class GenericBoardSource:
    """Generic job board discovery (generic API integration)."""

    name = "generic"

    async def discover(self, config: dict[str, Any]) -> list[JobListingCreate]:
        """Generic source discovery — override for custom boards."""

        logger.info("Generic source running with config: %s", config.get("name", "unnamed"))
        return []
