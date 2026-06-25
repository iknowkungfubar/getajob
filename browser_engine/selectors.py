"""Dynamic selector strategies for form-field discovery.

The :class:`SelectorRegistry` manages a knowledge base of CSS/XPath selectors
for common form field types (name, email, phone, resume upload, etc.).  When
the primary selector fails, it falls back through a priority-ordered list of
alternatives and, as a last resort, uses vision/LLM-based discovery via
:func:`dynamic_select`.
"""

from __future__ import annotations as _annotations

import re
from dataclasses import dataclass, field

import structlog
from playwright.async_api import ElementHandle, Page

__all__: list[str] = [
    "KNOWN_FIELD_TYPES",
    "SelectorRegistry",
    "SelectorStrategy",
]

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SelectorStrategy:
    """A named selector strategy with primary + fallback patterns.

    Attributes:
        name: Semantic field name (e.g. ``"name_input"``).
        primary: Primary CSS selector.
        fallbacks: Ordered list of alternative CSS/XPath selectors.
        selector_type: ``"css"`` (default) or ``"xpath"``.
    """

    name: str
    primary: str
    fallbacks: list[str] = field(default_factory=list)
    selector_type: str = "css"


# ── Known field types and their selectors ──────────────────────────────────────────

KNOWN_FIELD_TYPES: dict[str, SelectorStrategy] = {
    "name_input": SelectorStrategy(
        name="name_input",
        primary='input[type="text"][name*="name"], input[name*="name"]',
        fallbacks=[
            'input[id*="name"]',
            'input[placeholder*="name" i]',
            'input[aria-label*="name" i]',
            'input[autocomplete="name"]',
            'input[name*="full_name"], input[name*="fullName"]',
            'input[name*="first"][name*="last"]',
            'input:not([type="hidden"])[class*="name"]',
            # XPath fallbacks
            "xpath://input[contains(translate(@placeholder,'NAME','name'),'name')]",
            "xpath://input[contains(translate(@aria-label,'NAME','name'),'name')]",
        ],
    ),
    "email_input": SelectorStrategy(
        name="email_input",
        primary='input[type="email"]',
        fallbacks=[
            'input[name*="email"]',
            'input[id*="email"]',
            'input[autocomplete="email"]',
            'input[placeholder*="email" i]',
            'input[aria-label*="email" i]',
            'input[name*="e-mail"], input[name*="emailAddress"]',
            "xpath://input[contains(translate(@placeholder,'EMAIL','email'),'email')]",
        ],
    ),
    "phone_input": SelectorStrategy(
        name="phone_input",
        primary='input[type="tel"]',
        fallbacks=[
            'input[name*="phone"]',
            'input[id*="phone"]',
            'input[autocomplete="tel"]',
            'input[placeholder*="phone" i]',
            'input[aria-label*="phone" i]',
            'input[name*="mobile"], input[name*="phoneNumber"]',
        ],
    ),
    "resume_upload": SelectorStrategy(
        name="resume_upload",
        primary='input[type="file"][accept*="pdf"], input[type="file"][accept*="resume"]',
        fallbacks=[
            'input[type="file"][name*="resume"]',
            'input[type="file"][id*="resume"]',
            'input[type="file"][name*="file"]',
            'input[type="file"]',
            'button:has-text("Upload Resume"), button:has-text("Upload CV")',
            'a:has-text("Upload Resume")',
            'div[class*="upload"] input[type="file"]',
            "xpath://input[@type='file']",
        ],
    ),
    "cover_letter_field": SelectorStrategy(
        name="cover_letter_field",
        primary='textarea[name*="cover"], textarea[id*="cover"]',
        fallbacks=[
            'textarea[placeholder*="cover" i]',
            'textarea[aria-label*="cover" i]',
            'textarea[name*="letter"], textarea[id*="letter"]',
            'div[contenteditable="true"][class*="cover"]',
            'textarea:not([type="hidden"])',
            "xpath://textarea[contains(translate(@placeholder,'COVER','cover'),'cover')]",
        ],
    ),
    "submit_button": SelectorStrategy(
        name="submit_button",
        primary='button[type="submit"]',
        fallbacks=[
            'input[type="submit"]',
            'button:has-text("Submit")',
            'button:has-text("Apply")',
            'button:has-text("Send Application")',
            'button:has-text("Next"), button:has-text("Continue")',
            'button:has-text("Review"), button:has-text("Review Application")',
            'a:has-text("Submit Application")',
            'div[role="button"]:has-text("Submit")',
            "xpath://button[contains(translate(.,'SUBMIT','submit'),'submit')]",
            "xpath://button[contains(translate(.,'APPLY','apply'),'apply')]",
        ],
    ),
    "linkedin_import": SelectorStrategy(
        name="linkedin_import",
        primary='button:has-text("Import from LinkedIn")',
        fallbacks=[
            'button:has-text("LinkedIn")',
            'a:has-text("Import from LinkedIn")',
            'button[class*="linkedin"]',
            'img[alt*="linkedin" i]',
        ],
    ),
    "work_authorization": SelectorStrategy(
        name="work_authorization",
        primary='select[name*="work" i], select[id*="work" i]',
        fallbacks=[
            'select[aria-label*="work" i]',
            'fieldset:has(legend:has-text("Work Authorization")) select',
            'div:has(label:has-text("Work Authorization")) select',
            'select[name*="visa"], select[id*="visa"]',
            'select[name*="authorization"], select[id*="authorization"]',
        ],
    ),
    "location_input": SelectorStrategy(
        name="location_input",
        primary='input[name*="location"]',
        fallbacks=[
            'input[id*="location"]',
            'input[placeholder*="location" i]',
            'input[aria-label*="location" i]',
            'input[name*="city"], input[name*="address"]',
        ],
    ),
    "linkedin_url_input": SelectorStrategy(
        name="linkedin_url_input",
        primary='input[name*="linkedin"], input[id*="linkedin"]',
        fallbacks=[
            'input[placeholder*="linkedin" i]',
            'input[aria-label*="linkedin" i]',
            'input[name*="url"][class*="linkedin"]',
        ],
    ),
    "portfolio_url_input": SelectorStrategy(
        name="portfolio_url_input",
        primary='input[name*="portfolio"], input[name*="website"]',
        fallbacks=[
            'input[placeholder*="portfolio" i]',
            'input[placeholder*="website" i]',
            'input[aria-label*="portfolio" i]',
        ],
    ),
    "education_section": SelectorStrategy(
        name="education_section",
        primary='section[class*="education"], fieldset[class*="education"]',
        fallbacks=[
            'div[id*="education"]',
            'div[class*="education"]',
            'h2:has-text("Education"), h3:has-text("Education")',
            'legend:has-text("Education")',
        ],
    ),
    "experience_section": SelectorStrategy(
        name="experience_section",
        primary='section[class*="experience"], fieldset[class*="experience"]',
        fallbacks=[
            'div[id*="experience"]',
            'div[class*="experience"]',
            'h2:has-text("Experience"), h3:has-text("Experience")',
            'legend:has-text("Experience")',
            'h2:has-text("Work History"), h3:has-text("Work History")',
        ],
    ),
    "skills_section": SelectorStrategy(
        name="skills_section",
        primary='section[class*="skills"], fieldset[class*="skills"]',
        fallbacks=[
            'div[id*="skills"]',
            'div[class*="skills"]',
            'h2:has-text("Skills"), h3:has-text("Skills")',
            'legend:has-text("Skills")',
            'textarea[name*="skills"], textarea[id*="skills"]',
        ],
    ),
    "salary_expectation": SelectorStrategy(
        name="salary_expectation",
        primary='input[name*="salary"]',
        fallbacks=[
            'input[id*="salary"]',
            'input[placeholder*="salary" i]',
            'input[aria-label*="salary" i]',
            'input[name*="compensation"], input[name*="pay"]',
        ],
    ),
    "race_ethnicity": SelectorStrategy(
        name="race_ethnicity",
        primary='select[name*="race" i], select[id*="race" i]',
        fallbacks=[
            'fieldset:has(legend:has-text("Race")) select',
            'fieldset:has(legend:has-text("Ethnicity")) select',
            'div:has(label:has-text("Race")) select',
            'div:has(label:has-text("Ethnicity")) select',
        ],
    ),
    "gender": SelectorStrategy(
        name="gender",
        primary='select[name*="gender" i], select[id*="gender" i]',
        fallbacks=[
            'fieldset:has(legend:has-text("Gender")) select',
            'div:has(label:has-text("Gender")) select',
            'input[type="radio"][name*="gender"]',
        ],
    ),
    "veteran_status": SelectorStrategy(
        name="veteran_status",
        primary='select[name*="veteran" i]',
        fallbacks=[
            'fieldset:has(legend:has-text("Veteran")) select',
            'div:has(label:has-text("Veteran")) select',
            'input[type="radio"][name*="veteran"]',
        ],
    ),
    "disability": SelectorStrategy(
        name="disability",
        primary='select[name*="disabilit" i]',
        fallbacks=[
            'fieldset:has(legend:has-text("Disability")) select',
            'div:has(label:has-text("Disability")) select',
            'input[type="radio"][name*="disabilit"]',
        ],
    ),
}


