"""ATS type detection via URL patterns, DOM analysis, and meta tags.

The :class:`ATSDetector` examines a Playwright page and determines which
Applicant Tracking System (if any) it belongs to.  Detection is fast and
heuristic — it runs entirely client-side without LLM calls.
"""

from __future__ import annotations as _annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog
from playwright.async_api import Page

from browser_engine.ats_profiles import ATSProfile

__all__: list[str] = [
    "ATSDetector",
    "DetectionResult",
]

logger = structlog.get_logger(__name__)

# ── Type aliases ────────────────────────────────────────────────────────────────────


@dataclass
class DetectionResult:
    """Result of an ATS detection attempt."""

    profile: ATSProfile
    """The most likely ATS profile."""
    confidence: float = 0.0
    """Confidence score between 0.0 (uncertain) and 1.0 (certain)."""
    version_hints: list[str] = field(default_factory=list)
    """Detected version hints (e.g. ``["2024.3", "R121"]``)."""
    detection_signals: dict[str, Any] = field(default_factory=dict)
    """Raw signals used for the detection (for debugging)."""
    url: str = ""
    """The page URL at detection time."""
    ats_profile: ATSProfile | None = None
    """Alias for the detected profile (deprecated — use ``profile``)."""

    def __post_init__(self) -> None:
        if self.ats_profile is None:
            self.ats_profile = self.profile


# ── URL patterns ────────────────────────────────────────────────────────────────────

_URL_PATTERNS: list[tuple[re.Pattern[str], ATSProfile, float]] = [
    (re.compile(r"myworkdayjobs\.com|wd5\.myworkdaysite\.com|workday\.com"), ATSProfile.WORKDAY, 0.95),
    (re.compile(r"boards\.greenhouse\.io"), ATSProfile.GREENHOUSE, 0.95),
    (re.compile(r"jobs\.lever\.co"), ATSProfile.LEVER, 0.95),
    (re.compile(r"linkedin\.com/jobs"), ATSProfile.LINKEDIN, 0.90),
    (re.compile(r"indeed\.com"), ATSProfile.INDEED, 0.85),
    (re.compile(r"ashbyhq\.com"), ATSProfile.ASHBY, 0.90),
    (re.compile(r"bamboohr\.com"), ATSProfile.BAMBOO, 0.90),
    (re.compile(r"smartrecruiters\.com"), ATSProfile.SMART_RECRUITERS, 0.90),
]

# ── DOM signal patterns ─────────────────────────────────────────────────────────────

_DOM_SIGNATURES: list[tuple[list[str], ATSProfile, float]] = [
    # Workday
    (
        [
            'meta[name="Workday-Page-Context"]',
            '[data-automation-id*="workday"]',
            '[class*="workday"]',
            'script[src*="workday"]',
        ],
        ATSProfile.WORKDAY,
        0.80,
    ),
    # Greenhouse
    (
        [
            '[data-source="greenhouse"]',
            'link[href*="greenhouse"]',
            'meta[name*="greenhouse"]',
            '[class*="greenhouse"]',
        ],
        ATSProfile.GREENHOUSE,
        0.80,
    ),
    # Lever
    (
        [
            'meta[name="lever"]',
            '[data-lever*="application"]',
            '[class*="lever"]',
        ],
        ATSProfile.LEVER,
        0.75,
    ),
    # LinkedIn
    (
        [
            '[data-job-id]',
            '[data-apply-state]',
            '.jobs-apply-form',
            '[class*="jobs-easy-apply"]',
        ],
        ATSProfile.LINKEDIN,
        0.75,
    ),
    # Indeed
    (
        [
            '[data-tn-component]',
            '.icl-ApplyForm',
            '[class*="indeed-apply"]',
            'meta[name="indeed"]',
        ],
        ATSProfile.INDEED,
        0.75,
    ),
    # Ashby
    (
        [
            '[data-ashby]',
            'meta[name="ashby"]',
            'script[src*="ashby"]',
        ],
        ATSProfile.ASHBY,
        0.80,
    ),
    # SmartRecruiters
    (
        [
            'meta[name*="smartrecruiters"]',
            '[class*="smart-recruiters"]',
            'script[src*="smartrecruiters"]',
        ],
        ATSProfile.SMART_RECRUITERS,
        0.80,
    ),
    # BambooHR
    (
        [
            'meta[name*="bamboohr"]',
            '[class*="bamboo-hr"]',
            'script[src*="bamboohr"]',
        ],
        ATSProfile.BAMBOO,
        0.80,
    ),
]

# ── Markup fingerprints ─────────────────────────────────────────────────────────────

_GENERIC_FORM_SIGNALS: list[str] = [
    'form[action*="apply"]',
    'form[action*="application"]',
    'form[action*="career"]',
    'form[id*="apply"]',
    'form[id*="application"]',
    'form[class*="apply"]',
    'form[class*="application"]',
    'input[type="file"]',
    'input[type="email"]',
    'input[type="tel"]',
]

# ── Detector ────────────────────────────────────────────────────────────────────────


