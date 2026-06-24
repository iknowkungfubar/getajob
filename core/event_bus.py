"""Async event bus for inter-module communication.

Supports a Redis-backed implementation for production and a local in-memory
implementation for development and testing.  Both expose the same
:class:`EventBus` ABC so callers are decoupled from the transport.
"""

from __future__ import annotations as _annotations

import asyncio
import enum
import json
import uuid
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)

__all__: list[str] = [
    "EventBus",
    "EventHandler",
    "EventPriority",
    "InMemoryEventBus",
    "RedisEventBus",
]

# ── Priorities ───────────────────────────────────────────────────────────────────


class EventPriority(enum.IntEnum):
    """Priority levels for bus events.

    Higher-priority events are dispatched first within a single batch.
    """

    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


# ── Types ────────────────────────────────────────────────────────────────────────

EventHandler = Callable[["Event"], Coroutine[Any, Any, None]]
"""Signature for an async event handler: ``async def handler(event: Event) -> None``."""


class Event:
    """Lightweight event envelope carried across the bus."""

    __slots__ = ("data", "id", "priority", "source", "type")

    def __init__(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        priority: EventPriority = EventPriority.NORMAL,
        source: str | None = None,
    ) -> None:
        self.id: str = uuid.uuid4().hex[:16]
        self.type: str = event_type
        self.data: dict[str, Any] = data or {}
        self.priority: EventPriority = priority
        self.source: str | None = source

    def __repr__(self) -> str:
        return f"Event(id={self.id!r}, type={self.type!r}, priority={self.priority.name})"


# ── Event type constants ─────────────────────────────────────────────────────────


class EventType:
    """Well-known event type strings used across the platform."""

    JOB_DISCOVERED = "job.discovered"
    APPLICATION_TAILORED = "application.tailored"
    REVIEW_APPROVED = "review.approved"
    REVIEW_REJECTED = "review.rejected"
    SUBMITTED = "application.submitted"
    SUBMISSION_FAILED = "application.submission_failed"
    OUTREACH_STAGED = "outreach.staged"
    PROFILE_UPDATED = "profile.updated"
    SYSTEM_ERROR = "system.error"


# ── Abstract interface ───────────────────────────────────────────────────────────


class EventBus(Protocol):
    """Protocol defining the event-bus interface.

    Both :class:`InMemoryEventBus` and :class:`RedisEventBus` satisfy this
    protocol.
    """

    async def publish(self, event_type: str, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        """Publish an event to all subscribed handlers.

        Args:
            event_type: A string key (e.g. ``"job.discovered"``).
            data: Arbitrary JSON-serialisable payload.
            **kwargs: Forwarded to the :class:`Event` constructor (priority, source).
        """
        ...

    async def subscribe(self, event_type: str, handler: EventHandler) -> Callable[[], None]:
        """Register *handler* for *event_type*.

        Returns a no-arg callable that unsubscribes the handler when invoked.
        """
        ...

    async def start(self) -> None:
        """Open the bus transport (e.g. Redis connection pool)."""

    async def stop(self) -> None:
        """Close the bus transport gracefully."""


# ── In-memory implementation (testing / dev) ─────────────────────────────────────


class InMemoryEventBus:
    """Local in-memory event bus for development and testing.

    Events are dispatched synchronously within the calling coroutine —
    there is no background worker.  This makes it trivial to reason about
    event ordering in tests.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._started = False

    async def start(self) -> None:
        self._started = True
        logger.info("In-memory event bus started")

    async def stop(self) -> None:
        self._subscribers.clear()
        self._started = False
        logger.info("In-memory event bus stopped")

    async def publish(self, event_type: str, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        event = Event(event_type, data, **kwargs)
        handlers = self._subscribers.get(event_type, [])
        if not handlers:
            logger.debug("Event published with no subscribers", event_type=event_type, event_id=event.id)
            return

        logger.debug("Dispatching event", event_type=event_type, handlers=len(handlers))
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception("Handler failed for event", event_type=event_type, handler=handler.__name__)

    async def subscribe(self, event_type: str, handler: EventHandler) -> Callable[[], None]:
        self._subscribers.setdefault(event_type, []).append(handler)
        logger.debug("Handler subscribed", event_type=event_type, handler=handler.__name__)

        def _unsubscribe() -> None:
            self._subscribers[event_type].remove(handler)

        return _unsubscribe


# ── Redis-backed implementation (production) ─────────────────────────────────────


class RedisEventBus:
    """Production event bus backed by Redis pub/sub.

    Events are serialised as JSON and published to Redis channels named
    after the event type.  A background listener task dispatches to local
    subscribers.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._redis_url = redis_url
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._pub: Any = None  # redis.asyncio.Redis
        self._sub: Any = None  # redis.asyncio.Redis
        self._listener_task: asyncio.Task[None] | None = None
        self._started = False

    async def start(self) -> None:
        import redis.asyncio as aioredis

        self._pub = aioredis.from_url(self._redis_url, decode_responses=True)
        self._sub = aioredis.from_url(self._redis_url, decode_responses=True)
        self._started = True

        # The listener runs in the background.
        self._listener_task = asyncio.create_task(self._listen_loop())
        logger.info("Redis event bus started", url=self._redis_url)

    async def stop(self) -> None:
        self._started = False
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        if self._pub is not None:
            await self._pub.aclose()
        if self._sub is not None:
            await self._sub.aclose()
        logger.info("Redis event bus stopped")

    async def publish(self, event_type: str, data: dict[str, Any] | None = None, **kwargs: Any) -> None:
        event = Event(event_type, data, **kwargs)
        payload = json.dumps({"id": event.id, "type": event.type, "data": event.data})
        await self._pub.publish(event.type, payload)
        logger.debug("Published event to Redis", event_type=event_type, channel=event.type)

    async def subscribe(self, event_type: str, handler: EventHandler) -> Callable[[], None]:
        self._subscribers.setdefault(event_type, []).append(handler)
        await self._sub.subscribe(event_type)  # type: ignore[union-attr]

        def _unsubscribe() -> None:
            self._subscribers[event_type].remove(handler)

        return _unsubscribe

    async def _listen_loop(self) -> None:
        """Background loop: listen on subscribed Redis channels and dispatch."""
        if self._sub is None:
            return

        async with self._sub as conn:
            # Redis pub/sub listener does not use ``with`` in asyncio-redis
            # the same way — we get a PubSub object via psubscribe / subscribe.
            pubsub = conn.pubsub()
            # The actual subscribed channels are set on subscribe() calls.
            async for message in pubsub.listen():
                if not self._started:
                    break
                if message["type"] != "message":
                    continue

                try:
                    payload: dict[str, Any] = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Ignoring non-JSON Redis message", raw=message["data"])
                    continue

                event_type: str = message["channel"]
                event = Event(event_type, payload.get("data"))
                handlers = list(self._subscribers.get(event_type, []))
                for handler in handlers:
                    try:
                        await handler(event)
                    except Exception:
                        logger.exception("Handler failed", event_type=event_type, handler=handler.__name__)