# ── SelectorRegistry ────────────────────────────────────────────────────────────────


class SelectorRegistry:
    """Registry of known form-field selectors with fallback chaining.

    Usage::

        registry = SelectorRegistry()
        field_selector = registry.get("email_input")
        element = await registry.find_field(page, "email_input")
    """

    def __init__(self) -> None:
        self._strategies: dict[str, SelectorStrategy] = dict(KNOWN_FIELD_TYPES)
        self._logger = logger.bind(component="SelectorRegistry")

    def register(self, name: str, strategy: SelectorStrategy) -> None:
        """Register (or replace) a selector strategy.

        Args:
            name: Semantic field name.
            strategy: The :class:`SelectorStrategy` to associate.
        """
        self._strategies[name] = strategy
        self._logger.debug("Strategy registered", name=name)

    def get(self, name: str) -> SelectorStrategy | None:
        """Return the registered strategy for *name*, or ``None``."""
        return self._strategies.get(name)

    def has(self, name: str) -> bool:
        """Return ``True`` if a strategy is registered for *name*."""
        return name in self._strategies

    async def find_field(self, page: Page, field_type: str) -> ElementHandle | None:
        """Attempt to locate a form field by trying the registered selectors.

        The primary selector is tried first, then each fallback in order.
        Returns the first matching element, or ``None`` if no selector matched.

        Args:
            page: Playwright page to search within.
            field_type: Semantic field type key (e.g. ``"email_input"``).

        Returns:
            The matched element, or ``None``.
        """
        strategy = self._strategies.get(field_type)
        if strategy is None:
            self._logger.warning("Unknown field type", field_type=field_type)
            return None

        all_selectors = [strategy.primary, *strategy.fallbacks]

        for selector in all_selectors:
            try:
                is_xpath = selector.startswith("xpath:")
                query = selector[6:] if is_xpath else selector

                if is_xpath:
                    element = await page.query_selector(f"xpath={query}")
                else:
                    element = await page.query_selector(query)

                if element is not None:
                    self._logger.debug(
                        "Field located",
                        field_type=field_type,
                        selector=selector[:80],
                    )
                    return element
            except Exception as exc:
                self._logger.debug(
                    "Selector failed",
                    field_type=field_type,
                    selector=selector[:80],
                    error=str(exc)[:100],
                )

        self._logger.debug("Field not found with any selector", field_type=field_type)
        return None

    async def find_all_fields(self, page: Page, field_type: str) -> list[ElementHandle]:
        """Find all elements matching a field type (for multi-instance fields).

        Useful for education / experience sections where there may be
        multiple entries.  Uses the primary selector only (fallbacks are
        designed for single-field matching).
        """
        strategy = self._strategies.get(field_type)
        if strategy is None:
            return []

        try:
            elements = await page.query_selector_all(strategy.primary)
            if elements:
                return elements
        except Exception:
            pass

        # Try fallbacks.
        for fb in strategy.fallbacks:
            try:
                if fb.startswith("xpath:"):
                    elements = await page.query_selector_all(f"xpath={fb[6:]}")
                else:
                    elements = await page.query_selector_all(fb)
                if elements:
                    return elements
            except Exception:
                continue

        return []


