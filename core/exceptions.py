"""Custom exception hierarchy for the GetAJob platform.

Every module raises typed exceptions that inherit from a common base,
enabling callers to catch specific failure modes without reaching into
implementation details.
"""

from __future__ import annotations as _annotations

__all__: list[str] = [
    "BrowserError",
    "ConfigurationError",
    "GetAJobError",
    "IngestionError",
    "OutreachError",
    "ProfileError",
    "SecurityError",
    "StateMachineError",
    "TailoringError",
]


class GetAJobError(Exception):
    """Base exception for all GetAJob platform errors."""

    def __init__(self, message: str = "", *, details: dict | None = None) -> None:
        self.details = details or {}
        super().__init__(message)


# ── Configuration ────────────────────────────────────────────────────────────────


class ConfigurationError(GetAJobError):
    """Raised when the system configuration is invalid or missing."""


# ── Profile / Data ───────────────────────────────────────────────────────────────


class ProfileError(GetAJobError):
    """Raised when profile data cannot be loaded, parsed, or written."""


# ── Job Ingestion ────────────────────────────────────────────────────────────────


class IngestionError(GetAJobError):
    """Raised when a job listing cannot be fetched or parsed."""


# ── Tailoring ────────────────────────────────────────────────────────────────────


class TailoringError(GetAJobError):
    """Raised when resume or cover-letter generation fails."""


# ── Browser Automation ───────────────────────────────────────────────────────────


class BrowserError(GetAJobError):
    """Raised when browser automation encounters a recoverable or fatal error."""


# ── Outreach ─────────────────────────────────────────────────────────────────────


class OutreachError(GetAJobError):
    """Raised when recruiter contact discovery or messaging fails."""


# ── State Machine ────────────────────────────────────────────────────────────────


class StateMachineError(GetAJobError):
    """Raised when an invalid application-state transition is attempted."""


# ── Security ─────────────────────────────────────────────────────────────────────


class SecurityError(GetAJobError):
    """Raised when encryption, decryption, or PII tokenization fails."""
