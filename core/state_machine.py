"""Application state machine for the GetAJob platform.

Applications flow through a well-defined lifecycle from discovery through
submission and outreach.  Every transition is validated against an explicit
allow-list, and invalid transitions raise :exc:`StateMachineError`.
"""

from __future__ import annotations as _annotations

import enum
from typing import Any

import structlog

from core.exceptions import StateMachineError

__all__: list[str] = [
    "ALLOWED_TRANSITIONS",
    "ApplicationState",
    "transition_state",
]

logger = structlog.get_logger(__name__)


class ApplicationState(enum.StrEnum):
    """All possible states in the application lifecycle.

    The lifecycle is:

    ``DISCOVERED`` → ``TAILORED`` → ``PENDING_REVIEW`` → ``STAGED`` → ``SUBMITTED``
        ↘                    ↘                          ↘
         ``FAILED``           ``REJECTED`` (by HITL)     ``OUTREACH_PENDING``
    """

    # ── Core lifecycle ───────────────────────────────────────────────────────
    DISCOVERED = "DISCOVERED"
    """Job listing found and parsed by the Ingestion Agent."""

    TAILORED = "TAILORED"
    """Resume and cover letter generated and matched to the job."""

    PENDING_REVIEW = "PENDING_REVIEW"
    """Awaiting human approval (HITL pause)."""

    STAGED = "STAGED"
    """Approved by human, ready for browser submission."""

    SUBMITTED = "SUBMITTED"
    """Application successfully submitted via the Browser Engine."""

    OUTREACH_PENDING = "OUTREACH_PENDING"
    """Contact message staged for recruiter outreach."""

    # ── Terminal / failure states ─────────────────────────────────────────────
    REJECTED = "REJECTED"
    """Rejected by the human reviewer (terminal)."""

    FAILED = "FAILED"
    """Failed at any stage due to an unrecoverable error (terminal)."""


# ── Allowed transitions ──────────────────────────────────────────────────────────

ALLOWED_TRANSITIONS: dict[ApplicationState, set[ApplicationState]] = {
    ApplicationState.DISCOVERED: {
        ApplicationState.TAILORED,
        ApplicationState.REJECTED,
        ApplicationState.FAILED,
    },
    ApplicationState.TAILORED: {
        ApplicationState.PENDING_REVIEW,
        ApplicationState.FAILED,
    },
    ApplicationState.PENDING_REVIEW: {
        ApplicationState.STAGED,
        ApplicationState.TAILORED,  # Re-try tailoring after edits
        ApplicationState.REJECTED,
        ApplicationState.FAILED,
    },
    ApplicationState.STAGED: {
        ApplicationState.SUBMITTED,
        ApplicationState.OUTREACH_PENDING,
        ApplicationState.PENDING_REVIEW,  # Un-stage if edits needed
        ApplicationState.FAILED,
    },
    ApplicationState.SUBMITTED: {
        ApplicationState.OUTREACH_PENDING,
        ApplicationState.FAILED,
    },
    ApplicationState.OUTREACH_PENDING: {
        ApplicationState.FAILED,
    },
    # Terminal states — no outgoing transitions
    ApplicationState.REJECTED: {
        ApplicationState.PENDING_REVIEW,  # HITL reset: un-reject for re-review
    },
    ApplicationState.FAILED: set(),
}


def transition_state(
    current: ApplicationState,
    target: ApplicationState,
    *,
    application_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ApplicationState:
    """Validate and return the *target* state if the transition is allowed.

    This is a **pure validation function** — it does not persist anything.
    Callers (e.g. :meth:`ApplicationService.transition`) are responsible for
    recording the transition in the database.

    Args:
        current: The current state of the application.
        target: The desired next state.
        application_id: Optional application UUID (included in error/log output).
        metadata: Optional context dict for the audit log.

    Returns:
        *target* on success (pass-through for chaining convenience).

    Raises:
        StateMachineError: If *current → target* is not in
            :data:`ALLOWED_TRANSITIONS`.

    Example::

        new_state = transition_state(
            ApplicationState.DISCOVERED,
            ApplicationState.TAILORED,
            application_id=str(app.id),
        )
    """
    allowed = ALLOWED_TRANSITIONS.get(current, set())

    if target not in allowed:
        _id = application_id or "<unknown>"
        logger.warning(
            "Invalid state transition",
            application_id=_id,
            from_state=current.value,
            to_state=target.value,
            allowed=[s.value for s in allowed],
        )
        msg = (
            f"Cannot transition from {current.value!r} to {target.value!r} "
            f"(allowed from {current.value!r}: {[s.value for s in allowed] or 'none — terminal state'})"
        )
        raise StateMachineError(
            msg, details={"from_state": current.value, "target_state": target.value}
        )

    if metadata:
        logger.debug(
            "State transition validated",
            application_id=application_id,
            from_state=current.value,
            to_state=target.value,
        )

    return target