# ── Dynamic / vision-based discovery ────────────────────────────────────────────────

_SEMANTIC_FIELD_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "name_input": [
        re.compile(p, re.IGNORECASE)
        for p in [r"name", r"full.?name", r"first.?name", r"last.?name", r"your\s*name"]
    ],
    "email_input": [re.compile(p, re.IGNORECASE) for p in [r"email", r"e-?mail", r"your\s*email"]],
    "phone_input": [
        re.compile(p, re.IGNORECASE) for p in [r"phone", r"mobile", r"telephone", r"phone\s*number"]
    ],
    "resume_upload": [
        re.compile(p, re.IGNORECASE)
        for p in [r"resume", r"cv", r"upload\s*resume", r"attach\s*resume"]
    ],
    "cover_letter_field": [
        re.compile(p, re.IGNORECASE) for p in [r"cover\s*letter", r"cover\s*note"]
    ],
    "submit_button": [
        re.compile(p, re.IGNORECASE) for p in [r"submit", r"apply", r"send\s*application"]
    ],
    "linkedin_url_input": [
        re.compile(p, re.IGNORECASE)
        for p in [r"linkedin", r"linkedin\s*url", r"linkedin\s*profile"]
    ],
    "work_authorization": [
        re.compile(p, re.IGNORECASE)
        for p in [r"work\s*authorization", r"visa", r"work\s*status", r"sponsorship"]
    ],
}


