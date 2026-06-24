"""Browser Execution Engine — automated stealth job-application submission.

The Browser Execution Engine (Module 4) is the most critical component of the
GetAJob platform.  It uses ``browser-use`` (wrapping Playwright) to navigate
job portals, detect Applicant Tracking Systems, fill forms with human-like
behaviour, and submit applications with a HITL safety gate.

Architecture
============

::

    FormFiller              ← orchestrator, delegates per-ATS
      ├── ATSDetector       ← URL / DOM analysis
      ├── SelectorRegistry  ← multi-strategy field discovery
      ├── HumanSimulator    ← Bezier curves, typing, delays
      └── ATS profiles      ← Workday, Greenhouse, Lever, etc.
            ├── workday.py
            ├── greenhouse.py
            ├── lever.py
            ├── linkedin.py
            ├── indeed.py
            └── generic.py  ← vision-LLM fallback

Typical usage::

    from browser_engine import StealthBrowser, HumanSimulator, FormFiller

    browser = StealthBrowser()
    await browser.launch()
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto(job_url)

    human = HumanSimulator()
    filler = FormFiller(page, human)
    result = await filler.fill_form(profile, resume_path, cover_letter_text)
    # ... human reviews via the Approval Queue ...
    if approved:
        submit_result = await filler.submit_application()
"""

from __future__ import annotations as _annotations

from browser_engine.ats_detector import ATSDetector, DetectionResult
from browser_engine.ats_profiles import (
    ATSProfile,
    FormFillingProgress,
    FormFillingResult,
    get_handler_for_profile,
)
from browser_engine.form_filler import FormFiller
from browser_engine.human_simulator import HumanSimulator
from browser_engine.selectors import KNOWN_FIELD_TYPES, SelectorRegistry, SelectorStrategy
from browser_engine.stealth_browser import STEALTH_INIT_SCRIPT, StealthBrowser

__all__: list[str] = [
    "KNOWN_FIELD_TYPES",
    # Constants
    "STEALTH_INIT_SCRIPT",
    "ATSDetector",
    # Data types
    "ATSProfile",
    "DetectionResult",
    "FormFiller",
    "FormFillingProgress",
    "FormFillingResult",
    "HumanSimulator",
    "SelectorRegistry",
    "SelectorStrategy",
    # Core classes
    "StealthBrowser",
    # Utilities
    "get_handler_for_profile",
]
