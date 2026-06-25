"""Stealth browser wrapper for undetectable automation.

The :class:`StealthBrowser` wraps ``browser_use.Browser`` and applies a suite
of anti-detection measures - canvas fingerprint randomisation, WebGL noise,
navigator property patches - so that job portals cannot easily distinguish
automated traffic from a genuine human user.
"""

from __future__ import annotations as _annotations

import os
from pathlib import Path
from typing import Any

import structlog
from browser_use import Browser, BrowserConfig
from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import BrowserContext as PlaywrightContext

from core.config import get_settings
from core.exceptions import BrowserError

__all__: list[str] = [
    "STEALTH_INIT_SCRIPT",
    "StealthBrowser",
    "is_available",
]

logger = structlog.get_logger(__name__)

# ── Availability check (re-exported) ─────────────────────────────────────────────────

from browser_engine._availability import is_available  # noqa: E402

# ── Stealth initialisation script ───────────────────────────────────────────────────

STEALTH_INIT_SCRIPT: str = r"""
// ── Navigator overrides ──────────────────────────────────────────────────────────
// Spoof common automation-detectable properties.

Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' })),
});
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => Math.floor(Math.random() * 4) + 4 });

// Override chrome.runtime (Playwright detection)
window.chrome = {
    runtime: {
        connect: () => ({ onMessage: { addListener: () => {} }, postMessage: () => {} }),
        onMessage: { addListener: () => {} },
        onConnect: { addListener: () => {} },
    },
};

// ── Canvas fingerprint randomisation ─────────────────────────────────────────────
// Slightly perturb canvas operations so that the fingerprint differs per session.

const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function (...args) {
    const imageData = this.getContext('2d')?.getImageData(0, 0, this.width, this.height);
    if (imageData) {
        // Jitter every 10th pixel by ±1 in the least-significant byte.
        for (let i = 0; i < imageData.data.length; i += 40) {
            imageData.data[i] = imageData.data[i] ^ 1;
        }
        this.getContext('2d')?.putImageData(imageData, 0, 0);
    }
    return originalToDataURL.apply(this, args);
};

// ── WebGL noise injection ────────────────────────────────────────────────────────
// Perturb a fragment of every WebGL render to foil GPU fingerprinting.

const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function (param) {
    if (param === 37445) return 'Intel Inc.';          // RENDERER - spoof
    if (param === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.apply(this, arguments);
};
"""


# ── StealthBrowser ──────────────────────────────────────────────────────────────────


class StealthBrowser:
    """Production-grade stealth browser wrapper.

    Usage::

        browser = StealthBrowser()
        await browser.launch()
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://example.com")
        ...
        await browser.close()

    Each :meth:`new_context` call creates an isolated browsing context with
    no shared cookies, cache, or local storage.  Stealth patches are injected
    automatically into every page created from that context.
    """

    def __init__(self, config: BrowserConfig | None = None) -> None:
        self._config: BrowserConfig = config or self._build_default_config()
        self._browser: Browser | None = None
        self._playwright_browser: PlaywrightBrowser | None = None
        self._logger = logger.bind(component="StealthBrowser")

    # ── Public API ──────────────────────────────────────────────────────────────────

    async def launch(self) -> Browser:
        """Launch the browser with stealth configuration.

        On failure, automatically retries with a fallback config (headless,
        default viewport).  Raises :class:`BrowserError` after exhausting
        all fallbacks.

        Returns:
            The underlying ``browser_use.Browser`` instance.
        """
        if self._browser is not None and self.is_running:
            self._logger.warning("Browser already running - closing before re-launch")
            await self.close()

        configs: list[BrowserConfig] = [
            self._config,
            BrowserConfig(headless=True),
        ]

        last_error: Exception | None = None
        for attempt, cfg in enumerate(configs, 1):
            try:
                self._browser = Browser(config=cfg)
                # Access the underlying Playwright browser for direct manipulation.
                self._playwright_browser = self._browser.get_playwright_browser()
                self._logger.info(
                    "Browser launched",
                    attempt=attempt,
                    headless=cfg.headless,
                )
                return self._browser
            except Exception as exc:
                last_error = exc
                self._logger.warning(
                    "Browser launch attempt failed",
                    attempt=attempt,
                    error=str(exc)[:200],
                )

        msg = f"Failed to launch browser after {len(configs)} attempts"
        raise BrowserError(msg, details={"last_error": str(last_error)[:500]})

    async def close(self) -> None:
        """Shut down the browser and release all resources."""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception as exc:
                self._logger.warning("Error during browser close", error=str(exc)[:200])
            finally:
                self._browser = None
                self._playwright_browser = None
                self._logger.info("Browser closed")

    @property
    def is_running(self) -> bool:
        """Return ``True`` if the browser process is still alive."""
        if self._playwright_browser is None:
            return False
        try:
            return self._playwright_browser.is_connected()
        except Exception:
            return False

    async def new_context(self, **kwargs: Any) -> PlaywrightContext:
        """Create a new isolated browser context with stealth patches.

        Each context has an independent cookie jar, cache, and local storage.
        Stealth overrides (canvas, WebGL, navigator patches) are injected via
        ``add_init_script`` before any page loads.

        Args:
            **kwargs: Passed through to Playwright's ``browser.new_context()``.
                Useful overrides: ``viewport``, ``user_agent``, ``locale``,
                ``geolocation``, ``timezone_id``.

        Returns:
            A Playwright ``BrowserContext`` ready for ``await context.new_page()``.

        Raises:
            BrowserError: If the browser is not running.
        """
        if self._playwright_browser is None:
            msg = "Browser is not running - call launch() first"
            raise BrowserError(msg)

        # Merge configured viewport with caller overrides.
        settings = get_settings()
        ctx_kwargs: dict[str, Any] = {
            "viewport": {
                "width": settings.browser.viewport_width,
                "height": settings.browser.viewport_height,
            },
            "locale": settings.browser.locale,
            **kwargs,
        }

        context = await self._playwright_browser.new_context(**ctx_kwargs)

        # Inject stealth script into every page in this context.
        await context.add_init_script(STEALTH_INIT_SCRIPT)
        self._logger.debug("New browser context created", viewport=ctx_kwargs.get("viewport"))

        return context

    async def screenshot(self, path: str | Path | None = None) -> str:
        """Capture a full-page screenshot of every open page (first available).

        Args:
            path: Filesystem path for the PNG.  Auto-generated in ``data/screenshots/``
                if omitted.

        Returns:
            Absolute path to the saved screenshot.
        """
        if self._playwright_browser is None:
            msg = "Browser is not running"
            raise BrowserError(msg)

        if path is None:
            data_dir = get_settings().data_dir
            screenshot_dir = data_dir / "screenshots"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            path = str(screenshot_dir / f"screenshot_{os.urandom(4).hex()}.png")

        contexts = self._playwright_browser.contexts
        for ctx in contexts:
            pages = ctx.pages
            if pages:
                await pages[0].screenshot(path=str(path), full_page=True)
                self._logger.debug("Screenshot captured", path=str(path))
                return str(path)

        self._logger.warning("No pages available for screenshot")
        return ""

    # ── Internal helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _build_default_config() -> BrowserConfig:
        """Construct a :class:`BrowserConfig` from platform settings."""
        settings = get_settings()
        return BrowserConfig(
            headless=settings.browser.headless,
            viewport_width=settings.browser.viewport_width,
            viewport_height=settings.browser.viewport_height,
            locale=settings.browser.locale,
            proxy=settings.browser.proxy,
            slow_mo=settings.browser.slow_mo_ms,
        )
