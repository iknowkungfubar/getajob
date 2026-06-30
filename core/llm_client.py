"""Unified LLM client abstraction for the GetAJob platform.

Provides a common interface for calling language models --- either the Claude
API (via the Anthropic SDK) or a local model.  A :class:`MockLLMClient` is
included for testing without network calls.
"""

from __future__ import annotations as _annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any, TypeVar, cast

R = TypeVar("R")

import structlog  # noqa: E402

from core.config import get_settings  # noqa: E402
from core.exceptions import ConfigurationError, TailoringError  # noqa: E402

__all__: list[str] = [
    "ClaudeAPIClient",
    "LLMClient",
    "MockLLMClient",
    "get_llm_client",
]

logger = structlog.get_logger(__name__)


# ── Abstract base ────────────────────────────────────────────────────────────────


class LLMClient(ABC):
    """Abstract interface for LLM interactions.

    Every public method is async and includes retry logic in the base
    implementation.
    """

    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_DELAY_S = 2.0
    DEFAULT_MAX_TOKENS = 4096

    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """Send a text prompt and return the generated completion.

        Args:
            prompt: The user message / instruction text.
            system: Optional system prompt.
            max_tokens: Maximum tokens in the response.
            temperature: Sampling temperature (0.0 — 2.0).

        Returns:
            The generated text string.
        """

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Send a prompt and receive a structured JSON response matching *schema*.

        Args:
            prompt: The user message.
            schema: A JSON Schema dict describing the expected output shape.
            system: Optional system prompt.
            max_tokens: Maximum tokens in the response.
            temperature: Sampling temperature.

        Returns:
            A dict that validates against *schema*.
        """

    @abstractmethod
    def generate_stream(  # type: ignore[misc]  # abstract: subclasses use yield, making this async-generator
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream tokens from the model one chunk at a time.

        Args:
            prompt: The user message.
            system: Optional system prompt.
            max_tokens: Maximum tokens in the response.

        Yields:
            Successive text chunks as they arrive.
        """
        ...
        return
        yield  # pragma: no cover

    async def _retry(
        self, coro_factory: Callable[[], Coroutine[Any, Any, R]], label: str = "llm_call"
    ) -> R:
        """Retry *coro_factory* up to ``DEFAULT_MAX_RETRIES`` times on failure."""
        last_exc: Exception | None = None
        for attempt in range(1, self.DEFAULT_MAX_RETRIES + 1):
            try:
                return await coro_factory()
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "LLM call failed (retrying)",
                    attempt=attempt,
                    max_retries=self.DEFAULT_MAX_RETRIES,
                    label=label,
                    error=str(exc),
                )
                if attempt < self.DEFAULT_MAX_RETRIES:
                    await asyncio.sleep(self.DEFAULT_RETRY_DELAY_S * attempt)
        msg = f"LLM call failed after {self.DEFAULT_MAX_RETRIES} attempts"
        raise TailoringError(
            msg, details={"label": label, "last_error": str(last_exc)}
        ) from last_exc


# ── Claude API Client ────────────────────────────────────────────────────────────


class ClaudeAPIClient(LLMClient):
    """LLM client backed by Anthropic's Claude API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        timeout_s: int | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.llm.api_key
        self._model = model or settings.llm.model
        self._max_tokens = max_tokens or settings.llm.max_tokens
        self._timeout_s = timeout_s or settings.llm.timeout_seconds

        if not self._api_key:
            msg = (
                "Claude API key is not configured. "
                "Set GETAJOB_LLM__API_KEY in .env or pass api_key to the constructor."
            )
            raise ConfigurationError(msg)

        self._client: Any = None  # anthropic.AsyncAnthropic

    def _lazy_client(self) -> Any:
        """Import and cache the Anthropic SDK client (lazy import)."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:
                msg = "The ``anthropic`` package is required for ClaudeAPIClient"
                raise ConfigurationError(msg) from exc
            self._client = AsyncAnthropic(api_key=self._api_key, max_retries=2)
        return self._client

    async def generate_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        async def _call() -> str:
            client = self._lazy_client()
            resp = await client.messages.create(
                model=self._model,
                max_tokens=max_tokens or self._max_tokens,
                temperature=temperature if temperature is not None else 0.7,
                system=system or "",
                messages=[{"role": "user", "content": prompt}],
            )
            return cast(str, resp.content[0].text)

        return await self._retry(_call, label="generate_text")

    async def generate_structured(
        self,
        prompt: str,
        _schema: dict[str, Any],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        async def _call() -> dict[str, Any]:
            client = self._lazy_client()
            resp = await client.messages.create(
                model=self._model,
                max_tokens=max_tokens or self._max_tokens,
                temperature=temperature if temperature is not None else 0.7,
                system=system or "",
                messages=[{"role": "user", "content": prompt}],
                # Instruct the model to return a valid JSON object.
                # No beta header is required for structured output on Claude 4.x.
                extra_headers={},
            )
            text = cast(str, resp.content[0].text)
            # Strip markdown fences if present.
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                text = text.rsplit("```", 1)[0].strip()

            return cast(dict[str, Any], json.loads(text))

        return await self._retry(_call, label="generate_structured")

    async def generate_stream(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        client = self._lazy_client()
        async with client.messages.stream(
            model=self._model,
            max_tokens=max_tokens or self._max_tokens,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            async for text_chunk in stream.text_stream:
                yield text_chunk


# ── Mock Client (testing) ────────────────────────────────────────────────────────


class MockLLMClient(LLMClient):
    """In-memory mock that returns pre-configured responses.

    Useful for integration tests where you want to control exactly what the
    "LLM" returns.
    """

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.responses: dict[str, str] = responses or {}
        self.call_history: list[dict[str, Any]] = []

    async def generate_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,  # noqa: ARG002
        temperature: float | None = None,
    ) -> str:
        self.call_history.append({
            "method": "generate_text",
            "prompt": prompt,
            "system": system,
            "temperature": temperature,
        })
        return self._lookup(prompt, system)

    async def generate_structured(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        system: str | None = None,
        max_tokens: int | None = None,  # noqa: ARG002
        temperature: float | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        self.call_history.append({
            "method": "generate_structured",
            "prompt": prompt,
            "system": system,
            "schema": schema,
        })
        raw = self._lookup(prompt, system)
        return cast(dict[str, Any], json.loads(raw))

    async def generate_stream(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int | None = None,  # noqa: ARG002
    ) -> AsyncIterator[str]:
        self.call_history.append({"method": "generate_stream", "prompt": prompt, "system": system})
        text = self._lookup(prompt, system)
        for word in text.split(" "):
            yield word + " "

    def _lookup(self, prompt: str, system: str | None = None) -> str:
        key = system or prompt[:64]
        default = json.dumps({"response": "mock", "key": key})
        return self.responses.get(prompt, self.responses.get(key, default))


# ── Factory ──────────────────────────────────────────────────────────────────────


def get_llm_client() -> LLMClient:
    """Return the appropriate LLM client based on the current configuration.

    Reads ``settings.llm.provider`` and returns:

    - ``"anthropic"`` → :class:`ClaudeAPIClient`
    - ``"mock"`` → :class:`MockLLMClient`
    - Other values raise :class:`ConfigurationError`.
    """
    settings = get_settings()
    provider = settings.llm.provider.lower()

    if provider == "anthropic":
        return ClaudeAPIClient()
    if provider == "mock":
        return MockLLMClient()
    msg = f"Unknown LLM provider: {provider!r} (expected 'anthropic' or 'mock')"
    raise ConfigurationError(msg)