async def dynamic_select(
    page: Page, field_hint: str, *, _context: str | None = None
) -> ElementHandle | None:
    """Use text-label analysis to find a form field by semantic meaning.

    When CSS selectors fail, this function scans the page for visible labels
    and nearby ``<input>`` / ``<textarea>`` / ``<select>`` elements whose
    associated text matches *field_hint*.

    Args:
        page: Playwright page.
        field_hint: Semantic field type (e.g. ``"email_input"``).
        context: Optional extra context for LLM-based discovery (unused in
            the heuristic fallback; reserved for future use).

    Returns:
        The first matching element, or ``None``.
    """
    patterns = _SEMANTIC_FIELD_PATTERNS.get(field_hint)
    if patterns is None:
        return None

    # Strategy: find <label> elements whose text matches, then get the
    # associated input via ``for`` attribute or nesting.
    labels = await page.query_selector_all("label")
    for label in labels:
        try:
            text = await label.inner_text()
        except Exception:
            continue

        if any(p.search(text) for p in patterns):
            # Try ``for`` attribute.
            for_attr = await label.get_attribute("for")
            if for_attr:
                associated = await page.query_selector(f"#{for_attr}")
                if associated is not None:
                    return associated

            # Try nested input.
            nested = await label.query_selector("input, textarea, select")
            if nested is not None:
                return nested

    # Fallback: scan all input/textarea elements and check nearby text.
    inputs = await page.query_selector_all(
        "input:not([type='hidden']):not([type='submit']), textarea, select"
    )
    await page.inner_text("body")

    # If no label match succeeded, return the first input whose placeholder
    # or aria-label matches.
    for el in inputs:
        placeholder = await el.get_attribute("placeholder") or ""
        aria_label = await el.get_attribute("aria-label") or ""
        combined = f"{placeholder} {aria_label}"
        if any(p.search(combined) for p in patterns):
            return el

    return None
