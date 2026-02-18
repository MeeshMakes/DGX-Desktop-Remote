"""
dgx-service/src/resolution_monitor.py
Watch for X11 display resolution changes via xrandr and notify a callback.
"""

import subprocess
import threading
import re
import time
import logging
from typing import Callable, Optional, Tuple

log = logging.getLogger(__name__)


def _get_xrandr_current() -> Tuple[int, int]:
    """Parse xrandr --current and return (width, height) of primary/first connected output."""
    try:
        out = subprocess.check_output(
            ["xrandr", "--current"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode(errors="replace")
    except Exception:
        return (1920, 1080)

    # Match lines like: "   1920x1080+0+0  *current"
    # or primary screen resolution: e.g. "Screen 0: ... current 1920 x 1080"
    m = re.search(r"current\s+(\d+)\s+x\s+(\d+)", out)
    if m:
        return int(m.group(1)), int(m.group(2))

    # Fallback: first connected mode with *
    m = re.search(r"\s+(\d+)x(\d+)\s+.*\*", out)
    if m:
        return int(m.group(1)), int(m.group(2))

    return (1920, 1080)


class ResolutionMonitor:
    """
    Polls xrandr every 2 s and fires on_change(new_w, new_h) when resolution changes.
    """

    def __init__(self, poll_interval: float = 2.0):
        self._interval = poll_interval
        self._running  = False
        self._thread: Optional[threading.Thread] = None
        self._cb: Optional[Callable[[int, int], None]] = None
        self._current: Tuple[int, int] = (0, 0)

    @property
    def current(self) -> Tuple[int, int]:
        return self._current

    def start(self, on_change: Callable[[int, int], None]):
        self._cb      = on_change
        self._current = _get_xrandr_current()
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="ResolutionMonitor"
        )
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _loop(self):
        while self._running:
            time.sleep(self._interval)
            try:
                new = _get_xrandr_current()
                if new != self._current:
                    log.info("Resolution changed: %sx%s → %sx%s",
                             self._current[0], self._current[1], new[0], new[1])
                    self._current = new
                    if self._cb:
                        self._cb(new[0], new[1])
            except Exception as e:
                log.warning("ResolutionMonitor error: %s", e)
