"""Outreach Engine — Recruiter Contact Discovery & Messaging (Module 5).

Discovers recruiter contact information through multiple OSINT strategies,
generates personalized outreach messages, and validates email deliverability.

Usage::

    from outreach_engine import ContactFinder, MessageGenerator, EmailValidator

    finder = ContactFinder()
    recruiter = await finder.find_recruiter(job_listing)

    generator = MessageGenerator()
    message = await generator.generate_outreach_message(
        job_listing, profile, recruiter
    )

    validator = EmailValidator()
    result = validator.validate_deliverability(recruiter.email)
"""

from __future__ import annotations as _annotations

__all__: list[str] = [
    "ContactFinder",
    "EmailValidator",
    "MessageGenerator",
]

from outreach_engine.contact_finder import ContactFinder
from outreach_engine.email_validator import EmailValidator
from outreach_engine.message_generator import MessageGenerator
