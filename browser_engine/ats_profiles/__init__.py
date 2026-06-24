"""ATS profile handlers for the Browser Execution Engine.

Each supported Applicant Tracking System (ATS) gets its own form-handler
module under this package.  Handlers implement a common :class:`ATSFormHandler`
protocol so the :class:`~browser_engine.form_filler.FormFiller` can dispatch
to the correct implementation after detection.
"""

from __future__ import annotations as _annotations

import enum
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from playwright.async_api import Page as PlaywrightPage

from core.schemas import ProfileCreate

__all__: list[str] = [
    "PROFILE_HANDLER_REGISTRY",
    "ATSFormHandler",
    "ATSProfile",
    "FormFillingProgress",
    "FormFillingResult",
    "get_handler_for_profile",
]

# ── ATS Enum ────────────────────────────────────────────────────────────────────────


class ATSProfile(str, enum.Enum):
    """Well-known Applicant Tracking System types that we can detect and handle."""

    WORKDAY = "workday"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    ASHBY = "ashby"
    BAMBOO = "bamboo"
    SMART_RECRUITERS = "smart_recruiters"
    GENERIC = "generic"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value


# ── Shared result types ─────────────────────────────────────────────────────────────


@dataclass
class FormFillingProgress:
    """Progress update emitted during multi-page form filling."""

    step: str
    """Description of the current step (e.g. "Filling contact info")."""
    step_index: int
    """0-based index of this step within the overall flow."""
    total_steps: int
    """Total number of steps estimated for this form."""
    fields_filled_so_far: list[str] = field(default_factory=list)


@dataclass
class FormFillingResult:
    """Outcome of a single form-filling attempt."""

    success: bool
    """Whether the form was filled (and optionally submitted)."""
    confirmation_screenshot: str | None = None
    """Path to a PNG screenshot captured at the confirmation screen."""
    error: str | None = None
    """Human-readable error description on failure."""
    fields_filled: list[str] = field(default_factory=list)
    """Field labels that were successfully populated."""
    fields_missing: list[str] = field(default_factory=list)
    """Field labels that could not be found or filled."""
    screenshot_paths: list[str] = field(default_factory=list)
    """Paths to screenshots captured at key milestones."""
    ats_profile: ATSProfile | None = None
    """Which ATS profile was used (may be GENERIC for unknown)."""
    submitted: bool = False
    """Whether the form was actually submitted (vs. paused for HITL)."""


# ── Handler protocol ────────────────────────────────────────────────────────────────


class ATSFormHandler(Protocol):
    """Interface that every ATS-specific form handler must satisfy.

    Each handler is responsible for:
      1. Detecting whether the current page belongs to its ATS type.
      2. Navigating the application flow (which may span multiple pages/steps).
      3. Filling form fields by delegating to the shared
         :class:`~browser_engine.form_filler.FormFiller` primitives.
      4. Returning a :class:`FormFillingResult`.
    """

    name: ATSProfile
    """The ATS this handler is designed for."""

    async def detect(self, page: PlaywrightPage) -> bool:
        """Return ``True`` if *page* belongs to this ATS.

        Implementations should check URL patterns, ``<meta>`` tags, DOM
        structure, and form-field naming conventions.
        """
        ...

    async def handle(
        self,
        page: PlaywrightPage,
        profile: ProfileCreate,
        resume_path: str,
        cover_letter_text: str | None = None,
        **kwargs: Any,
    ) -> FormFillingResult:
        """Navigate the application flow and fill all form fields.

        Args:
            page: Playwright page object already pointed at the application URL.
            profile: The user's master profile with personal details, work
                history, and skills.
            resume_path: Absolute or relative path to the tailored resume PDF.
            cover_letter_text: Tailored cover letter plain text (may be ``None``).
            **kwargs: Additional arguments (e.g. ``submit: bool``, ``on_progress``
                callback, ``human_simulator`` instance, ``selector_registry``).

        Returns:
            A :class:`FormFillingResult` summarising what was done.
        """
        ...

    async def emit_progress(
        self,
        progress_cb: Any,
        result: FormFillingResult,
        step: str,
        step_index: int,
        total_steps: int,
    ) -> None:
        """Emit a progress update if a callback was provided."""
        if progress_cb is not None:
            update = FormFillingProgress(
                step=step,
                step_index=step_index,
                total_steps=total_steps,
                fields_filled_so_far=list(result.fields_filled),
            )
            if callable(progress_cb):
                maybe_awaitable = progress_cb(update)
                if hasattr(maybe_awaitable, "__await__"):
                    await maybe_awaitable


# ── Handler registry ────────────────────────────────────────────────────────────────

# Populated at module import time by each handler module.
PROFILE_HANDLER_REGISTRY: dict[ATSProfile, type] = {}
"""Maps each :class:`ATSProfile` value to its handler *class* (not instance)."""


def get_handler_for_profile(profile: ATSProfile) -> type | None:
    """Return the registered handler class for *profile*, or ``None``."""
    return PROFILE_HANDLER_REGISTRY.get(profile)


# ── Handler module imports (side-effect registration) ────────────────────────

# Each handler module registers its class in PROFILE_HANDLER_REGISTRY via
# module-level code.  We import them here (after the registry is defined) so
# those side effects actually execute — otherwise PROFILE_HANDLER_REGISTRY
# stays empty except for GenericFormHandler.
from browser_engine.ats_profiles import (  # noqa: E402  # isort: skip
    generic,
    greenhouse,
    indeed,
    lever,
    linkedin,
    workday,
)
