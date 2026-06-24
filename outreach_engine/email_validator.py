"""Email validation for recruiter contacts.

Performs RFC-like format checks, MX record verification, and deliverability
estimation.  MX lookups use ``dnspython`` when available and fall back to
``socket``/``getaddrinfo`` for basic domain resolution.

Usage::

    from outreach_engine.email_validator import EmailValidator

    validator = EmailValidator()
    if validator.validate_format("jane@company.com"):
        mx_ok = await validator.validate_mx("company.com")
        result = await validator.validate_deliverability("jane@company.com")
        print(result.overall_score)
"""

from __future__ import annotations as _annotations

import re
import socket
from dataclasses import dataclass, field
from email.utils import parseaddr

import structlog

__all__: list[str] = [
    "DeliverabilityResult",
    "EmailValidator",
]

logger = structlog.get_logger(__name__)

# RFC 5322 simplified email pattern — permissive but catches obvious invalids.
_EMAIL_PATTERN = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$",
)

_COMMON_DISPOSABLE_DOMAINS: set[str] = {
    "mailinator.com",
    "guerrillamail.com",
    "10minutemail.com",
    "tempmail.com",
    "throwaway.email",
    "yopmail.com",
    "sharklasers.com",
    "trashmail.com",
    "maildrop.cc",
    "temp-mail.org",
    "fakeinbox.com",
    "dispostable.com",
    "mailnesia.com",
    "getairmail.com",
}

_ROLE_BASED_PREFIXES: set[str] = {
    "info",
    "admin",
    "support",
    "contact",
    "hello",
    "team",
    "sales",
    "marketing",
    "press",
    "jobs",
    "hr",
    "careers",
    "billing",
    "help",
    "enquiries",
    "noreply",
    "no-reply",
}

# ── Result dataclass ────────────────────────────────────────────────────────


@dataclass
class DeliverabilityResult:
    """Result of an email deliverability assessment.

    Each dimension is scored 0.0-1.0, with 1.0 = best possible
    deliverability.
    """

    format_valid: bool = False
    """Whether the email passes RFC-like format validation."""

    mx_exists: bool = False
    """Whether the domain has MX records for mail receipt."""

    mx_score: float = 0.0
    """MX quality score — presence of well-known mail providers scores higher."""

    domain_resolves: bool = False
    """Whether the domain resolves to an A/AAAA record."""

    is_role_based: bool = False
    """Whether the local-part appears to be a role address (info@, contact@)."""

    is_disposable: bool = False
    """Whether the domain is a known disposable-email provider."""

    overall_score: float = 0.0
    """Composite deliverability score (0.0 - 1.0)."""

    details: list[str] = field(default_factory=list)
    """Human-readable explanation of the assessment."""


# ── Validator ───────────────────────────────────────────────────────────────


