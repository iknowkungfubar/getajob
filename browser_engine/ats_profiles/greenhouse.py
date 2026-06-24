"""Greenhouse ATS form handler.

Greenhouse presents relatively simple, single-page application forms with
well-structured HTML.  Fields use custom ``data-*`` attributes and the
form auto-fills many fields from the uploaded resume.
"""

from __future__ import annotations as _annotations

from typing import Any

import structlog
from playwright.async_api import Page

from browser_engine.ats_profiles import ATSFormHandler, ATSProfile, FormFillingResult, PROFILE_HANDLER_REGISTRY

__all__: list[str] = [
    "GreenhouseFormHandler",
]

logger = structlog.get_logger(__name__)

# ── Known Greenhouse selectors ──────────────────────────────────────────────────────

_GREENHOUSE_SELECTORS: dict[str, str] = {
    "first_name": 'input[name="first_name"], input[id*="first_name"]',
    "last_name": 'input[name="last_name"], input[id*="last_name"]',
    "email": 'input[name="email"], input[data-email]',
    "phone": 'input[name="phone"], input[type="tel"]',
    "resume": 'input[type="file"][name*="resume"], input[type="file"][accept*="pdf"]',
    "cover_letter": 'textarea[name*="cover"], textarea[id*="cover_letter"]',
    "linkedin": 'input[name*="linkedin"], input[placeholder*="LinkedIn"]',
    "website": 'input[name*="website"], input[placeholder*="website"]',
    "location": 'input[name*="location"], input[placeholder*="location"]',
    "work_authorization": 'select[name*="work_authorization"], select[name*="visa"]',
    "race": 'select[name*="race"], select[name*="ethnicity"]',
    "gender": 'select[name*="gender"]',
    "veteran": 'select[name*="veteran"]',
    "disability": 'select[name*="disability"]',
    "submit": 'button[type="submit"], button:has-text("Submit"), button:has-text("Submit Application")',
    "next": 'button:has-text("Next"), button:has-text("Continue")',
    "review": 'button:has-text("Review"), a:has-text("Review")',
}

# ── Known values for standard Greenhouse dropdowns ──────────────────────────────────

_GREENHOUSE_DROPDOWN_VALUES: dict[str, dict[str, str]] = {
    "work_authorization": {
        "us_citizen": "US Citizen",
        "green_card": "Green Card",
        "h1b": "H-1B",
        "tn": "TN Permit",
        "other": "Other",
    },
    "race": {
        "white": "White",
        "asian": "Asian",
        "black": "Black or African American",
        "hispanic": "Hispanic or Latino",
        "two_or_more": "Two or More Races",
        "decline": "I decline to self-identify",
    },
    "gender": {
        "male": "Male",
        "female": "Female",
        "decline": "I decline to self-identify",
        "non_binary": "Non-binary",
    },
    "veteran": {
        "yes": "I am a protected veteran",
        "no": "I am not a protected veteran",
        "decline": "I decline to self-identify",
    },
    "disability": {
        "yes": "Yes, I have a disability",
        "no": "No, I don't have a disability",
        "decline": "I decline to self-identify",
    },
}


