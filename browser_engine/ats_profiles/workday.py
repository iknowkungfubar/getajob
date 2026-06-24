"""Workday ATS form handler.

Workday is one of the most complex ATS platforms to automate.  Its forms are
multi-page, heavily use custom Web Components (``<wd-*>`` elements), and often
include self-identification questionnaires.  The handler navigates step by
step, using both standard selectors and Workday-specific DOM patterns.
"""

from __future__ import annotations as _annotations

import re
from typing import Any

import structlog
from playwright.async_api import Page

from browser_engine.ats_profiles import ATSFormHandler, ATSProfile, FormFillingResult, PROFILE_HANDLER_REGISTRY

__all__: list[str] = [
    "WorkdayFormHandler",
]

logger = structlog.get_logger(__name__)

# ── Workday-specific selectors ──────────────────────────────────────────────────────

_WORKDAY_SELECTORS: dict[str, str] = {
    # Buttons
    "apply_button": 'button[data-automation-id*="Apply"], button:has-text("Apply"), a:has-text("Apply")',
    "next_button": 'button[data-automation-id*="nextStep"], button:has-text("Next"), button[aria-label*="Next"]',
    "review_button": 'button[data-automation-id*="review"], button:has-text("Review")',
    "submit_button": 'button[data-automation-id*="submit"], button:has-text("Submit")',
    "back_button": 'button[data-automation-id*="back"], button[aria-label*="Back"]',
    # Fields
    "first_name": 'input[data-automation-id*="firstName"], input[name*="firstName"]',
    "last_name": 'input[data-automation-id*="lastName"], input[name*="lastName"]',
    "email": 'input[data-automation-id*="email"], input[name*="email"]',
    "phone": 'input[data-automation-id*="phone"], input[name*="phone"], input[type="tel"]',
    "resume": 'input[type="file"]',
    "cover_letter": 'textarea[data-automation-id*="cover"], textarea[name*="cover"]',
    "linkedin": 'input[data-automation-id*="linkedIn"], input[placeholder*="LinkedIn"]',
    "location": 'input[data-automation-id*="location"], input[name*="location"]',
    # Self-identification
    "gender": 'select[data-automation-id*="gender"]',
    "race": 'select[data-automation-id*="race"], select[data-automation-id*="ethnicity"]',
    "veteran": 'select[data-automation-id*="veteran"]',
    "disability": 'select[data-automation-id*="disability"]',
    # Experience & Education
    "add_experience": 'button[data-automation-id*="addExperience"], button[aria-label*="Add Experience"]',
    "add_education": 'button[data-automation-id*="addEducation"], button[aria-label*="Add Education"]',
    "work_experience_section": 'div[data-automation-id*="workExperienceSection"]',
    "education_section": 'div[data-automation-id*="educationSection"]',
    "language_select": 'div[data-automation-id*="language"] input',
}

# ── Step detection patterns ─────────────────────────────────────────────────────────

_WORKDAY_STEP_INDICATORS: list[re.Pattern[str]] = [
    re.compile(r"apply", re.IGNORECASE),
    re.compile(r"resume", re.IGNORECASE),
    re.compile(r"additional", re.IGNORECASE),
    re.compile(r"self.?identification", re.IGNORECASE),
    re.compile(r"review", re.IGNORECASE),
]


