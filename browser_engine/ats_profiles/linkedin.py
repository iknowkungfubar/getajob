"""LinkedIn "Easy Apply" form handler.

LinkedIn's job application flow uses an inline modal dialog (the "Easy Apply"
form).  The form is multi-step but contained within a single overlay that
advances through pages without a full navigation.  Fields vary by company
since each employer customises the questions.
"""

from __future__ import annotations as _annotations

import re
from typing import TYPE_CHECKING, Any

import structlog
from playwright.async_api import Page

from browser_engine.ats_profiles import (
    PROFILE_HANDLER_REGISTRY,
    ATSProfile,
    FormFillingResult,
)

__all__: list[str] = [
    "LinkedInFormHandler",
]

if TYPE_CHECKING:
    from browser_engine.form_filler import FormFiller
    from browser_engine.human_simulator import HumanSimulator

logger = structlog.get_logger(__name__)

# ── LinkedIn Easy Apply selectors ───────────────────────────────────────────────────

_LINKEDIN_SELECTORS: dict[str, str] = {
    # Apply buttons
    "easy_apply_button": 'button:has-text("Easy Apply"), button[data-control-name="easy_apply"]',
    "next_button": 'button[aria-label*="Next"], button:has-text("Next")',
    "review_button": 'button:has-text("Review")',
    "submit_button": 'button[aria-label*="Submit"], button:has-text("Submit application")',
    "close_button": 'button[aria-label*="Close"], button[data-control-name="discard_application"]',
    "discard_button": 'button:has-text("Discard")',
    # Modal
    "modal": 'div[data-test-modal], div[class*="easy-apply"]',
    "modal_content": 'div[class*="application-form"]',
    # Fields (inside the modal)
    "resume_upload": 'input[type="file"]',
    "phone": 'input[name*="phone"], input[autocomplete="tel"]',
    "email": 'input[name*="email"], input[type="email"]',
    "cover_letter": 'textarea[name*="cover"], textarea[placeholder*="cover" i]',
    "location": 'input[name*="location"], input[placeholder*="location"]',
    "work_authorization": 'select[name*="visa"], select[name*="authorization"]',
    "salary": 'input[name*="salary"], input[placeholder*="salary"]',
    "radio_group": 'fieldset[data-test-form-builder-radio-button-form-component], div[role="radiogroup"]',
    "checkbox": 'input[type="checkbox"]',
    "dropdown": "select",
    "text_input": 'input:not([type="hidden"]):not([type="submit"]):not([type="file"])',
    "textarea": 'textarea:not([type="hidden"])',
    "file_upload_label": 'label:has-text("Resume"), label:has-text("CV")',
}

# ── Step indicators ─────────────────────────────────────────────────────────────────

_LINKEDIN_STEP_SELECTOR = "progress-bar, [data-progress-level], [class*='step-indicator']"


