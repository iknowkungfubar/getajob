"""Orchestrated form-filling engine for job applications.

The :class:`FormFiller` is the central coordinator of the Browser Execution
Engine.  It:

1. Detects the ATS type via :class:`~browser_engine.ats_detector.ATSDetector`.
2. Looks up the appropriate handler from
   :data:`~browser_engine.ats_profiles.PROFILE_HANDLER_REGISTRY`.
3. Delegates to the handler or falls back to the generic handler.

All field-level interactions (typing, clicking, file upload) are routed
through :class:`~browser_engine.human_simulator.HumanSimulator` for
human-like timing.
"""

from __future__ import annotations as _annotations

import os
from collections.abc import Callable, Coroutine
from typing import Any

import structlog
from playwright.async_api import Page

from browser_engine.ats_detector import ATSDetector
from browser_engine.ats_profiles import (
    PROFILE_HANDLER_REGISTRY,
    ATSFormHandler,
    FormFillingProgress,
    FormFillingResult,
)
from browser_engine.human_simulator import HumanSimulator
from browser_engine.selectors import SelectorRegistry, dynamic_select
from core.config import get_settings
from core.exceptions import BrowserError
from core.schemas import ProfileCreate

__all__: list[str] = [
    "FormFiller",
]

logger = structlog.get_logger(__name__)


# ── FormFiller ──────────────────────────────────────────────────────────────────────


