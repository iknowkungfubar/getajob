"""Base agent class for the GetAJob Hermes-compatible agent system.

All platform agents (ingestion, tailoring, outreach, etc.) inherit from
:class:`BaseAgent`, which provides logging, configuration access, event-bus
emission, and a shared exception for human-in-the-loop pauses.

Usage::

    class MyAgent(BaseAgent):
        async def run(self) -> None:
            self.logger.info("Agent starting", config=self.config)
            # … do work …
            await self.emit_event("my.event", {"key": "value"})
"""

from __future__ import annotations as _annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any

import structlog

from core.config import GetAJobSettings, get_settings
from core.event_bus import EventBus, EventPriority, InMemoryEventBus

__all__: list[str] = [
    "BaseAgent",
    "HumanInLoopPause",
]


# ── Human-in-the-Loop Exception ──────────────────────────────────────────────


class HumanInLoopPause(Exception):
    """Raised when an agent reaches a point that requires human review.

    The orchestrator catches this exception and routes the item to the
    :class:`~approval_queue.main` web UI for manual approval before
    continuing the workflow.

    Attributes:
        item_id: Identifier for the item requiring review (application UUID,
            outreach message UUID, etc.).
        reason: Human-readable explanation of why the pause was triggered.
        context: Arbitrary metadata forwarded to the approval-queue UI.
    """

    def __init__(
        self,
        item_id: str,
        reason: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.item_id = item_id
        self.reason = reason
        self.context = context or {}
        super().__init__(f"[HITL] {reason} (item: {item_id})")


# ── Base Agent ────────────────────────────────────────────────────────────────


class BaseAgent(ABC):
    """Abstract base class for all GetAJob agents.

    Every agent has:
    - A **name** (short snake_case identifier used in logs and events).
    - Access to the global :class:`~core.config.GetAJobSettings` singleton.
    - A **structured logger** pre-bound with the agent's name.
    - An **event bus** reference for publishing inter-agent events.
    - An **agent_id** (UUID v4) for correlation across logs and events.

    Subclasses must implement :meth:`run`.
    """

    def __init__(
        self,
        name: str = "",
        *,
        event_bus: EventBus | None = None,
    ) -> None:
        self.name: str = name or self.__class__.__name__
        self.agent_id: str = uuid.uuid4().hex[:12]

        # Module-level config singleton.
        self.config: GetAJobSettings = get_settings()

        # Structured logger pre-bound with agent identity.
        self.logger = structlog.get_logger(
            self.__class__.__module__,
            agent=self.name,
            agent_id=self.agent_id,
        )

        # Event bus — default to in-memory if none provided (dev/test).
        self._event_bus: EventBus = event_bus or InMemoryEventBus()

    # ── Abstract interface ─────────────────────────────────────────────────

    @abstractmethod
    async def run(self) -> Any:
        """Execute the agent's primary work loop.

        This is called by the orchestrator once the agent is started.
        Return value semantics are agent-specific (e.g. number of items
        processed, a result dict, or ``None``).
        """
        ...

    # ── Event emission ─────────────────────────────────────────────────────

    async def emit_event(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> None:
        """Publish an event to the bus with this agent as the source.

        Args:
            event_type: A dot-separated event type string
                (e.g. ``"job.discovered"``, ``"application.tailored"``).
            data: JSON-serialisable payload.
            priority: Event priority (default NORMAL).
        """
        await self._event_bus.publish(
            event_type,
            data=data,
            priority=priority,
            source=f"agent:{self.name}:{self.agent_id}",
        )
        self.logger.debug("Event emitted", event_type=event_type, data=data)

    # ── Lifecycle hooks ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Called by the orchestrator before :meth:`run`.

        Subclasses can override this to acquire resources (open connections,
        start background tasks, etc.).
        """
        self.logger.info("Agent starting")

    async def stop(self) -> None:
        """Called by the orchestrator after :meth:`run` completes or on shutdown.

        Subclasses should override this to release resources gracefully.
        """
        self.logger.info("Agent stopping")

    # ── Convenience helpers ────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}[{self.name}] id={self.agent_id}>"
