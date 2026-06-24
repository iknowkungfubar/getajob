"""Lever ATS form handler.

Lever forms use a distinctive two-panel layout: a left navigation panel and a
right content panel.  The application is typically a single scrolling page with
clearly labelled sections rather than true multi-page navigation.
"""

from __future__ import annotations as _annotations

from typing import Any

import structlog
from playwright.async_api import Page

from browser_engine.ats_profiles import (
    PROFILE_HANDLER_REGISTRY,
    ATSProfile,
    FormFillingResult,
)

__all__: list[str] = [
    "LeverFormHandler",
]

logger = structlog.get_logger(__name__)

# ── Known Lever selectors ───────────────────────────────────────────────────────────

_LEVER_SELECTORS: dict[str, str] = {
    # Core fields
    "name": 'input[name="name"], input[placeholder*="Full name"]',
    "email": 'input[name="email"], input[type="email"]',
    "phone": 'input[name="phone"], input[type="tel"]',
    "resume": 'input[type="file"][accept*="pdf"], input[type="file"][accept*="doc"]',
    "cover_letter": 'textarea[name*="cover"], textarea[placeholder*="cover" i]',
    "linkedin": 'input[name*="linkedin"], input[placeholder*="LinkedIn"]',
    "website": 'input[name*="url"], input[placeholder*="Portfolio"], input[placeholder*="website"]',
    "location": 'input[name*="location"], input[placeholder*="location"]',
    "work_authorization": 'select[name*="work_authorization"], select[name*="visa"]',
    # Buttons
    "submit": 'button[type="submit"], button:has-text("Submit application"), button:has-text("Submit")',
    "next": 'button:has-text("Next"), button[aria-label*="Next"]',
    "review": 'button:has-text("Review")',
    "back": 'button:has-text("Back")',
    "add_another": 'button:has-text("Add another")',
    # Section headings
    "contact_section": 'h2:has-text("Contact"), h3:has-text("Contact")',
    "resume_section": 'h2:has-text("Resume"), h3:has-text("Resume")',
    "links_section": 'h2:has-text("Links"), h3:has-text("Links")',
    "eeo_section": 'h2:has-text("Demographics"), h3:has-text("Equal Opportunity")',
}