class FormFiller:
    """Coordinator for automated job-application form filling.

    Usage::

        filler = FormFiller(page, human_simulator)
        result = await filler.fill_form(
            profile=profile_data,
            resume_path="/path/to/resume.pdf",
            cover_letter_text="Dear hiring team...",
            submit=False,  # Pause for HITL.
        )
    """

    def __init__(
        self,
        page: Page,
        human_simulator: HumanSimulator | None = None,
        selector_registry: SelectorRegistry | None = None,
    ) -> None:
        self._page = page
        self._human = human_simulator or HumanSimulator()
        self._selectors = selector_registry or SelectorRegistry()
        self._detector = ATSDetector()
        self._logger = logger.bind(component="FormFiller")

    # ── Main entry point ───────────────────────────────────────────────────────────

    async def fill_form(
        self,
        profile: ProfileCreate,
        resume_path: str,
        cover_letter_text: str | None = None,
        *,
        submit: bool = False,
        on_progress: Callable[[FormFillingProgress], Coroutine[Any, Any, None] | None]
        | None = None,
    ) -> FormFillingResult:
        """Fill a job application form using the best available handler.

        Steps:
          1. Detect the ATS type from the current page.
          2. Look up a registered handler for that ATS.
          3. If no handler is registered, use the generic fallback.
          4. Return the filled-form result.

        Args:
            profile: The user's master profile.
            resume_path: Path to the tailored resume PDF.
            cover_letter_text: Plain-text cover letter (optional).
            submit: If ``True``, attempt to submit the form after filling.
                If ``False``, pause at the final review page for HITL.
            on_progress: Optional async callback receiving
                :class:`FormFillingProgress` updates during multi-page flows.

        Returns:
            A :class:`FormFillingResult` describing the outcome.
        """
        resume_path = os.path.abspath(resume_path)
        if not os.path.isfile(resume_path):
            msg = f"Resume file not found: {resume_path}"
            raise BrowserError(msg)

        # 1. Detect the ATS.
        detection = await self._detector.detect(self._page)
        self._logger.info(
            "ATS detection result",
            profile=detection.profile.value,
            confidence=round(detection.confidence, 2),
        )

        # 2. Look up handler.
        handler_cls = PROFILE_HANDLER_REGISTRY.get(detection.profile)

        if handler_cls is not None:
            # Instantiate the handler and delegate.
            handler: ATSFormHandler = handler_cls()  # type: ignore[call-arg]
            self._logger.info(
                "Delegating to ATS handler",
                handler=handler_cls.__name__,
                profile=detection.profile.value,
            )

            # Inject shared utilities into kwargs.
            kwargs: dict[str, Any] = {
                "human_simulator": self._human,
                "selector_registry": self._selectors,
                "submit": submit,
                "on_progress": on_progress,
            }

            result = await handler.handle(
                page=self._page,
                profile=profile,
                resume_path=resume_path,
                cover_letter_text=cover_letter_text,
                **kwargs,
            )
        else:
            # 3. No handler registered — use generic fallback.
            self._logger.warning(
                "No registered handler for ATS — using generic fallback",
                profile=detection.profile.value,
            )
            from browser_engine.ats_profiles.generic import GenericFormHandler

            handler = GenericFormHandler()
            kwargs = {
                "human_simulator": self._human,
                "selector_registry": self._selectors,
                "submit": submit,
                "on_progress": on_progress,
            }
            result = await handler.handle(
                page=self._page,
                profile=profile,
                resume_path=resume_path,
                cover_letter_text=cover_letter_text,
                **kwargs,
            )

        # Attach the detected ATS profile for reference.
        result.ats_profile = detection.profile
        return result

    # ── Field-level helpers (used by profile handlers) ──────────────────────────────

    async def fill_text_field(
        self,
        field_name: str,
        value: str,
        *,
        selector: str | None = None,
    ) -> bool:
        """Find a text/email field by semantic name and type the value.

        Args:
            field_name: Semantic field type (e.g. ``"email_input"``).
            value: The text to enter.
            selector: Optional explicit CSS selector (skips registry lookup).

        Returns:
            ``True`` if the field was found and filled.
        """
        if selector:
            element = await self._page.query_selector(selector)
        else:
            element = await self._selectors.find_field(self._page, field_name)

        if element is None:
            # Fallback: dynamic text-label scan.
            element = await dynamic_select(self._page, field_name, context=value)

        if element is None:
            self._logger.debug("Field not found", field_name=field_name)
            return False

        await self._human.human_type(self._page, element, value)
        return True

    async def handle_file_upload(
        self,
        field_name: str,
        file_path: str,
        *,
        selector: str | None = None,
    ) -> bool:
        """Upload a file to a file-input field.

        Args:
            field_name: Semantic field type (e.g. ``"resume_upload"``).
            file_path: Absolute path to the file.
            selector: Optional explicit selector.

        Returns:
            ``True`` if the file was uploaded successfully.
        """
        resolved_path = os.path.abspath(file_path)
        if not os.path.isfile(resolved_path):
            self._logger.error("File not found for upload", path=resolved_path)
            return False

        if selector:
            element = await self._page.query_selector(selector)
        else:
            element = await self._selectors.find_field(self._page, field_name)

        if element is None:
            element = await dynamic_select(self._page, field_name)

        if element is None:
            self._logger.warning("File-upload field not found", field_name=field_name)
            return False

        # Click the upload element to simulate human interaction.
        await self._human.click_element(self._page, element)
        await self._human.random_delay(200, 400)

        # Playwright resolves relative paths from the CWD — use absolute path.
        await element.set_input_files(resolved_path)
        await self._human.sleep_between_actions()

        self._logger.info("File uploaded", field=field_name, file=resolved_path)
        return True

    async def handle_dropdown(
        self, field_name: str, value: str, *, selector: str | None = None
    ) -> bool:
        """Select an option from a ``<select>`` dropdown.

        Args:
            field_name: Semantic field type (e.g. ``"work_authorization"``).
            value: The option value or visible text to select.
            selector: Optional explicit selector.

        Returns:
            ``True`` if the selection was made.
        """
        if selector:
            element = await self._page.query_selector(selector)
        else:
            element = await self._selectors.find_field(self._page, field_name)

        if element is None:
            element = await dynamic_select(self._page, field_name)

        if element is None:
            self._logger.warning("Dropdown not found", field_name=field_name)
            return False

        await self._human.click_element(self._page, element)
        await self._human.random_delay(100, 200)

        await element.select_option(label=value)
        await self._human.random_delay(50, 150)

        self._logger.info("Dropdown selected", field=field_name, value=value)
        return True

    async def handle_checkbox(
        self, field_name: str, checked: bool = True, *, selector: str | None = None
    ) -> bool:
        """Check or un-check a checkbox (or radio button).

        Args:
            field_name: Semantic field type.
            checked: Desired state.
            selector: Optional explicit selector.

        Returns:
            ``True`` if the checkbox was found and toggled.
        """
        if selector:
            element = await self._page.query_selector(selector)
        else:
            element = await self._selectors.find_field(self._page, field_name)

        if element is None:
            element = await dynamic_select(self._page, field_name)

        if element is None:
            self._logger.warning("Checkbox not found", field_name=field_name)
            return False

        is_checked = await element.is_checked()
        if is_checked != checked:
            await self._human.click_element(self._page, element)
            self._logger.debug("Checkbox toggled", field=field_name, checked=checked)

        return True

    async def handle_date_field(
        self, field_name: str, date_str: str, *, selector: str | None = None
    ) -> bool:
        """Fill a date input field.

        Args:
            field_name: Semantic field type.
            date_str: Date string in ``YYYY-MM-DD`` format (the HTML5 standard).
            selector: Optional explicit selector.

        Returns:
            ``True`` if the field was filled.
        """
        if selector:
            element = await self._page.query_selector(selector)
        else:
            element = await self._selectors.find_field(self._page, field_name)

        if element is None:
            element = await dynamic_select(self._page, field_name)

        if element is None:
            self._logger.warning("Date field not found", field_name=field_name)
            return False

        # Clear and fill.
        await element.click(click_count=3)
        await self._page.keyboard.press("Backspace")
        await self._human.random_delay(30, 70)
        await self._human.human_type(self._page, element, date_str)

        return True

    async def submit_application(
        self, *, wait_for_confirmation_s: float = 10.0
    ) -> FormFillingResult:
        """Click the submit button and wait for a confirmation signal.

        Args:
            wait_for_confirmation_s: How long to wait for a confirmation
                element to appear after clicking.

        Returns:
            A ``FormFillingResult`` with the outcome.
        """
        result = FormFillingResult(success=False)

        # Find the submit button.
        submit_btn = await self._selectors.find_field(self._page, "submit_button")
        if submit_btn is None:
            submit_btn = await dynamic_select(self._page, "submit_button")

        if submit_btn is None:
            result.error = "Submit button not found"
            self._logger.error("Submit button not found")
            return result

        # Scroll into view and click with human-like movement.
        await self._human.random_scroll(self._page)
        await self._human.sleep_between_actions()
        await self._human.click_element(self._page, submit_btn)

        # Wait for navigation or confirmation.
        try:
            await self._page.wait_for_url(
                lambda url: "confirmation" in url.lower()
                or "thank" in url.lower()
                or "success" in url.lower(),
                timeout=int(wait_for_confirmation_s * 1000),
            )
        except Exception:
            # Also check for visible confirmation text.
            try:
                await self._page.wait_for_selector(
                    "text=Thank you,:text('Application submitted'),:text('Application received')",
                    timeout=int(wait_for_confirmation_s * 1000),
                )
            except Exception:
                self._logger.warning("No confirmation signal detected — submission may have failed")
                result.error = "No confirmation signal detected"
                return result

        result.success = True
        result.submitted = True
        self._logger.info("Application submitted successfully")
        return result

    # ── HITL gate ───────────────────────────────────────────────────────────────────

    async def wait_for_human_approval(self, timeout_minutes: int = 60) -> bool:
        """Pause execution and wait for a human to approve the submission.

        This method polls for a ``.approval-gate`` file to be written to the
        data directory, allowing the HITL web UI to signal approval.

        Args:
            timeout_minutes: Maximum time to wait before timing out.

        Returns:
            ``True`` if the human approved, ``False`` on timeout.
        """
        settings = get_settings()
        gate_path = settings.data_dir / ".approval-gate"

        # Remove any stale gate.
        gate_path.unlink(missing_ok=True)

        self._logger.info("Waiting for human approval (HITL gate)", timeout_minutes=timeout_minutes)

        import asyncio

        poll_interval = 2.0  # seconds
        max_polls = int((timeout_minutes * 60) / poll_interval)

        for _ in range(max_polls):
            if gate_path.exists():
                content = gate_path.read_text().strip()
                gate_path.unlink(missing_ok=True)
                approved = content.lower() == "approved"
                self._logger.info("HITL decision received", approved=approved)
                return approved
            await asyncio.sleep(poll_interval)

        self._logger.warning("HITL approval timed out")
        return False
