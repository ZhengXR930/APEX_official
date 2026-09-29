"""Compatibility helpers for sync Agent SDK entry points."""
from __future__ import annotations

import asyncio


def ensure_event_loop() -> None:
    """Ensure ``asyncio.get_event_loop()`` works in the current thread.

    Some sync SDK wrappers still call ``get_event_loop`` directly.  On newer
    Python versions a previous ``asyncio.run`` can leave the main thread
    without a current loop, so create one only when the policy has none.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
        return
    # ``asyncio.run`` closes the loop it installs but older sync wrappers may
    # leave that closed object as the current policy loop.  Merely calling
    # ``get_event_loop`` then succeeds, while the next ``run_until_complete``
    # fails with ``RuntimeError: Event loop is closed``.
    if loop.is_closed():
        asyncio.set_event_loop(asyncio.new_event_loop())
