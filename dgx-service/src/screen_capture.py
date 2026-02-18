"""
dgx-service/src/screen_capture.py
Continuous JPEG frame pump using mss + Pillow.
Designed to be called from a thread; pushes frames to a callback.
"""

import io
import threading
import time
from typing import Callable, Optional

try:
    import mss
    import mss.tools
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class ScreenCapture:
    """
    Captures the primary monitor at a target FPS and yields JPEG bytes.

    Usage:
        cap = ScreenCapture(monitor_index=1, fps=60, quality=85)
        cap.start(on_frame_cb)
        ...
        cap.stop()
    """

    def __init__(
        self,
        monitor_index: int = 1,
        fps: int = 60,
        quality: int = 85,
    ):
        if not HAS_MSS:
            raise RuntimeError("mss is not installed — run: pip install mss")
        if not HAS_PIL:
            raise RuntimeError("Pillow is not installed — run: pip install Pillow")

        self._monitor_index = monitor_index
        self._fps           = max(1, min(fps, 120))
        self._quality       = max(40, min(quality, 100))
        self._running       = False
        self._thread: Optional[threading.Thread] = None
        self._cb: Optional[Callable[[bytes, int, int], None]] = None
        self._frame_interval = 1.0 / self._fps

    @property
    def running(self) -> bool:
        return self._running

    def set_params(self, fps: int = None, quality: int = None):
        if fps      is not None: self._fps     = fps;     self._frame_interval = 1.0 / fps
        if quality  is not None: self._quality = quality

    def start(self, on_frame: Callable[[bytes, int, int], None]):
        """
        Start capture.
        on_frame(jpeg_bytes, width, height) called from capture thread.
        """
        if self._running:
            return
        self._cb      = on_frame
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True, name="ScreenCapture")
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ------------------------------------------------------------------
    # Capture loop
    # ------------------------------------------------------------------

    def _loop(self):
        with mss.mss() as sct:
            monitors = sct.monitors   # index 0 = all, 1+ = individual
            if self._monitor_index >= len(monitors):
                mon = monitors[1]
            else:
                mon = monitors[self._monitor_index]

            width  = mon["width"]
            height = mon["height"]

            while self._running:
                t0 = time.monotonic()

                # Grab raw BGRA screenshot
                raw     = sct.grab(mon)
                img     = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

                # Encode to JPEG — high quality, no chroma subsampling
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=self._quality,
                         subsampling=0, optimize=False)
                jpeg = buf.getvalue()

                if self._cb:
                    self._cb(jpeg, width, height)

                # Throttle
                elapsed = time.monotonic() - t0
                sleep   = self._frame_interval - elapsed
                if sleep > 0:
                    time.sleep(sleep)

    # ------------------------------------------------------------------
    # Screen info
    # ------------------------------------------------------------------

    def get_resolution(self) -> tuple[int, int]:
        """Return (width, height) of capture monitor."""
        with mss.mss() as sct:
            monitors = sct.monitors
            if self._monitor_index >= len(monitors):
                mon = monitors[1]
            else:
                mon = monitors[self._monitor_index]
            return mon["width"], mon["height"]