class GreenhouseFormHandler:
    """Form handler for Greenhouse ATS (boards.greenhouse.io).

    Greenhouse forms are typically single-page with well-labelled fields.
    The handler fills standard fields, uploads the resume, optionally adds
    a cover letter, and submits.
    """

    name = ATSProfile.GREENHOUSE

    def __init__(self) -> None:
        self._logger = logger.bind(component="GreenhouseFormHandler")

    async def detect(self, page: Page) -> bool:
        """Return ``True`` if the page is a Greenhouse application form."""
        url = page.url.lower()
        if "boards.greenhouse.io" in url and "/jobs/" in url:
            return True

        # Check for Greenhouse-specific DOM signals.
        for attr_name in ["data-source", "data-greenhouse"]:
            try:
                el = await page.query_selector(f'[{attr_name}="greenhouse"]')
                if el is not None:
                    return True
            except Exception:
                continue

        return False

    async def handle(  # type: ignore[override]
        self,
        page: Page,
        profile: Any,
        resume_path: str,
        cover_letter_text: str | None = None,
        **kwargs: Any,
    ) -> FormFillingResult:
        """Navigate a Greenhouse application form and fill all fields.

        Args:
            page: Playwright page at the Greenhouse job application URL.
            profile: User profile (``ProfileCreate`` schema).
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
            # Step 1: Personal information.
            await self._emit(on_progress, result, "Filling personal information", 0, 5)

            # Check if the "Apply with resume" / import step is present.
            await human.random_scroll(page)
            await human.sleep_between_actions()

            # First name.
            first_name_el = await page.query_selector(_GREENHOUSE_SELECTORS["first_name"])
            if first_name_el:
                name_parts = profile.name.strip().split(" ", 1)
                await human.human_type(page, first_name_el, name_parts[0])
                result.fields_filled.append("first_name")

                # Last name.
                if len(name_parts) > 1:
                    last_el = await page.query_selector(_GREENHOUSE_SELECTORS["last_name"])
                    if last_el:
                        await human.human_type(page, last_el, name_parts[1])
                        result.fields_filled.append("last_name")

            # Email.
            filled = await filler.fill_text_field("email_input", profile.email)
            if filled:
                result.fields_filled.append("email")

            # Phone.
            filled = await filler.fill_text_field("phone_input", profile.phone)
            if filled:
                result.fields_filled.append("phone")

            # Step 2: Upload resume.
            await self._emit(on_progress, result, "Uploading resume", 1, 5)
            uploaded = await filler.handle_file_upload("resume_upload", resume_path)
            if uploaded:
                result.fields_filled.append("resume")
                await human.sleep_between_actions()
            else:
                result.fields_missing.append("resume")

            # Step 3: Cover letter (if provided).
            if cover_letter_text:
                await self._emit(on_progress, result, "Filling cover letter", 2, 5)
                cl_el = await page.query_selector(_GREENHOUSE_SELECTORS["cover_letter"])
                if cl_el:
                    await human.human_type(page, cl_el, cover_letter_text)
                    result.fields_filled.append("cover_letter")
                else:
                    result.fields_missing.append("cover_letter")

            # Step 4: Additional fields (LinkedIn, website, location).
            await self._emit(on_progress, result, "Filling additional fields", 3, 5)
            if profile.linkedin_url:
                li_el = await page.query_selector(_GREENHOUSE_SELECTORS["linkedin"])
                if li_el:
                    await human.human_type(page, li_el, profile.linkedin_url)
                    result.fields_filled.append("linkedin")

            if profile.location:
                loc_el = await page.query_selector(_GREENHOUSE_SELECTORS["location"])
                if loc_el:
                    await human.human_type(page, loc_el, profile.location)
                    result.fields_filled.append("location")

            # Step 5: Work authorization.
            await self._emit(on_progress, result, "Filling work authorization", 4, 5)
            if profile.work_authorization:
                wa_el = await page.query_selector(_GREENHOUSE_SELECTORS["work_authorization"])
                if wa_el:
                    values = _GREENHOUSE_DROPDOWN_VALUES.get("work_authorization", {})
                    val = values.get(profile.work_authorization.lower().replace(" ", "_"), profile.work_authorization)
                    await filler.handle_dropdown("work_authorization", val)
                    result.fields_filled.append("work_authorization")

            # Handle EEO / demographic pages (optional, often paginated).
            await self._handle_eeo_sections(page, human, result)

            # Step 6: Submit or pause for HITL.
            if submit:
                await self._emit(on_progress, result, "Submitting application", 5, 5)
                submit_result = await filler.submit_application()
                result.success = submit_result.success
                result.submitted = submit_result.submitted
                if submit_result.error:
                    result.error = submit_result.error
            else:
                # Scroll to final review and pause.
                await human.random_scroll(page)
                await human.sleep_between_actions()
                result.success = True
                result.submitted = False

        except Exception as exc:
            self._logger.exception("Greenhouse form handling failed")
            result.success = False
            result.error = f"Greenhouse handler error: {exc}"

        return result

    async def _handle_eeo_sections(self, page: Page, human: Any, result: FormFillingResult) -> None:
        """Fill optional EEO/demographic sections if present.

        Greenhouse often presents paginated EEO questions after the main form.
        """
        eeos = [
            ("race", _GREENHOUSE_SELECTORS["race"]),
            ("gender", _GREENHOUSE_SELECTORS["gender"]),
            ("veteran", _GREENHOUSE_SELECTORS["veteran"]),
            ("disability", _GREENHOUSE_SELECTORS["disability"]),
        ]

        for field_name, selector in eeos:
            try:
                el = await page.query_selector(selector)
                if el is not None:
                    values = _GREENHOUSE_DROPDOWN_VALUES.get(field_name, {})
                    default_val = values.get("decline", "I decline to self-identify")
                    await el.select_option(label=default_val)
                    result.fields_filled.append(field_name)

                    # Check for next/submit button after EEO page.
                    next_btn = await page.query_selector(_GREENHOUSE_SELECTORS["next"])
                    if next_btn:
                        await human.click_element(page, next_btn)
                        await human.sleep_between_actions()
            except Exception:
                continue

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

PROFILE_HANDLER_REGISTRY[ATSProfile.GREENHOUSE] = GreenhouseFormHandler
