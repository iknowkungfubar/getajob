"""Playwright / Chromium availability check - zero browser dependencies.

This module exists so the availability check can be imported without
triggering ``import playwright`` or ``import browser_use``, both of
which are only needed at runtime when the browser is actually launched.
"""

from __future__ import annotations as _annotations

__all__: list[str] = [
    "is_available",
]


def is_available() -> bool:
    """Check whether Playwright / Chromium is installed and usable.

    This is a *silent* check - it never prints, logs, or raises.  Use it
    before launching the browser engine so callers can fall back gracefully
    when the dependencies are missing.

    Returns:
        ``True`` if both ``playwright`` and ``browser_use`` can be imported
        without error, ``False`` otherwise.
    """
    import importlib.util

    return (
        importlib.util.find_spec("browser_use") is not None
        and importlib.util.find_spec("playwright") is not None
    )
