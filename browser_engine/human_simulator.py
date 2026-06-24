"""Human-like interaction patterns for browser automation.

The :class:`HumanSimulator` sits between the automation code and Playwright,
injecting realistic timing, mouse movements, and typing patterns so that
job-portal anti-bot systems cannot easily distinguish the automation from
a genuine user.
"""

from __future__ import annotations as _annotations

import asyncio
import random
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from playwright.async_api import ElementHandle, Locator, Page

from core.config import get_settings

__all__: list[str] = [
    "HumanSimulator",
]

logger = structlog.get_logger(__name__)

# ── Default constants ───────────────────────────────────────────────────────────────

_DEFAULT_TYPING_MIN_MS = 50
_DEFAULT_TYPING_MAX_MS = 120
_DEFAULT_TYPO_PROBABILITY = 0.03
_DEFAULT_PAUSE_MIN_S = 1.0
_DEFAULT_PAUSE_MAX_S = 3.0
_DEFAULT_SCROLL_STEPS = 8
_BEZIER_POINTS = 20

# Common English-character typos for simulation.
_TYPO_MAP: dict[str, str] = {
    "t": "r",
    "h": "n",
    "e": "w",
    "i": "o",
    "o": "p",
    "a": "s",
    "s": "d",
    "n": "m",
    "r": "t",
    "u": "y",
}


# ── HumanSimulator ──────────────────────────────────────────────────────────────────


