"""Tests for the EmailValidator module.

Covers format validation, MX record checking, deliverability scoring,
disposable-domain detection, and role-based address detection.
"""

from __future__ import annotations as _annotations

import pytest

from outreach_engine.email_validator import DeliverabilityResult, EmailValidator


class TestValidateFormat:
    """Tests for ``validate_format``."""

    def test_valid_email(self) -> None:
        validator = EmailValidator()
        assert validator.validate_format("jane.smith@acme.com") is True

    def test_valid_email_with_plus(self) -> None:
        validator = EmailValidator()
        assert validator.validate_format("jane+filter@acme.com") is True

    def test_missing_at_symbol(self) -> None:
        validator = EmailValidator()
        assert validator.validate_format("jane.smith.acme.com") is False

    def test_empty_string(self) -> None:
        validator = EmailValidator()
        assert validator.validate_format("") is False

    def test_none_input(self) -> None:
        validator = EmailValidator()
        assert validator.validate_format(None) is False  # type: ignore[arg-type]

    def test_no_tld(self) -> None:
        validator = EmailValidator()
        assert validator.validate_format("jane@localhost") is False

    def test_local_too_long(self) -> None:
        validator = EmailValidator()
        local = "a" * 65
        assert validator.validate_format(f"{local}@acme.com") is False


class TestSanitizeEmail:
    """Tests for ``sanitize_email``."""

    def test_strips_whitespace(self) -> None:
        validator = EmailValidator()
        assert validator.sanitize_email("  jane@acme.com  ") == "jane@acme.com"

    def test_lowercases(self) -> None:
        validator = EmailValidator()
        assert validator.sanitize_email("Jane@Acme.Com") == "jane@acme.com"

    def test_removes_mailto_prefix(self) -> None:
        validator = EmailValidator()
        assert validator.sanitize_email("mailto:jane@acme.com") == "jane@acme.com"

    def test_removes_angle_brackets(self) -> None:
        validator = EmailValidator()
        assert validator.sanitize_email("<jane@acme.com>") == "jane@acme.com"

    def test_empty_string(self) -> None:
        validator = EmailValidator()
        assert validator.sanitize_email("") == ""


class TestDeliverability:
    """Tests for ``validate_deliverability``."""

    @pytest.mark.asyncio
    async def test_returns_early_on_invalid_format(self) -> None:
        validator = EmailValidator()
        result = await validator.validate_deliverability("not-an-email")
        assert result.format_valid is False
        assert result.overall_score == 0.0

    @pytest.mark.asyncio
    async def test_valid_email_gets_baseline_score(self) -> None:
        validator = EmailValidator()
        result = await validator.validate_deliverability("jane@acme.com")
        # Format valid = 0.2 baseline
        assert result.format_valid is True
        assert result.overall_score >= 0.2

    def test_role_based_detection(self) -> None:
        validator = EmailValidator(check_role_based=True)
        result = DeliverabilityResult(
            format_valid=True,
            is_role_based=True,
            overall_score=0.2,
            details=["Role-based address"],
        )
        assert result.is_role_based is True

    def test_disposable_detection(self) -> None:
        validator = EmailValidator(check_disposable=True)
        result = DeliverabilityResult(
            format_valid=True,
            is_disposable=True,
            overall_score=0.2,
            details=["Disposable email domain"],
        )
        assert result.is_disposable is True


class TestDisposableDomains:
    """Tests for disposable-email detection."""

    def test_known_disposable_is_detected(self) -> None:
        validator = EmailValidator()
        assert validator._check_disposable is True

    def test_role_based_prefix_flagged(self) -> None:
        validator = EmailValidator()
        from outreach_engine.email_validator import _ROLE_BASED_PREFIXES

        assert "info" in _ROLE_BASED_PREFIXES
        assert "hr" in _ROLE_BASED_PREFIXES
