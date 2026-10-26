"""Is anyone using the site? Background network work (data refresh, live feed, history) runs only while the answer is yes.

A free host counts every byte the server sends and receives, so the server must not keep pulling data when nobody looks.
Every /api/ request marks the site as in use; after UNDERTOW_IDLE_SECONDS (default 600) without one, the background
threads pause in `wait()` until the next visitor. The page itself ("/") does not count, because the host's health check
requests it every few seconds. UNDERTOW_IDLE_SECONDS=0 turns pausing off (always active).
"""
from __future__ import annotations

import os
import time

IDLE_SECONDS = float(os.environ.get("UNDERTOW_IDLE_SECONDS", "600"))
_last = time.time()  # active at start, so the first data arrives before the first visitor


def touch() -> None:
    global _last
    _last = time.time()


def active() -> bool:
    return IDLE_SECONDS <= 0 or time.time() - _last < IDLE_SECONDS


def wait(poll: float = 2.0) -> None:
    """Block until someone is using the site."""
    while not active():
        time.sleep(poll)