class HumanSimulator:
    """Injects human-like behaviour into Playwright page interactions.

    All timings are drawn from configurable ranges with random jitter to
    avoid deterministic fingerprints.

    Usage::

        sim = HumanSimulator()
        async with sim.simulate_human_behavior(page):
            await sim.human_type(page, "#name", "John Doe")
            await sim.random_scroll(page)
            await sim.sleep_between_actions()
    """

    def __init__(
        self,
        typing_min_ms: int = _DEFAULT_TYPING_MIN_MS,
        typing_max_ms: int = _DEFAULT_TYPING_MAX_MS,
        typo_probability: float = _DEFAULT_TYPO_PROBABILITY,
        pause_min_s: float = _DEFAULT_PAUSE_MIN_S,
        pause_max_s: float = _DEFAULT_PAUSE_MAX_S,
    ) -> None:
        self._typing_min_ms = typing_min_ms
        self._typing_max_ms = typing_max_ms
        self._typo_probability = typo_probability
        self._pause_min_s = pause_min_s
        self._pause_max_s = pause_max_s
        self._logger = logger.bind(component="HumanSimulator")

    # ── Timing ─────────────────────────────────────────────────────────────────────

    async def random_delay(self, min_ms: int = _DEFAULT_TYPING_MIN_MS, max_ms: int = _DEFAULT_TYPING_MAX_MS) -> None:
        """Sleep for a random duration between *min_ms* and *max_ms*.

        Applies exponential jitter so that repeated calls produce a
        natural-looking distribution of inter-arrival times.
        """
        jitter = random.expovariate(1.0 / ((min_ms + max_ms) / 2.0))
        delay_ms = max(min_ms, min(max_ms, jitter))
        await asyncio.sleep(delay_ms / 1000.0)

    async def sleep_between_actions(self) -> None:
        """Pause for a random duration simulating reading / thinking time.

        Typical range is 1–3 seconds, mimicking the time a human spends
        scanning the next section of a form.
        """
        duration = random.uniform(self._pause_min_s, self._pause_max_s)
        self._logger.debug("Sleeping between actions", duration_s=round(duration, 2))
        await asyncio.sleep(duration)

    # ── Mouse movements ─────────────────────────────────────────────────────────────

    async def random_mouse_movement(
        self,
        page: Page,
        target_element: ElementHandle | Locator | None = None,
        *,
        target_x: int | None = None,
        target_y: int | None = None,
    ) -> None:
        """Move the mouse along a randomised cubic Bezier curve to a target.

        The curve has randomised control points, giving each movement a
        distinct arc rather than a robotic straight line.

        Args:
            page: Playwright page to move the mouse on.
            target_element: An element to move towards (its centre is used
                as the destination).  Mutually exclusive with *target_x/Y*.
            target_x: Absolute X coordinate for the destination.
            target_y: Absolute Y coordinate for the destination.
        """
        # Resolve destination coordinates.
        if target_element is not None:
            box = await target_element.bounding_box()
            if box is None:
                self._logger.warning("Cannot resolve bounding box for target — skipping movement")
                return
            end_x = box["x"] + box["width"] / 2
            end_y = box["y"] + box["height"] / 2
        elif target_x is not None and target_y is not None:
            end_x = float(target_x)
            end_y = float(target_y)
        else:
            self._logger.debug("No target specified — moving to random viewport position")
            viewport = page.viewport_size
            end_x = random.uniform(0, viewport["width"] if viewport else 800)
            end_y = random.uniform(0, viewport["height"] if viewport else 600)

        # Current cursor position (fall back to viewport centre).
        try:
            current_pos = await page.evaluate("({x: window.mouseX || 0, y: window.mouseY || 0})")
        except Exception:
            current_pos = {"x": 0, "y": 0}

        start_x = float(current_pos.get("x", 0))
        start_y = float(current_pos.get("y", 0))

        # Generate control points for the Bezier curve.
        cp1_x = start_x + (end_x - start_x) * 0.25 + random.uniform(-50, 50)
        cp1_y = start_y + (end_y - start_y) * 0.1 + random.uniform(-30, 30)
        cp2_x = start_x + (end_x - start_x) * 0.75 + random.uniform(-50, 50)
        cp2_y = start_y + (end_y - start_y) * 0.9 + random.uniform(-30, 30)

        # Walk the curve and move the mouse.
        for i in range(1, _BEZIER_POINTS + 1):
            t = i / _BEZIER_POINTS
            x = (
                (1 - t) ** 3 * start_x
                + 3 * (1 - t) ** 2 * t * cp1_x
                + 3 * (1 - t) * t**2 * cp2_x
                + t**3 * end_x
            )
            y = (
                (1 - t) ** 3 * start_y
                + 3 * (1 - t) ** 2 * t * cp1_y
                + 3 * (1 - t) * t**2 * cp2_y
                + t**3 * end_y
            )
            await page.mouse.move(x, y)
            await self.random_delay(5, 15)

    # ── Typing ──────────────────────────────────────────────────────────────────────

    async def human_type(
        self,
        page: Page,
        selector_or_element: str | ElementHandle | Locator,
        text: str,
        *,
        clear_first: bool = True,
    ) -> None:
        """Type *text* into a form field with human-like timing and typos.

        Each keystroke is delayed by 50–120 ms.  With low probability
        (``typo_probability``) a nearby-key typo is typed and then corrected
        with Backspace, simulating natural typing errors.

        Args:
            page: Playwright page.
            selector_or_element: CSS selector, ``ElementHandle``, or ``Locator``
                identifying the input field.
            text: The text to type.
            clear_first: Whether to triple-click / clear the field first.
        """
        # Resolve to a Locator.
        if isinstance(selector_or_element, str):
            locator = page.locator(selector_or_element)
        elif isinstance(selector_or_element, ElementHandle):
            locator = selector_or_element.as_locator()
        else:
            locator = selector_or_element

        # Bring the field into view and focus it.
        await locator.scroll_into_view_if_needed()
        await self.random_delay(30, 80)
        await locator.focus()
        await self.random_delay(20, 50)

        if clear_first:
            await locator.click(click_count=3)  # Triple-click to select all.
            await page.keyboard.press("Backspace")
            await self.random_delay(30, 70)

        # Type each character.
        for char in text:
            # Simulate occasional typo.
            if char.isalpha() and random.random() < self._typo_probability:
                typo_char = _TYPO_MAP.get(char.lower(), char.lower())
                if char.isupper():
                    typo_char = typo_char.upper()
                await page.keyboard.press(typo_char)
                await self.random_delay(60, 100)
                await page.keyboard.press("Backspace")
                await self.random_delay(40, 80)

            await page.keyboard.press(char)
            await self.random_delay(self._typing_min_ms, self._typing_max_ms)

        self._logger.debug(
            "Human typing complete",
            text_length=len(text),
            field=str(locator),
        )

    # ── Scrolling ───────────────────────────────────────────────────────────────────

    async def random_scroll(self, page: Page, *, steps: int = _DEFAULT_SCROLL_STEPS) -> None:
        """Scroll the page with random speed and distance.

        Simulates a user scanning the page: scrolls by a random amount,
        pauses, then may scroll again.
        """
        viewport_height = page.viewport_size.get("height", 800) if page.viewport_size else 800

        for _ in range(random.randint(1, steps)):
            scroll_distance = random.randint(int(viewport_height * 0.3), int(viewport_height * 0.8))
            # Alternate direction occasionally.
            if random.random() < 0.15:
                scroll_distance = -scroll_distance

            await page.evaluate(f"window.scrollBy({{top: {scroll_distance}, behavior: 'smooth'}})")

            # Pause after each scroll burst (reading time).
            await asyncio.sleep(random.uniform(0.3, 1.2))

    # ── Viewport variation ──────────────────────────────────────────────────────────

    def random_viewport_size(self) -> dict[str, int]:
        """Return a slightly randomised viewport size.

        The configured dimensions are perturbed by up to ±20 px so that
        every context appears to come from a slightly different screen.
        """
        settings = get_settings()
        width = settings.browser.viewport_width + random.randint(-20, 20)
        height = settings.browser.viewport_height + random.randint(-20, 20)
        return {"width": max(640, width), "height": max(480, height)}

    # ── Context manager ─────────────────────────────────────────────────────────────

    @asynccontextmanager
    async def simulate_human_behavior(self, page: Page) -> AsyncGenerator[None, None]:
        """Context manager that applies humanisation to all interactions.

        Usage::

            async with sim.simulate_human_behavior(page):
                # Every interaction inside this block is humanised.
                ...
        """
        # Before entering: inject random scroll + pause.
        await self.random_delay(100, 300)
        await self.random_scroll(page)
        await self.sleep_between_actions()

        try:
            yield
        finally:
            # After leaving: brief pause before next action.
            await self.random_delay(50, 150)

    # ── High-level helpers ──────────────────────────────────────────────────────────

    async def click_element(
        self,
        page: Page,
        selector_or_element: str | ElementHandle | Locator,
    ) -> None:
        """Move the mouse naturally to an element and click it.

        Combines a Bezier movement with a randomised click pause.
        """
        # Resolve to Locator.
        if isinstance(selector_or_element, str):
            locator = page.locator(selector_or_element).first
            element_handle = await locator.element_handle()
        elif isinstance(selector_or_element, ElementHandle):
            element_handle = selector_or_element
            locator = element_handle.as_locator()
        else:
            locator = selector_or_element
            element_handle = await locator.element_handle()

        if element_handle is None:
            self._logger.warning("Cannot find element for click", selector=str(selector_or_element))
            return

        await self.random_mouse_movement(page, target_element=element_handle)
        await self.random_delay(10, 30)
        await locator.click()
        await self.random_delay(20, 60)
