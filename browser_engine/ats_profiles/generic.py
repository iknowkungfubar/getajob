"""Generic fallback form handler for unknown ATS systems.

When no ATS-specific handler is registered, the :class:`GenericFormHandler`
uses a vision/LLM-based approach to discover and fill form fields.  It:

1. Captures a page screenshot.
2. Queries an LLM (via the platform's unified client) to identify visible
   form fields and their semantic meanings.
3. Maps fields to profile data.
4. Fills each field using the shared form-filler primitives.

If an LLM client is unavailable, it falls back to heuristic scanning for
common input types (``<input>``, ``<textarea>``, ``<select>``).
"""

from __future__ import annotations as _annotations

import base64
import json
from typing import Any

import structlog
from playwright.async_api import Page

from browser_engine.ats_profiles import (
    PROFILE_HANDLER_REGISTRY,
    ATSProfile,
    FormFillingResult,
)

__all__: list[str] = [
    "GenericFormHandler",
]

logger = structlog.get_logger(__name__)


class GenericFormHandler:
    """Fallback handler for unknown / unrecognised ATS forms.

    Uses a two-tier strategy:
      1. **LLM-vision** — capture a screenshot and ask the LLM to identify
         fields, their types, and selectors.
      2. **Heuristic scan** — if the LLM is unavailable or fails, scan the
         DOM for common input patterns.

    This handler always matches (``detect`` returns ``True``) so it acts as
    the default when no other handler claims a page.
    """

    name = ATSProfile.GENERIC

    def __init__(self) -> None:
        self._logger = logger.bind(component="GenericFormHandler")

    async def detect(self, _page: Page) -> bool:
        """Always return ``True`` — this is the universal fallback."""
        return True

    async def handle(  # type: ignore[override]
        self,
        page: Page,
        profile: Any,
        resume_path: str,
        cover_letter_text: str | None = None,
        **kwargs: Any,
    ) -> FormFillingResult:
        """Analyse and fill an unknown ATS form using vision + heuristics.

        Args:
            page: Playwright page at the application form.
            profile: User profile.
            resume_path: Path to the resume PDF.
            cover_letter_text: Optional cover letter text.
            **kwargs: May include ``human_simulator``, ``selector_registry``,
                ``submit``, ``on_progress``, and ``llm_client``.

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
            # Tier 1: Try LLM-vision field discovery.
            llm_client = kwargs.get("llm_client")
            fields: list[dict[str, str]] = []

            if llm_client is not None:
                await self._emit(on_progress, result, "Analysing form with LLM vision", 0, 4)
                fields = await self._discover_fields_via_llm(page, llm_client)
                self._logger.info("LLM vision discovered fields", count=len(fields))

            # Tier 2: Fall back to heuristic scan if LLM failed or returned no fields.
            if not fields:
                await self._emit(on_progress, result, "Scanning form heuristically", 0, 4)
                fields = await self._heuristic_scan(page)
                self._logger.info("Heuristic scan discovered fields", count=len(fields))

            if not fields:
                result.error = "No form fields could be discovered on this page"
                return result

            # Fill fields using the discovered information.
            await self._emit(on_progress, result, "Filling form fields", 1, 4)
            await human.random_scroll(page)
            await human.sleep_between_actions()

            for field_info in fields:
                field_name = field_info.get("name", "")
                selector = field_info.get("selector", "")
                field_type = field_info.get("type", "text")

                if not selector:
                    continue

                value = self._map_field_to_profile(
                    field_name, field_type, profile, cover_letter_text
                )

                if value is None:
                    result.fields_missing.append(field_name)
                    continue

                try:
                    if field_type in ("file", "upload"):
                        if value and isinstance(value, str):
                            await filler.handle_file_upload(
                                "resume_upload", value, selector=selector
                            )
                            result.fields_filled.append(field_name)
                    elif field_type in ("select", "dropdown"):
                        await filler.handle_dropdown(field_name, str(value), selector=selector)
                        result.fields_filled.append(field_name)
                    elif field_type in ("checkbox", "radio"):
                        await filler.handle_checkbox(field_name, bool(value), selector=selector)
                        result.fields_filled.append(field_name)
                    else:
                        await filler.fill_text_field(field_name, str(value), selector=selector)
                        result.fields_filled.append(field_name)
                except Exception as exc:
                    self._logger.warning("Failed to fill field", field=field_name, error=str(exc))
                    result.fields_missing.append(field_name)

            # Upload resume if there's a file input we haven't filled yet.
            await self._emit(on_progress, result, "Uploading resume", 2, 4)
            if "resume" not in result.fields_filled:
                uploaded = await filler.handle_file_upload("resume_upload", resume_path)
                if uploaded:
                    result.fields_filled.append("resume")

            # Submit or pause.
            await self._emit(on_progress, result, "Review & submit", 3, 4)
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
            self._logger.exception("Generic form handling failed")
            result.success = False
            result.error = f"Generic handler error: {exc}"

        return result

    async def _discover_fields_via_llm(self, page: Page, llm_client: Any) -> list[dict[str, str]]:
        """Use an LLM with vision to identify form fields on the current page.

        Sends a screenshot to the LLM and asks it to return a structured list
        of visible form fields with suggested selectors.

        Returns:
            A list of ``{"name": ..., "selector": ..., "type": ...}`` dicts.
        """
        try:
            # Capture screenshot as base64.
            screenshot_bytes = await page.screenshot(full_page=True)
            base64.b64encode(screenshot_bytes).decode("utf-8")

            prompt = (
                "You are analysing a job application form from a screenshot. "
                "Identify ALL visible form fields (text inputs, textareas, "
                "dropdowns, file uploads, checkboxes, radio buttons, submit "
                "buttons). For each field, provide:\n"
                "1. A semantic name (e.g. 'first_name', 'email', 'resume_upload')\n"
                "2. A CSS selector that uniquely identifies the element "
                "(use the element's id, data-*, name, aria-label, or class)\n"
                "3. The field type (text, email, tel, textarea, select, "
                "file, checkbox, radio, submit)\n\n"
                "Return ONLY a JSON array of objects with keys: name, selector, type."
            )

            schema: dict[str, Any] = {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "selector": {"type": "string"},
                                "type": {"type": "string"},
                            },
                            "required": ["name", "selector", "type"],
                        },
                    }
                },
                "required": ["fields"],
            }

            # The generic handler might get a text-only LLM client that can't
            # process images. Try structured generation first.
            try:
                result_dict = await llm_client.generate_structured(
                    prompt=prompt,
                    schema=schema,
                    system="You are a form-field extraction specialist.",
                )
            except Exception:
                # Fall back to text generation and parse JSON from the response.
                text = await llm_client.generate_text(
                    prompt=prompt,
                    system="You are a form-field extraction specialist. Return ONLY valid JSON.",
                )
                result_dict = json.loads(
                    text.strip().removeprefix("```json").removesuffix("```").strip()
                )

            return result_dict.get("fields", [])

        except Exception as exc:
            self._logger.warning("LLM field discovery failed", error=str(exc)[:200])
            return []

    async def _heuristic_scan(self, page: Page) -> list[dict[str, str]]:
        """Scan the DOM for common form input elements.

        Returns:
            A list of ``{"name": ..., "selector": ..., "type": ...}`` dicts.
        """
        fields: list[dict[str, str]] = []

        try:
            # Scan all visible form elements.
            elements = await page.evaluate(
                """
                () => {
                    const els = [];
                    const inputs = document.querySelectorAll(
                        'input:not([type="hidden"]), textarea, select, button[type="submit"]'
                    );
                    inputs.forEach(el => {
                        // Skip elements that are not visible.
                        const rect = el.getBoundingClientRect();
                        if (rect.width === 0 || rect.height === 0) return;

                        const tag = el.tagName.toLowerCase();
                        const type = el.type || tag;
                        const id = el.id ? `#${CSS.escape(el.id)}` : '';
                        const name = el.name ? `[name="${CSS.escape(el.name)}"]` : '';
                        const cls = el.className && typeof el.className === 'string'
                            ? el.className.split(' ').filter(c => c).map(c => `.${CSS.escape(c)}`).join('')
                            : '';
                        const placeholder = el.placeholder || '';
                        const ariaLabel = el.getAttribute('aria-label') || '';
                        const dataAttr = el.getAttribute('data-automation-id')
                            ? `[data-automation-id="${CSS.escape(el.getAttribute('data-automation-id'))}"]`
                            : '';

                        let selector = id || dataAttr || name || cls;
                        if (!selector) {
                            // Fall back to a generic tag-based selector.
                            selector = `${tag}${name || cls ? '' : ''}`;
                        }

                        els.push({
                            name: placeholder || ariaLabel || name || tag,
                            selector: selector,
                            type: type === 'text' ? (el.inputMode || 'text') : type,
                        });
                    });
                    return els;
                }
                """
            )

            for el_data in elements:
                if isinstance(el_data, dict) and el_data.get("selector"):
                    fields.append({
                        "name": str(el_data.get("name", "")),
                        "selector": str(el_data.get("selector", "")),
                        "type": _normalise_field_type(str(el_data.get("type", "text"))),
                    })

        except Exception as exc:
            self._logger.warning("Heuristic scan failed", error=str(exc)[:200])

        # Deduplicate by selector.
        seen: set[str] = set()
        deduped: list[dict[str, str]] = []
        for f in fields:
            sel = f["selector"]
            if sel not in seen:
                seen.add(sel)
                deduped.append(f)

        return deduped

    @staticmethod
    def _map_field_to_profile(
        field_name: str,
        field_type: str,
        profile: Any,
        cover_letter_text: str | None,
    ) -> Any:
        """Map a semantic field name and type to profile data.

        Args:
            field_name: Semantic name from LLM or heuristic (e.g. ``"email"``).
            field_type: HTML input type (``"text"``, ``"email"``, etc.).
            profile: The user profile.
            cover_letter_text: Optional cover letter text.

        Returns:
            The value to fill, or ``None`` if no match is found.
        """
        name_lower = field_name.lower()

        # Direct field matching.
        field_map: dict[str, str] = {
            "name": profile.name,
            "full_name": profile.name,
            "first_name": profile.name.split(" ", 1)[0] if profile.name else "",
            "last_name": profile.name.split(" ", 1)[1]
            if profile.name and " " in profile.name
            else "",
            "email": profile.email,
            "e-mail": profile.email,
            "phone": profile.phone,
            "telephone": profile.phone,
            "mobile": profile.phone,
            "location": profile.location or "",
            "city": profile.location or "",
            "linkedin": profile.linkedin_url or "",
            "linkedin_url": profile.linkedin_url or "",
            "linkedin_profile": profile.linkedin_url or "",
            "portfolio": profile.portfolio_url or "",
            "website": profile.portfolio_url or "",
            "work_authorization": profile.work_authorization or "",
            "visa": profile.work_authorization or "",
            "work_status": profile.work_authorization or "",
            "resume": None,  # Handled separately via file upload.
            "cv": None,
            "cover_letter": cover_letter_text,
            "cover_note": cover_letter_text,
        }

        # Check for field_name in the map.
        if name_lower in field_map:
            return field_map[name_lower]

        # Check for field_type-based mapping.
        if field_type in ("email",):
            return profile.email
        if field_type in ("tel", "phone"):
            return profile.phone
        if field_type in ("file",):
            return None  # File uploads handled separately.

        # Keyword-based matching.
        combined = name_lower
        if "email" in combined or "e-mail" in combined:
            return profile.email
        if "phone" in combined or "telephone" in combined or "mobile" in combined:
            return profile.phone
        if "name" in combined:
            return profile.name
        if "linkedin" in combined:
            return profile.linkedin_url or ""
        if "location" in combined or "city" in combined:
            return profile.location or ""
        if "portfolio" in combined or "website" in combined or "url" in combined:
            return profile.portfolio_url or ""
        if "cover" in combined and "letter" in combined:
            return cover_letter_text

        return None

    async def _emit(
        self, cb: Any, result: FormFillingResult, step: str, idx: int, total: int
    ) -> None:
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


# ── Helper ──────────────────────────────────────────────────────────────────────────


def _normalise_field_type(raw: str) -> str:
    """Normalise an HTML input type to one of our internal types."""
    type_map: dict[str, str] = {
        "text": "text",
        "email": "email",
        "tel": "tel",
        "password": "password",
        "number": "number",
        "url": "url",
        "date": "date",
        "file": "file",
        "checkbox": "checkbox",
        "radio": "radio",
        "select": "select",
        "select-one": "select",
        "textarea": "textarea",
        "submit": "submit",
    }
    return type_map.get(raw.lower(), "text")


# ── Register handler ────────────────────────────────────────────────────────────────

PROFILE_HANDLER_REGISTRY[ATSProfile.GENERIC] = GenericFormHandler