class EmailValidator:
    """Validate recruiter email addresses for correctness and deliverability.

    Args:
        check_disposable: Whether to check against a known list of disposable
            email providers (default ``True``).
        check_role_based: Whether to flag role-based email addresses
            (default ``True``).
    """

    def __init__(
        self,
        *,
        check_disposable: bool = True,
        check_role_based: bool = True,
    ) -> None:
        self._check_disposable = check_disposable
        self._check_role_based = check_role_based
        self.logger = logger.bind(module="email_validator")

    # ── Public API ─────────────────────────────────────────────────────────

    def validate_format(self, email: str) -> bool:
        """Check whether *email* has a valid RFC-like format.

        Args:
            email: The email address to check.

        Returns:
            ``True`` if the format is plausible.
        """
        if not email or not isinstance(email, str):
            return False

        sanitised = self.sanitize_email(email)

        # Python's ``parseaddr`` is quite permissive — use our regex as well.
        _parsed_name, parsed_addr = parseaddr(sanitised)
        if not parsed_addr or "@" not in parsed_addr:
            return False

        if not _EMAIL_PATTERN.match(parsed_addr):
            return False

        # Basic sanity checks on the local-part and domain.
        local, domain = parsed_addr.rsplit("@", 1)
        if len(local) > 64:
            return False
        if len(domain) > 255:
            return False
        return "." in domain  # Must have a TLD.

    async def validate_mx(self, domain: str) -> bool:
        """Check whether *domain* has at least one MX record.

        Tries ``dnspython`` first (async via custom executor), then falls
        back to checking whether the domain resolves via ``getaddrinfo``.

        Args:
            domain: The domain to check (e.g. ``"company.com"``).

        Returns:
            ``True`` if MX records (or at least A/AAAA records) exist.
        """
        if not domain or "." not in domain:
            return False

        # Attempt MX record lookup via dnspython.
        mx_found = await self._check_mx_dnspython(domain)

        if mx_found:
            return True

        # Fallback: check whether the domain resolves at all.
        resolves = await self._check_domain_resolves(domain)
        if resolves:
            # No MX but domain exists — could still receive mail via A record.
            self.logger.debug("Domain resolves but no MX records", domain=domain)
            return True

        return False

    async def validate_deliverability(self, email: str) -> DeliverabilityResult:
        """Full deliverability assessment for *email*.

        Combines format validation, MX lookup, domain resolution, role-address
        and disposable-domain checks into a composite score.

        Args:
            email: The email address to assess.

        Returns:
            A :class:`DeliverabilityResult` with per-dimension scores.
        """
        sanitised = self.sanitize_email(email)
        details: list[str] = []

        # 1. Format check.
        format_valid = self.validate_format(sanitised)
        if not format_valid:
            return DeliverabilityResult(
                format_valid=False,
                details=["Email address is not in a valid format"],
            )

        details.append("Format valid")

        # 2. Parse local-part and domain.
        local, domain = sanitised.rsplit("@", 1)

        # 3. MX check.
        mx_exists = await self.validate_mx(domain)
        if mx_exists:
            details.append("Domain has MX records")
        else:
            details.append("No MX records found — mail may not be deliverable")

        # 4. Domain resolution.
        domain_resolves = mx_exists or await self._check_domain_resolves(domain)
        if domain_resolves:
            details.append("Domain resolves")
        else:
            details.append("Domain does not resolve")

        # 5. MX quality score.
        mx_score = self._score_mx_quality(domain)

        # 6. Role-based check.
        is_role_based = False
        if self._check_role_based:
            local_lower = local.lower()
            is_role_based = local_lower in _ROLE_BASED_PREFIXES
            if is_role_based:
                details.append("Role-based address — may not reach a specific person")

        # 7. Disposable check.
        is_disposable = False
        if self._check_disposable:
            domain_lower = domain.lower()
            is_disposable = domain_lower in _COMMON_DISPOSABLE_DOMAINS
            if is_disposable:
                details.append("Disposable email domain — unreliable")

        # 8. Composite score.
        score = 0.0
        if format_valid:
            score += 0.2
        if mx_exists:
            score += 0.3
        if domain_resolves:
            score += 0.2
        if not is_role_based:
            score += 0.15
        if not is_disposable:
            score += 0.15

        overall_score = round(min(score, 1.0), 4)

        return DeliverabilityResult(
            format_valid=format_valid,
            mx_exists=mx_exists,
            mx_score=round(mx_score, 4),
            domain_resolves=domain_resolves,
            is_role_based=is_role_based,
            is_disposable=is_disposable,
            overall_score=overall_score,
            details=details,
        )

    @staticmethod
    def sanitize_email(email: str) -> str:
        """Clean an email address by stripping whitespace and normalising.

        Args:
            email: Raw email string.

        Returns:
            Normalised email string.
        """
        if not email:
            return ""

        cleaned = email.strip().lower()
        # Remove mailto: prefix if present.
        if cleaned.startswith("mailto:"):
            cleaned = cleaned[7:]
        # Remove any angle-bracket wrapping from copy-paste.
        cleaned = cleaned.strip("<>")

        return cleaned

    # ── MX / Network helpers ───────────────────────────────────────────────

    @staticmethod
    async def _check_mx_dnspython(domain: str) -> bool:
        """Attempt to resolve MX records using dnspython (optional dependency).

        Falls back gracefully if dnspython is not installed.
        """
        try:
            import dns.asyncresolver
            import dns.exception
            import dns.resolver
        except ImportError:
            logger.debug("dnspython not available — skipping MX lookup")
            return False

        try:
            answers = await dns.asyncresolver.resolve(domain, "MX", lifetime=10)
            mx_records = [r for r in answers if r.to_text().strip()]
            return len(mx_records) > 0
        except (dns.exception.DNSException, dns.resolver.NoAnswer, dns.resolver.NXDOMAIN) as exc:
            logger.debug("MX lookup failed", domain=domain, error=str(exc))
            return False

    @staticmethod
    def _resolve_sync(domain: str) -> bool:
        """Synchronous domain resolution via getaddrinfo."""
        try:
            socket.getaddrinfo(domain, 25, socket.AF_INET, socket.SOCK_STREAM)
            return True
        except (socket.gaierror, OSError):
            return False

    async def _check_domain_resolves(self, domain: str) -> bool:
        """Check whether *domain* resolves to an IP address."""
        from asyncio import get_event_loop

        loop = get_event_loop()
        try:
            return await loop.run_in_executor(None, self._resolve_sync, domain)
        except Exception as exc:
            logger.debug("Domain resolution check failed", domain=domain, error=str(exc))
            return False

    @staticmethod
    def _score_mx_quality(domain: str) -> float:
        """Assign a quality score to the domain based on known mail providers.

        Domains hosted on well-known providers are scored higher.
        """
        domain_lower = domain.lower()

        # Well-regarded mail providers.
        high_quality = {"google.com", "outlook.com", "microsoft.com", "exchange.com"}
        medium_quality = {"zoho.com", "protonmail.com", "fastmail.com", "icloud.com"}

        for hq in high_quality:
            if hq in domain_lower or _check_mx_provider(domain_lower, hq):
                return 0.9

        for mq in medium_quality:
            if mq in domain_lower or _check_mx_provider(domain_lower, mq):
                return 0.7

        # Self-hosted or unknown — no score penalty but not boosted.
        return 0.5


def _check_mx_provider(_domain: str, _provider_domain: str) -> bool:
    """Simple heuristic: does domain use this provider for MX?"""
    # We could check MX records here, but that would require another DNS
    # call — this is a static heuristic.
    return False
