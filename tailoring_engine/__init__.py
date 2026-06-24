"""Tailoring Engine — Resume & Cover Letter Generation (Module 3).

Generates job-specific resumes and cover letters that are honest, effective,
and indistinguishable from human writing.  The engine includes anti-AI
detection guardrails and a truth-validator that cross-checks every claim
against the user's master profile.

Usage::

    from tailoring_engine import ResumeGenerator, CoverLetterGenerator
    from tailoring_engine.anti_ai_detector import AntiAIDetector
    from tailoring_engine.truth_validator import TruthValidator
"""

from __future__ import annotations as _annotations

__all__: list[str] = [
    "ResumeGenerator",
    "CoverLetterGenerator",
]

from tailoring_engine.resume_generator import ResumeGenerator
from tailoring_engine.cover_letter_generator import CoverLetterGenerator