class LeverFormHandler:
    """Form handler for Lever ATS (jobs.lever.co).

    Lever forms are single-page scrolling forms with sections.  The handler
    scrolls through the page, fills each visible section, and submits.
    """

    name = ATSProfile.LEVER

    def __init__(self) -> None:
        self._logger = logger.bind(component="LeverFormHandler")

    async def detect(self, page: Page) -> bool:
        """Return ``True`` if the page is a Lever application form."""
        url = page.url.lower()
        if "jobs.lever.co" in url and "/apply" in url:
            return True

        # Check for Lever-specific DOM signals.
        try:
            meta = await page.query_selector('meta[name="lever"]')
            if meta is not None:
                return True
        except Exception:
            pass

        # Check for the distinctive Lever two-panel structure.
        try:
            left_panel = await page.query_selector('[class*="application"], [class*="form-wrapper"]')
            if left_panel is not None:
                # Lever forms typically have a visible h2 with "Contact" or "Apply".
                heading = await page.query_selector(
                    'h2:has-text("Apply"), h2:has-text("Contact"), h2:has-text("Resume")'
                )
                if heading is not None:
                    return True
        except Exception:
            pass

        return False

    async def handle(  # type: ignore[override]
        self,
        page: Page,
        profile: Any,
        resume_path: str,
        cover_letter_text: str | None = None,
        **kwargs: Any,
    ) -> FormFillingResult:
        """Navigate the Lever application form and fill all fields.

        Args:
            page: Playwright page at the Lever job application URL.
            profile: User profile.
            resume_path: Path to the resume PDF.
            cover_letter_text: Optional cover letter text.
            **kwargs: May include ``human_simulator``, ``selector_registry``,
                ``submit``, and ``on_progress``.

        Returns:
            A :class:`FormFillingResult`.
        """
        from browser_engine.form_filler import FormFiller
        from browser_engine.human_simulator import HumanSimulator
        from browser_engine.selectors import SelectorRegistry

        human: HumanSimulator = kwargs.get("human_simulator", HumanSimulator())
        selectors: SelectorRegistry = kwargs.get("selector_registry", SelectorRegistry())
        submit: bool = kwargs.get("submit", False)
        on_progress = kwargs.get("on_progress")

        filler = FormFiller(page, human, selectors)
        result = FormFillingResult(success=False)

        try:
            # Step 1: Contact information.
            await self._emit(on_progress, result, "Filling contact information", 0, 5)
            await human.random_scroll(page)
            await human.sleep_between_actions()

            # Name field (Lever uses a single "name" input).
            name_el = await page.query_selector(_LEVER_SELECTORS["name"])
            if name_el is not None:
                await human.human_type(page, name_el, profile.name)
                result.fields_filled.append("name")

            # Email.
            filled = await filler.fill_text_field("email_input", profile.email)
            if filled:
                result.fields_filled.append("email")

            # Phone.
            filled = await filler.fill_text_field("phone_input", profile.phone)
            if filled:
                result.fields_filled.append("phone")

            # Step 2: Resume upload.
            await self._emit(on_progress, result, "Uploading resume", 1, 5)
            await human.random_scroll(page)
            await human.sleep_between_actions()

            file_input = await page.query_selector(_LEVER_SELECTORS["resume"])
            if file_input is not None:
                await human.click_element(page, file_input)
                await human.random_delay(200, 400)
                await file_input.set_input_files(resume_path)
                result.fields_filled.append("resume")
                await human.sleep_between_actions()
            else:
                # Try the generic file upload.
                uploaded = await filler.handle_file_upload("resume_upload", resume_path)
                if uploaded:
                    result.fields_filled.append("resume")
                else:
                    result.fields_missing.append("resume")

            # Step 3: Cover letter (if provided).
            if cover_letter_text:
                await self._emit(on_progress, result, "Filling cover letter", 2, 5)
                await human.random_scroll(page)

                cl_el = await page.query_selector(_LEVER_SELECTORS["cover_letter"])
                if cl_el is not None:
                    await human.human_type(page, cl_el, cover_letter_text)
                    result.fields_filled.append("cover_letter")
                else:
                    # Lever sometimes has a contenteditable div for the cover letter.
                    cl_div = await page.query_selector(
                        'div[contenteditable="true"][class*="cover"], '
                        'div[contenteditable="true"][class*="letter"]'
                    )
                    if cl_div is not None:
                        await cl_div.click()
                        await human.random_delay(30, 80)
                        await page.keyboard.type(cover_letter_text, delay=30)
                        result.fields_filled.append("cover_letter")
                    else:
                        result.fields_missing.append("cover_letter")

            # Step 4: Additional links (LinkedIn, website, location).
            await self._emit(on_progress, result, "Filling links & additional info", 3, 5)
            await human.random_scroll(page)

            if profile.linkedin_url:
                li_el = await page.query_selector(_LEVER_SELECTORS["linkedin"])
                if li_el is not None:
                    await human.human_type(page, li_el, profile.linkedin_url)
                    result.fields_filled.append("linkedin")

            if profile.location:
                loc_el = await page.query_selector(_LEVER_SELECTORS["location"])
                if loc_el is not None:
                    await human.human_type(page, loc_el, profile.location)
                    result.fields_filled.append("location")

            # Step 5: Submit or pause for HITL.
            await self._emit(on_progress, result, "Finalizing", 4, 5)
            await human.random_scroll(page)
            await human.sleep_between_actions()

            if submit:
                submit_result = await filler.submit_application()
                result.success = submit_result.success
                result.submitted = submit_result.submitted
                if submit_result.error:
                    result.error = submit_result.error
            else:
                result.success = True
                result.submitted = False

        except Exception as exc:
            self._logger.exception("Lever form handling failed")
            result.success = False
            result.error = f"Lever handler error: {exc}"

        return result

    async def _emit(self, cb: Any, result: FormFillingResult, step: str, idx: int, total: int) -> None:
        """Emit a progress update if a callback was registered."""
        if cb is not None:
            from browser_engine.ats_profiles import FormFillingProgress

            update = FormFillingProgress(
                step=step,
                step_index=idx,
                total_steps=total,
                fields_filled_so_far=list(result.fields_filled),
            )
            maybe = cb(update)
            if hasattr(maybe, "__await__"):
                await maybe


# ── Register handler ────────────────────────────────────────────────────────────────

PROFILE_HANDLER_REGISTRY[ATSProfile.LEVER] = LeverFormHandler