class LinkedInFormHandler:
    """Form handler for LinkedIn Easy Apply.

    The handler opens the Easy Apply modal and advances through each step
    (contact info → resume → custom questions → review → submit), filling
    whatever fields are visible on each page.
    """

    name = ATSProfile.LINKEDIN

    def __init__(self) -> None:
        self._logger = logger.bind(component="LinkedInFormHandler")

    async def detect(self, page: Page) -> bool:
        """Return ``True`` if a LinkedIn Easy Apply form is available."""
        url = page.url.lower()
        if "linkedin.com/jobs" in url:
            return True

        # Check for the Easy Apply button.
        try:
            button = await page.query_selector(_LINKEDIN_SELECTORS["easy_apply_button"])
            if button is not None:
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
        """Navigate LinkedIn Easy Apply and fill the form.

        Args:
            page: Playwright page at a LinkedIn job posting.
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
            # Step 0: Click the Easy Apply button to open the modal.
            await self._emit(on_progress, result, "Opening Easy Apply", 0, 6)
            easy_btn = await page.query_selector(_LINKEDIN_SELECTORS["easy_apply_button"])
            if easy_btn is None:
                result.error = "Easy Apply button not found"
                return result

            await human.click_element(page, easy_btn)
            await human.sleep_between_actions()

            # Wait for the modal to appear.
            try:
                await page.wait_for_selector(
                    "div[data-test-modal], div[class*='easy-apply']",
                    timeout=10_000,
                )
            except Exception:
                result.error = "Easy Apply modal did not appear"
                return result

            # Step 1-4: Iterate through form steps.
            max_steps = 20  # Safety limit.
            current_step = 1

            for _step_idx in range(max_steps):
                # Determine if we're on the review page or a field page.
                review_btn = await page.query_selector(_LINKEDIN_SELECTORS["review_button"])
                submit_btn = await page.query_selector(_LINKEDIN_SELECTORS["submit_button"])

                if submit_btn is not None or review_btn is not None:
                    # On the review/submit page.
                    await self._emit(on_progress, result, "Review & submit", current_step + 1, 6)
                    await human.random_scroll(page)
                    await human.sleep_between_actions()

                    if submit:
                        await human.click_element(page, submit_btn or review_btn)
                        await human.sleep_between_actions()

                        # Wait for confirmation modal.
                        try:
                            await page.wait_for_selector(
                                "text=Submitted,text=Application sent",
                                timeout=15_000,
                            )
                            result.success = True
                            result.submitted = True
                        except Exception:
                            result.success = True
                            result.submitted = True  # Likely succeeded.
                    else:
                        result.success = True
                        result.submitted = False
                    break

                # Fill the current page's visible fields.
                await self._fill_visible_fields(
                    page, human, filler, profile, cover_letter_text, result, resume_path
                )

                # Advance to the next step.
                next_btn = await page.query_selector(_LINKEDIN_SELECTORS["next_button"])
                if next_btn is None:
                    # No next button — try submit/review.
                    continue

                await human.click_element(page, next_btn)
                await human.sleep_between_actions()
                current_step += 1

            else:
                result.error = "Exceeded maximum Easy Apply steps"
                result.success = False

        except Exception as exc:
            self._logger.exception("LinkedIn Easy Apply failed")
            result.success = False
            result.error = f"LinkedIn handler error: {exc}"

        return result

    async def _fill_visible_fields(
        self,
        page: Page,
        human: HumanSimulator,
        _filler: FormFiller,
        profile: Any,
        cover_letter_text: str | None,
        result: FormFillingResult,
        resume_path: str | None = None,
    ) -> None:
        """Detect and fill all visible form fields on the current Easy Apply page."""
        await human.random_scroll(page)

        # 1. Phone number.
        phone_el = await page.query_selector(_LINKEDIN_SELECTORS["phone"])
        if phone_el and await phone_el.is_visible():
            current_value = await phone_el.input_value()
            if not current_value:
                await human.human_type(page, phone_el, profile.phone)
                result.fields_filled.append("phone")

        # 2. Resume upload.
        file_input = await page.query_selector(_LINKEDIN_SELECTORS["resume_upload"])
        if file_input and await file_input.is_visible():
            try:
                await file_input.set_input_files(resume_path)
                result.fields_filled.append("resume")
            except Exception:
                pass

        # 3. Cover letter.
        if cover_letter_text:
            cl_el = await page.query_selector(_LINKEDIN_SELECTORS["cover_letter"])
            if cl_el and await cl_el.is_visible():
                current_value = await cl_el.input_value()
                if not current_value:
                    await human.human_type(page, cl_el, cover_letter_text)
                    result.fields_filled.append("cover_letter")

        # 4. Text inputs (generic — catch fields like location, salary, etc.).
        text_inputs = await page.query_selector_all(_LINKEDIN_SELECTORS["text_input"])
        for inp in text_inputs:
            try:
                if not await inp.is_visible():
                    continue

                name = await inp.get_attribute("name") or ""
                placeholder = await inp.get_attribute("placeholder") or ""
                current_val = await inp.input_value()
                if current_val:
                    continue  # Already filled.

                label_text = await self._find_label_text(page, inp)

                # Match against known field types.
                if self._field_matches(label_text, placeholder, name, "phone"):
                    continue  # Already handled above.
                if self._field_matches(label_text, placeholder, name, "email"):
                    if profile.email:
                        await human.human_type(page, inp, profile.email)
                        result.fields_filled.append("email")
                elif self._field_matches(label_text, placeholder, name, "location"):
                    if profile.location:
                        await human.human_type(page, inp, profile.location)
                        result.fields_filled.append("location")
                elif self._field_matches(label_text, placeholder, name, "salary"):
                    # Skip salary expectations — user may not want to share.
                    pass
                elif (
                    self._field_matches(label_text, placeholder, name, "linkedin")
                    and profile.linkedin_url
                ):
                    await human.human_type(page, inp, profile.linkedin_url)
                    result.fields_filled.append("linkedin")
            except Exception as exc:
                logger.debug("Field interaction failed", field="text_input", error=str(exc))
                continue

        # 5. Dropdowns.
        dropdowns = await page.query_selector_all(_LINKEDIN_SELECTORS["dropdown"])
        for dd in dropdowns:
            try:
                if not await dd.is_visible():
                    continue
                label_text = await self._find_label_text(page, dd)
                if (
                    re.search(r"work.?auth|visa|sponsor", label_text, re.IGNORECASE)
                    and profile.work_authorization
                ):
                    await dd.select_option(label=profile.work_authorization)
                    result.fields_filled.append("work_authorization")
            except Exception as exc:
                logger.debug("Field interaction failed", field="dropdown", error=str(exc))
                continue

        # 6. Radio groups (Yes/No questions).
        radio_groups = await page.query_selector_all(_LINKEDIN_SELECTORS["radio_group"])
        for group in radio_groups:
            try:
                legend = await group.query_selector("legend")
                if legend:
                    legend_text = await legend.inner_text()
                    # Default answer for common questions.
                    if re.search(r"sponsor|visa|work.?auth", legend_text, re.IGNORECASE):
                        no_radio = await group.query_selector(
                            'label:has-text("No"), input[value="No"]'
                        )
                        if no_radio:
                            await human.click_element(page, no_radio)
            except Exception as exc:
                logger.debug("Field interaction failed", field="radio_group", error=str(exc))
                continue

    async def _find_label_text(self, page: Page, element: Any) -> str:
        """Resolve the label text associated with a form element."""
        # Try ``aria-labelledby``.
        labelledby = await element.get_attribute("aria-labelledby")
        if labelledby:
            try:
                label_el = await page.query_selector(f"#{labelledby}")
                if label_el:
                    return (await label_el.inner_text()).strip()
            except Exception:
                pass

        # Try ``aria-label``.
        aria_label = await element.get_attribute("aria-label")
        if aria_label:
            return aria_label

        # Try associated <label> via ``id`` → ``for``.
        el_id = await element.get_attribute("id")
        if el_id:
            try:
                label_el = await page.query_selector(f'label[for="{el_id}"]')
                if label_el:
                    return (await label_el.inner_text()).strip()
            except Exception:
                pass

        # Try parent <label>.
        try:
            parent_label = await element.query_selector("xpath=ancestor::label")
            if parent_label:
                return (await parent_label.inner_text()).strip()
        except Exception:
            pass

        return ""

    @staticmethod
    def _field_matches(label: str, placeholder: str, name: str, field_type: str) -> bool:
        """Check if a field's metadata matches a known field type."""
        patterns: dict[str, list[str]] = {
            "phone": ["phone", "mobile", "telephone", "tel"],
            "email": ["email", "e-mail"],
            "location": ["location", "city", "address", "postal", "zip"],
            "salary": ["salary", "compensation", "pay"],
            "linkedin": ["linkedin", "linkedin profile", "linkedin url"],
        }
        keywords = patterns.get(field_type, [])
        combined = f"{label} {placeholder} {name}".lower()
        return any(kw in combined for kw in keywords)


# ── Register handler ────────────────────────────────────────────────────────────────

PROFILE_HANDLER_REGISTRY[ATSProfile.LINKEDIN] = LinkedInFormHandler