class ATSDetector:
    """Detect ATS type from a Playwright page.

    Uses three signal categories in priority order:
      1. URL patterns (fast, high confidence)
      2. DOM meta/attribute signatures (medium confidence)
      3. Form field analysis (lower confidence, used for GENERIC classification)

    Usage::

        detector = ATSDetector()
        result = await detector.detect(page)
        print(f"Detected: {result.profile} (confidence: {result.confidence:.2f})")
    """

    def __init__(self) -> None:
        self._logger = logger.bind(component="ATSDetector")

    async def detect(self, page: Page) -> DetectionResult:
        """Analyse *page* and return the most likely ATS profile.

        Args:
            page: A Playwright page already loaded at the target URL.

        Returns:
            A :class:`DetectionResult` with the best-guess profile and
            confidence score.
        """
        url = page.url
        signals: dict[str, Any] = {"url": url}

        # 1. URL-based detection (fast path).
        url_result = self._check_url(url)
        if url_result is not None:
            profile, confidence = url_result
            signals["url_match"] = profile.value
            self._logger.info(
                "ATS detected via URL pattern",
                profile=profile.value,
                confidence=confidence,
            )
            return DetectionResult(
                profile=profile,
                confidence=confidence,
                url=url,
                detection_signals=signals,
            )

        # 2. DOM signature detection.
        dom_result = await self._check_dom(page)
        if dom_result is not None:
            profile, confidence, dom_signals = dom_result
            signals["dom_signals"] = dom_signals
            self._logger.info(
                "ATS detected via DOM signature",
                profile=profile.value,
                confidence=confidence,
            )
            return DetectionResult(
                profile=profile,
                confidence=confidence,
                url=url,
                detection_signals=signals,
            )

        # 3. Check if this is an application form at all.
        is_form = await self._check_application_form(page)
        signals["is_application_form"] = is_form

        profile = ATSProfile.GENERIC if is_form else ATSProfile.UNKNOWN
        signals["fallback_reason"] = "no_ats_signals_found"

        self._logger.info(
            "No ATS detected — falling back to generic",
            is_application_form=is_form,
        )
        return DetectionResult(
            profile=profile,
            confidence=0.3 if is_form else 0.1,
            url=url,
            detection_signals=signals,
        )

    async def detect_application_form(self, page: Page) -> bool:
        """Return ``True`` if *page* appears to contain a job application form.

        This is a lightweight check that can be run before a full ATS
        detection.
        """
        # Quick URL check for known application paths.
        url = page.url.lower()
        form_paths = ["apply", "application", "careers", "job", "position"]
        if any(p in url for p in form_paths):
            return True

        # DOM check for common form elements.
        try:
            for selector in _GENERIC_FORM_SIGNALS:
                element = await page.query_selector(selector)
                if element is not None:
                    return True
        except Exception:
            pass

        return False

    # ── Internal detection methods ──────────────────────────────────────────────────

    def _check_url(self, url: str) -> tuple[ATSProfile, float] | None:
        """Match the URL against known ATS patterns."""
        for pattern, profile, confidence in _URL_PATTERNS:
            if pattern.search(url):
                return profile, confidence
        return None

    async def _check_dom(self, page: Page) -> tuple[ATSProfile, float, dict[str, Any]] | None:
        """Match DOM elements against known ATS signatures."""
        for selectors, profile, base_confidence in _DOM_SIGNATURES:
            matched_signals: dict[str, Any] = {}
            match_count = 0

            for sel in selectors:
                try:
                    element = await page.query_selector(sel)
                    if element is not None:
                        matched_signals[sel] = True
                        match_count += 1
                except Exception:
                    continue

            if match_count > 0:
                # Boost confidence with more matches, capped at 0.95.
                boost = min(match_count * 0.05, 0.15)
                effective_confidence = min(base_confidence + boost, 0.95)

                # Collect version hints from meta tags / script sources.
                version_hints = await self._extract_version_hints(page, profile)

                return profile, effective_confidence, {
                    "matched_selectors": list(matched_signals.keys()),
                    "match_count": match_count,
                    "version_hints": version_hints,
                }

        return None

    async def _extract_version_hints(self, page: Page, profile: ATSProfile) -> list[str]:
        """Extract version information from page metadata."""
        hints: list[str] = []

        try:
            if profile == ATSProfile.WORKDAY:
                # Workday often embeds version info in script sources.
                scripts = await page.query_selector_all('script[src*="workday"]')
                for script in scripts:
                    src = await script.get_attribute("src")
                    if src:
                        version_match = re.search(r"v(\d+[._]\d+)", src)
                        if version_match:
                            hints.append(f"workday-{version_match.group(1)}")

            elif profile == ATSProfile.GREENHOUSE:
                meta = await page.query_selector('meta[name="greenhouse-version"]')
                if meta:
                    content = await meta.get_attribute("content")
                    if content:
                        hints.append(f"greenhouse-{content}")

            elif profile == ATSProfile.LEVER:
                meta = await page.query_selector('meta[name="lever-version"]')
                if meta:
                    content = await meta.get_attribute("content")
                    if content:
                        hints.append(f"lever-{content}")
        except Exception:
            pass

        return hints