class WorkdayFormHandler:
    """Form handler for Workday ATS.

    Workday's multi-step application process is handled one step at a time.
    The handler detects the current step, fills visible fields, advances,
    and repeats until the review/submit page.
    """

    name = ATSProfile.WORKDAY

    def __init__(self) -> None:
        self._logger = logger.bind(component="WorkdayFormHandler")

    async def detect(self, page: Page) -> bool:
        """Return ``True`` if the page belongs to a Workday ATS."""
        url = page.url.lower()
        if "myworkdayjobs.com" in url or "wd5.myworkdaysite.com" in url:
            return True

        # Check for Workday-specific meta tag.
        try:
            meta = await page.query_selector('meta[name="Workday-Page-Context"]')
            if meta is not None:
                return True
        except Exception:
            pass

        # Check for Workday's web component markup.
        try:
            wd_component = await page.query_selector("[data-automation-id]")
            if wd_component is not None:
                # Workday pages typically have many data-automation-id attributes.
                count = await page.evaluate(
                    "document.querySelectorAll('[data-automation-id]').length"
                )
                if count >= 5:
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
        """Navigate Workday's multi-page application and fill all fields.

        Args:
            page: Playwright page at the Workday job posting.
            profile: User profile.
            resume_path: Path to the resume PDF.
            cover_letter_text: Optional cover letter text.
            **kwargs: May include ``human_simulator``, ``selector_registry``,
                ``submit``, and ``on_progress``.

        Returns:
            A :class:`FormFillingResult`.
        """
        from browser_engine.form_filler import FormFiller  # noqa: PLC0415
        from browser_engine.human_simulator import HumanSimulator  # noqa: PLC0415
        from browser_engine.selectors import SelectorRegistry  # noqa: PLC0415

        human: HumanSimulator = kwargs.get("human_simulator", HumanSimulator())
        selectors: SelectorRegistry = kwargs.get("selector_registry", SelectorRegistry())
        submit: bool = kwargs.get("submit", False)
        on_progress = kwargs.get("on_progress")

        filler = FormFiller(page, human, selectors)
        result = FormFillingResult(success=False)

        try:
            # Step 0: Click "Apply" button if present (job description page).
            await self._emit(on_progress, result, "Starting application", 0, 6)
            apply_btn = await page.query_selector(_WORKDAY_SELECTORS["apply_button"])
            if apply_btn is not None:
                await human.click_element(page, apply_btn)
                await human.sleep_between_actions()
                await page.wait_for_load_state("networkidle")

            # Step 1: Contact / personal info step.
            await self._fill_contact_step(page, human, filler, profile, result)
            await self._advance(page, human)

            # Step 2: Resume upload step.
            await self._emit(on_progress, result, "Uploading resume", 2, 6)
            uploaded = await self._upload_resume(page, human, filler, resume_path)
            if uploaded:
                result.fields_filled.append("resume")
            else:
                result.fields_missing.append("resume")
            await self._advance(page, human)

            # Step 3: Additional info (LinkedIn, cover letter, etc.).
            await self._fill_additional_step(page, human, filler, profile, cover_letter_text, result)
            await self._advance(page, human)

            # Step 4: Self-identification (optional).
            await self._fill_self_id_step(page, human, filler, result)

            # Step 5: Review & submit.
            await self._emit(on_progress, result, "Review & submit", 5, 6)
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
            self._logger.exception("Workday form handling failed")
            result.success = False
            result.error = f"Workday handler error: {exc}"

        return result

    async def _fill_contact_step(
        self,
        page: Page,
        human: HumanSimulator,
        filler: FormFiller,
        profile: Any,
        result: FormFillingResult,
    ) -> None:
        """Fill the contact/personal information step."""
        await self._emit_on(result, "Filling contact information", 1, 6)

        # First name.
        fn_el = await page.query_selector(_WORKDAY_SELECTORS["first_name"])
        if fn_el:
            name_parts = profile.name.strip().split(" ", 1)
            await human.human_type(page, fn_el, name_parts[0])
            result.fields_filled.append("first_name")

        # Last name.
        if len(name_parts) > 1:
            ln_el = await page.query_selector(_WORKDAY_SELECTORS["last_name"])
            if ln_el:
                await human.human_type(page, ln_el, name_parts[1])
                result.fields_filled.append("last_name")

        # Email.
        email_el = await page.query_selector(_WORKDAY_SELECTORS["email"])
        if email_el:
            await human.human_type(page, email_el, profile.email)
            result.fields_filled.append("email")

        # Phone.
        phone_el = await page.query_selector(_WORKDAY_SELECTORS["phone"])
        if phone_el:
            await human.human_type(page, phone_el, profile.phone)
            result.fields_filled.append("phone")

        await human.sleep_between_actions()

    async def _upload_resume(
        self,
        page: Page,
        human: HumanSimulator,
        filler: FormFiller,
        resume_path: str,
    ) -> bool:
        """Upload resume on the resume step."""
        await human.random_scroll(page)

        # Check if there's a file input.
        file_input = await page.query_selector(_WORKDAY_SELECTORS["resume"])
        if file_input:
            await human.click_element(page, file_input)
            await human.random_delay(200, 400)
            await file_input.set_input_files(resume_path)
            await human.sleep_between_actions()
            return True

        # Some Workday instances use a drag-and-drop zone instead.
        drop_zone = await page.query_selector(
            'div[data-automation-id*="resumeDropzone"], '
            'div[data-automation-id*="upload"], '
            'button[data-automation-id*="upload"]'
        )
        if drop_zone:
            await human.click_element(page, drop_zone)
            await human.random_delay(300, 600)
            file_input = await page.query_selector('input[type="file"]')
            if file_input:
                await file_input.set_input_files(resume_path)
                await human.sleep_between_actions()
                return True

        return False

    async def _fill_additional_step(
        self,
        page: Page,
        human: HumanSimulator,
        filler: FormFiller,
        profile: Any,
        cover_letter_text: str | None,
        result: FormFillingResult,
    ) -> None:
        """Fill LinkedIn, portfolio, cover letter, etc."""
        await self._emit_on(result, "Filling additional information", 3, 6)

        # LinkedIn.
        if profile.linkedin_url:
            li_el = await page.query_selector(_WORKDAY_SELECTORS["linkedin"])
            if li_el:
                await human.human_type(page, li_el, profile.linkedin_url)
                result.fields_filled.append("linkedin")

        # Cover letter.
        if cover_letter_text:
            cl_el = await page.query_selector(_WORKDAY_SELECTORS["cover_letter"])
            if cl_el:
                await human.human_type(page, cl_el, cover_letter_text)
                result.fields_filled.append("cover_letter")

        # Handle Workday's experience / education sections.
        await self._handle_experience_section(page, human, profile, result)
        await self._handle_education_section(page, human, profile, result)

        await human.sleep_between_actions()

    async def _handle_experience_section(
        self,
        page: Page,
        human: HumanSimulator,
        profile: Any,
        result: FormFillingResult,
    ) -> None:
        """Fill Workday's work experience section if present."""
        section = await page.query_selector(_WORKDAY_SELECTORS["work_experience_section"])
        if section is None:
            return

        # Try adding experience entries.
        if profile.work_experiences:
            for exp in profile.work_experiences:
                add_btn = await page.query_selector(_WORKDAY_SELECTORS["add_experience"])
                if add_btn is None:
                    break

                await human.click_element(page, add_btn)
                await human.sleep_between_actions()

                # Fill visible experience fields.
                await self._fill_visible_fields(page, human, {
                    "job_title": exp.title,
                    "company": exp.company,
                })
                result.fields_filled.append(f"experience_{exp.company}")

    async def _handle_education_section(
        self,
        page: Page,
        human: HumanSimulator,
        profile: Any,
        result: FormFillingResult,
    ) -> None:
        """Fill Workday's education section if present."""
        section = await page.query_selector(_WORKDAY_SELECTORS["education_section"])
        if section is None:
            return

        if profile.education:
            for edu in profile.education:
                add_btn = await page.query_selector(_WORKDAY_SELECTORS["add_education"])
                if add_btn is None:
                    break

                await human.click_element(page, add_btn)
                await human.sleep_between_actions()

                try:
                    school_field = edu.get("school", "") if isinstance(edu, dict) else getattr(edu, "school", "")
                    degree_field = edu.get("degree", "") if isinstance(edu, dict) else getattr(edu, "degree", "")
                except (AttributeError, TypeError, KeyError):
                    continue

                await self._fill_visible_fields(page, human, {
                    "school": school_field,
                    "degree": degree_field,
                })
                result.fields_filled.append(f"education_{school_field}")

    async def _fill_self_id_step(
        self,
        page: Page,
        human: HumanSimulator,
        filler: FormFiller,
        result: FormFillingResult,
    ) -> None:
        """Fill self-identification questions (gender, race, veteran, disability)."""
        await self._emit_on(result, "Filling self-identification", 4, 6)

        # Check if any self-ID fields are visible on the current page.
        for field_name, selector in [
            ("gender", _WORKDAY_SELECTORS["gender"]),
            ("race", _WORKDAY_SELECTORS["race"]),
            ("veteran", _WORKDAY_SELECTORS["veteran"]),
            ("disability", _WORKDAY_SELECTORS["disability"]),
        ]:
            el = await page.query_selector(selector)
            if el is not None:
                try:
                    await el.select_option(label="I decline to self-identify")
                    result.fields_filled.append(field_name)
                except Exception:
                    pass

        await human.sleep_between_actions()

    async def _advance(self, page: Page, human: HumanSimulator) -> None:
        """Click the "Next" or "Review" button to advance to the next step."""
        for selector_key in ["next_button", "review_button", "submit_button"]:
            btn = await page.query_selector(_WORKDAY_SELECTORS[selector_key])
            if btn is not None:
                await human.click_element(page, btn)
                await human.sleep_between_actions()
                try:
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                except Exception:
                    pass
                return

    async def _fill_visible_fields(self, page: Page, human: HumanSimulator, fields: dict[str, str]) -> None:
        """Fill visible input fields that match known labels."""
        for label, value in fields.items():
            if not value:
                continue

            # Try matching by data-automation-id or name attribute.
            for attr in ["data-automation-id", "name", "aria-label"]:
                try:
                    el = await page.query_selector(
                        f'input[{attr}*="{label}" i], textarea[{attr}*="{label}" i]'
                    )
                    if el is not None and await el.is_visible():
                        await human.human_type(page, el, value)
                        break
                except Exception as exc:
                    logger.debug("Field interaction failed", field=label, error=str(exc))
                    continue

    async def _emit_on(self, result: FormFillingResult, step: str, idx: int, total: int) -> None:
        """Helper for progress emission (called from context where on_progress is
        accessed via outer scope)."""
        self._logger.debug("Workday step", step=step, step_index=idx, total_steps=total)

    async def _emit(self, cb: Any, result: FormFillingResult, step: str, idx: int, total: int) -> None:
        """Emit a progress update if a callback was registered."""
        if cb is not None:
            from browser_engine.ats_profiles import FormFillingProgress  # noqa: PLC0415

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

PROFILE_HANDLER_REGISTRY[ATSProfile.WORKDAY] = WorkdayFormHandler
