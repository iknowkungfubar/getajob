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

import typing as _t

__all__: list[str] = [
    "KNOWN_FIELD_TYPES",
    "STEALTH_INIT_SCRIPT",
    "ATSDetector",
    "ATSProfile",
    "DetectionResult",
    "FormFiller",
    "FormFillingProgress",
    "FormFillingResult",
    "HumanSimulator",
    "SelectorRegistry",
    "SelectorStrategy",
    "StealthBrowser",
    "export_submit_url",
    "get_handler_for_profile",
    "is_available",
]

_LAZY_IMPORTS: dict[str, tuple[str, str, list[str]]] = {
    "is_available": ("browser_engine._availability", "is_available", []),
    "export_submit_url": ("browser_engine.manual_submit", "export_submit_url", []),
    "STEALTH_INIT_SCRIPT": ("browser_engine.stealth_browser", "STEALTH_INIT_SCRIPT", []),
    "StealthBrowser": ("browser_engine.stealth_browser", "StealthBrowser", []),
    "ATSDetector": ("browser_engine.ats_detector", "ATSDetector", []),
    "DetectionResult": ("browser_engine.ats_detector", "DetectionResult", []),
    "ATSProfile": ("browser_engine.ats_profiles", "ATSProfile", []),
    "FormFillingProgress": ("browser_engine.ats_profiles", "FormFillingProgress", []),
    "FormFillingResult": ("browser_engine.ats_profiles", "FormFillingResult", []),
    "get_handler_for_profile": ("browser_engine.ats_profiles", "get_handler_for_profile", []),
    "FormFiller": ("browser_engine.form_filler", "FormFiller", ["FormFiller"]),
    "HumanSimulator": ("browser_engine.human_simulator", "HumanSimulator", ["HumanSimulator"]),
    "SelectorRegistry": ("browser_engine.selectors", "SelectorRegistry", ["SelectorRegistry"]),
    "SelectorStrategy": ("browser_engine.selectors", "SelectorStrategy", ["SelectorStrategy"]),
    "KNOWN_FIELD_TYPES": ("browser_engine.selectors", "KNOWN_FIELD_TYPES", ["KNOWN_FIELD_TYPES"]),
}

# Track which names have been resolved this session.
_resolved: dict[str, _t.Any] = {}


def __getattr__(name: str) -> _t.Any:
    """Resolve *name* on first access, caching the result.

    Sub-modules that transitively import ``playwright`` or ``browser_use``
    are imported lazily so that ``import browser_engine`` never crashes when
    those dependencies are missing.
    """
    # Fast path — already resolved in this process.
    if name in _resolved:
        return _resolved[name]

    # Check whether this name is a known lazy export.
    if name in _LAZY_IMPORTS:
        mod_path, attr, _ = _LAZY_IMPORTS[name]
        import importlib

        mod = importlib.import_module(mod_path)
        value = getattr(mod, attr)
        _resolved[name] = value
        return value

    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    """Include lazy exports in ``dir()`` listings."""
    return sorted(set(__all__))
