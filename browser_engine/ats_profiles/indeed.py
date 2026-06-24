"""Indeed ATS form handler.

Indeed's application flow is nuanced: some employers use Indeed's own hosted
application form, while others redirect to the employer's own career site.
This handler focuses on Indeed's native apply flow, which appears as a
multi-step modal within the job listing page.
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
    "IndeedFormHandler",
]

logger = structlog.get_logger(__name__)

# ── Indeed selectors ────────────────────────────────────────────────────────────────

_INDEED_SELECTORS: dict[str, str] = {
    # Apply button
    "apply_button": 'button:has-text("Apply now"), a:has-text("Apply now"), button[data-tn-component*="apply"]',
    "external_apply_button": 'button:has-text("Apply on company site"), a[data-tn-element*="externalApply"]',
    # Modal elements
    "modal": 'div[id*="apply-modal"], div[class*="apply-modal"], div[id*="apply"]',
    "modal_close": 'button[aria-label*="Close"], button[class*="close"]',
    "next_button": 'button:has-text("Next"), button[aria-label*="Next"]',
    "submit_button": 'button:has-text("Submit"), button:has-text("Send"), button[type="submit"]',
    "review_button": 'button:has-text("Review"), button:has-text("Continue")',
    "back_button": 'button:has-text("Back")',
    # Core fields
    "name": 'input[name="name"], input[autocomplete="name"]',
    "email": 'input[name="email"], input[type="email"]',
    "phone": 'input[name="phone"], input[type="tel"]',
    "resume": 'input[type="file"]',
    "cover_letter": 'textarea[name*="cover"], textarea[placeholder*="cover" i]',
    "location": 'input[name*="location"], input[placeholder*="location"]',
    "work_authorization": 'select[name*="visa"], select[name*="authorization"]',
    "salary": 'input[name*="salary"], input[placeholder*="salary"]',
    "linkedin": 'input[name*="linkedin"], input[placeholder*="LinkedIn"]',
    "website": 'input[name*="url"], input[placeholder*="Portfolio"]',
    "education": 'select[name*="education"], select[class*="education"]',
    "experience": 'select[name*="experience"], select[class*="experience"]',
    "radio_yes_no": 'div[class*="radio"] input[type="radio"]',
    "checkbox": 'input[type="checkbox"]',
    "text_input": 'input:not([type="hidden"]):not([type="submit"]):not([type="file"]):not([type="radio"]):not([type="checkbox"])',
    "dropdown": 'select:not([multiple])',
}


class IndeedFormHandler:
    """Form handler for Indeed native applications.

    Handles Indeed's multi-step apply modal.  If the job redirects externally,
    the handler reports this so the caller can fall back to generic handling
    or the ATS detector.
    """

    name = ATSProfile.INDEED

    def __init__(self) -> None:
        self._logger = logger.bind(component="IndeedFormHandler")

    async def detect(self, page: Page) -> bool:
        """Return ``True`` if Indeed apply form is present."""
        url = page.url.lower()
        if "indeed.com" in url:
            # Check for apply button or modal.
            try:
                button = await page.query_selector(_INDEED_SELECTORS["apply_button"])
                if button is not None:
                    return True
            except Exception:
                pass

            try:
                modal = await page.query_selector(_INDEED_SELECTORS["modal"])
                if modal is not None:
                    return True
            except Exception:
                pass

            return True  # On indeed.com but no apply form → still our domain.

        return False

    async def handle(  # type: ignore[override]
        self,
        page: Page,
        profile: Any,
        resume_path: str,
        cover_letter_text: str | None = None,
        **kwargs: Any,
    ) -> FormFillingResult:
        """Navigate Indeed's apply flow and fill the form.

        Args:
            page: Playwright page at an Indeed job listing.
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
            # Step 0: Check for external redirect.
            await self._emit(on_progress, result, "Checking application type", 0, 5)
            external_btn = await page.query_selector(_INDEED_SELECTORS["external_apply_button"])
            if external_btn is not None:
                result.success = False
                result.error = (
                    "External application redirect — Indeed form cannot be handled "
                    "natively; switch to Generic handler"
                )
                return result

            # Step 0b: Click the apply button to open the modal.
            apply_btn = await page.query_selector(_INDEED_SELECTORS["apply_button"])
            if apply_btn is None:
                result.error = "Indeed Apply button not found"
                return result

            await human.click_element(page, apply_btn)
            await human.sleep_between_actions()

            # Wait for modal to appear.
            try:
                await page.wait_for_selector(
                    "div[id*='apply-modal'], div[class*='apply-modal']",
                    timeout=10_000,
                )
            except Exception:
                # Indeed may redirect to a new page.
                await page.wait_for_load_state("networkidle")
                await human.sleep_between_actions()

            # Step 1: Fill personal info (name, email, phone).
            await self._emit(on_progress, result, "Filling personal info", 1, 5)
            await self._fill_visible_text_fields(page, human, profile, result)

            # Try advancing.
            await self._try_advance(page, human)

            # Step 2: Upload resume.
            await self._emit(on_progress, result, "Uploading resume", 2, 5)
            uploaded = await filler.handle_file_upload("resume_upload", resume_path)
            if uploaded:
                result.fields_filled.append("resume")
            else:
                # Try Indeed-specific resume upload.
                file_input = await page.query_selector(_INDEED_SELECTORS["resume"])
                if file_input:
                    await human.click_element(page, file_input)
                    await human.random_delay(200, 400)
                    await file_input.set_input_files(resume_path)
                    result.fields_filled.append("resume")
                else:
                    result.fields_missing.append("resume")

            await self._try_advance(page, human)

            # Step 3: Additional questions (experience, education, etc.).
            await self._emit(on_progress, result, "Answering additional questions", 3, 5)
            await self._fill_additional_questions(page, human, profile, result)
            await self._try_advance(page, human)

            # Step 4: Submit or pause.
            await self._emit(on_progress, result, "Review & submit", 4, 5)
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
            self._logger.exception("Indeed form handling failed")
            result.success = False
            result.error = f"Indeed handler error: {exc}"

        return result

    async def _fill_visible_text_fields(
        self,
        page: Page,
        human: HumanSimulator,
        profile: Any,
        result: FormFillingResult,
    ) -> None:
        """Fill visible text inputs with profile data."""
        text_inputs = await page.query_selector_all(_INDEED_SELECTORS["text_input"])

        for inp in text_inputs:
            try:
                if not await inp.is_visible():
                    continue

                placeholder = (await inp.get_attribute("placeholder") or "").lower()
                aria = (await inp.get_attribute("aria-label") or "").lower()
                name = (await inp.get_attribute("name") or "").lower()
                current_val = await inp.input_value()

                if current_val:
                    continue

                combined = f"{placeholder} {aria} {name}"

                if "name" in combined and profile.name:
                    await human.human_type(page, inp, profile.name)
                    result.fields_filled.append("name")
                elif "email" in combined or "e-mail" in combined:
                    await human.human_type(page, inp, profile.email)
                    result.fields_filled.append("email")
                elif "phone" in combined or "tel" in combined or "mobile" in combined:
                    await human.human_type(page, inp, profile.phone)
                    result.fields_filled.append("phone")
                elif ("location" in combined or "city" in combined) and profile.location:
                    await human.human_type(page, inp, profile.location)
                    result.fields_filled.append("location")
            except Exception as exc:
                logger.debug("Field interaction failed", field="text_input", error=str(exc))
                continue

    async def _fill_additional_questions(
        self,
        page: Page,
        human: HumanSimulator,
        profile: Any,
        result: FormFillingResult,
    ) -> None:
        """Fill dropdowns, radio buttons, and custom questions."""
        # Dropdowns (experience level, education, etc.).
        dropdowns = await page.query_selector_all(_INDEED_SELECTORS["dropdown"])
        for dd in dropdowns:
            try:
                if not await dd.is_visible():
                    continue
                aria = (await dd.get_attribute("aria-label") or "").lower()
                name = (await dd.get_attribute("name") or "").lower()
                combined = f"{aria} {name}"

                # Default to the highest option for experience/education.
                if "education" in combined or "degree" in combined:
                    options = await dd.query_selector_all("option")
                    if options:
                        await dd.select_option(index=len(options) - 2)  # Second-to-last.
                        result.fields_filled.append("education")
                elif "experience" in combined:
                    options = await dd.query_selector_all("option")
                    if options:
                        await dd.select_option(index=len(options) - 3)
                        result.fields_filled.append("experience")
            except Exception as exc:
                logger.debug("Field interaction failed", field="dropdown", error=str(exc))
                continue

        # Note: Indeed custom questions vary widely by employer.
        # The generic handler is better suited for highly customised forms.

    async def _try_advance(self, page: Page, human: HumanSimulator) -> None:
        """Attempt to click a next/review/submit button if present."""
        for key in ["next_button", "review_button", "submit_button"]:
            btn = await page.query_selector(_INDEED_SELECTORS[key])
            if btn is not None:
                await human.click_element(page, btn)
                await human.sleep_between_actions()
                try:
                    await page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass
                return

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

PROFILE_HANDLER_REGISTRY[ATSProfile.INDEED] = IndeedFormHandler
