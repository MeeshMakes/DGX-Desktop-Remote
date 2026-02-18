"""
dgx-service/src/input_handler.py

Inject mouse and keyboard input into the DGX X session.

PRIMARY:  python-xlib XTest extension — persistent X connection,
          direct socket call per event, sub-millisecond latency.
FALLBACK: xdotool subprocess (--sync removed to not block the input loop).

The critical requirement is that mouse_move is processed as fast as
events arrive (~100-165 Hz from the PC).  The old xdotool approach
forked a new subprocess per event with --sync (waiting for X ack),
costing ~10-50 ms each and causing the severe cursor lag.
"""

import logging
import shutil
import subprocess
import threading
from typing import Optional

log = logging.getLogger(__name__)

# X11 mouse button numbers
_MOUSE_BTN = {
    "left":   1,
    "middle": 2,
    "right":  3,
    "x1":     8,
    "x2":     9,
}

# Qt key name → X keysym name
_XLIB_KEY_MAP: dict[str, str] = {
    "Return":    "Return",
    "Enter":     "Return",
    "BackSpace": "BackSpace",
    "Tab":       "Tab",
    "Escape":    "Escape",
    "Delete":    "Delete",
    "Insert":    "Insert",
    "Home":      "Home",
    "End":       "End",
    "Page_Up":   "Page_Up",
    "Page_Down": "Page_Down",
    "Left":      "Left",
    "Right":     "Right",
    "Up":        "Up",
    "Down":      "Down",
    "space":     "space",
    "Control":   "Control_L",
    "Alt":       "Alt_L",
    "Shift":     "Shift_L",
    "Super":     "Super_L",
    "CapsLock":  "Caps_Lock",
    "NumLock":   "Num_Lock",
    "F1":  "F1",  "F2":  "F2",  "F3":  "F3",  "F4":  "F4",
    "F5":  "F5",  "F6":  "F6",  "F7":  "F7",  "F8":  "F8",
    "F9":  "F9",  "F10": "F10", "F11": "F11", "F12": "F12",
}

# Qt key name → xdotool key name (fallback only)
_XDOTOOL_KEY_MAP: dict[str, str] = {
    "Return":    "Return",
    "Enter":     "Return",
    "BackSpace": "BackSpace",
    "Tab":       "Tab",
    "Escape":    "Escape",
    "Delete":    "Delete",
    "Insert":    "Insert",
    "Home":      "Home",
    "End":       "End",
    "Page_Up":   "Page_Up",
    "Page_Down": "Page_Down",
    "Left":      "Left",
    "Right":     "Right",
    "Up":        "Up",
    "Down":      "Down",
    "space":     "space",
    "Control":   "ctrl",
    "Alt":       "alt",
    "Shift":     "shift",
    "Super":     "super",
    "CapsLock":  "Caps_Lock",
    "NumLock":   "Num_Lock",
    "F1":  "F1",  "F2":  "F2",  "F3":  "F3",  "F4":  "F4",
    "F5":  "F5",  "F6":  "F6",  "F7":  "F7",  "F8":  "F8",
    "F9":  "F9",  "F10": "F10", "F11": "F11", "F12": "F12",
}


# ── Backend: python-xlib XTest (fast path) ────────────────────────────

class _XlibBackend:
    """
    Direct X11 input via XTest extension.  No subprocesses.
    Single persistent Display connection; lock protects concurrent calls.
    Each mouse_move is a direct socket write to X — takes ~10-50 µs,
    not 10-50 ms like a subprocess fork.
    """

    def __init__(self):
        from Xlib import display as _Disp, X as _X
        from Xlib.ext import xtest as _xtest
        self._X     = _X
        self._xtest = _xtest
        self._dpy   = _Disp.Display()
        self._lock  = threading.Lock()
        if not self._dpy.has_extension("XTEST"):
            raise RuntimeError("X server does not support XTEST extension")
        log.info("XTest input backend ready (python-xlib) — zero subprocess overhead")

    def _fi(self, event_type: int, detail: int = 0, x: int = 0, y: int = 0):
        with self._lock:
            self._xtest.fake_input(self._dpy, event_type, detail=detail, x=x, y=y)
            self._dpy.flush()

    def mouse_move(self, x: int, y: int):
        self._fi(self._X.MotionNotify, x=x, y=y)

    def mouse_press(self, button: str):
        self._fi(self._X.ButtonPress, detail=_MOUSE_BTN.get(button.lower(), 1))

    def mouse_release(self, button: str):
        self._fi(self._X.ButtonRelease, detail=_MOUSE_BTN.get(button.lower(), 1))

    def mouse_scroll(self, dx: int, dy: int):
        # 4=scroll-up 5=scroll-down 6=scroll-left 7=scroll-right
        btns: list[int] = []
        if dy < 0:
            btns += [4] * abs(dy)
        elif dy > 0:
            btns += [5] * abs(dy)
        if dx < 0:
            btns += [6] * abs(dx)
        elif dx > 0:
            btns += [7] * abs(dx)
        with self._lock:
            for b in btns:
                self._xtest.fake_input(self._dpy, self._X.ButtonPress,   detail=b)
                self._xtest.fake_input(self._dpy, self._X.ButtonRelease, detail=b)
            self._dpy.flush()

    def key_press(self, key: str):
        kc = self._keycode(key)
        if kc:
            self._fi(self._X.KeyPress, detail=kc)

    def key_release(self, key: str):
        kc = self._keycode(key)
        if kc:
            self._fi(self._X.KeyRelease, detail=kc)

    def _keycode(self, key: str) -> int:
        from Xlib import XK
        for name in (_XLIB_KEY_MAP.get(key, key), key):
            sym = XK.string_to_keysym(name)
            if sym:
                kc = self._dpy.keysym_to_keycode(sym)
                if kc:
                    return kc
        log.debug("No keycode for %r", key)
        return 0


# ── Backend: xdotool subprocess (fallback) ────────────────────────────

class _XdotoolBackend:
    """Subprocess fallback. --sync removed so the input loop doesn't block."""

    _exe: Optional[str] = shutil.which("xdotool")

    def _run(self, *args: str):
        if not self._exe:
            log.error("xdotool not found")
            return
        try:
            subprocess.run([self._exe, *args], capture_output=True, timeout=0.3)
        except Exception as e:
            log.debug("xdotool: %s", e)

    def mouse_move(self, x: int, y: int):
        self._run("mousemove", str(x), str(y))   # no --sync

    def mouse_press(self, button: str):
        self._run("mousedown", str(_MOUSE_BTN.get(button.lower(), 1)))

    def mouse_release(self, button: str):
        self._run("mouseup", str(_MOUSE_BTN.get(button.lower(), 1)))

    def mouse_scroll(self, dx: int, dy: int):
        if dy < 0:
            for _ in range(abs(dy)): self._run("click", "4")
        elif dy > 0:
            for _ in range(abs(dy)): self._run("click", "5")
        if dx < 0:
            for _ in range(abs(dx)): self._run("click", "6")
        elif dx > 0:
            for _ in range(abs(dx)): self._run("click", "7")

    def key_press(self, key: str):
        self._run("keydown", _XDOTOOL_KEY_MAP.get(key, key))

    def key_release(self, key: str):
        self._run("keyup", _XDOTOOL_KEY_MAP.get(key, key))


# ── Auto-select backend on import ─────────────────────────────────────

def _make_backend():
    try:
        return _XlibBackend()
    except Exception as e:
        log.warning("python-xlib XTest unavailable (%s) — falling back to xdotool", e)
        return _XdotoolBackend()


# ── Public API ────────────────────────────────────────────────────────

class InputHandler:
    """
    Single instance per DGXService.  Backend selected once at startup.
    All methods are thread-safe (backend handles its own locking).
    """

    def __init__(self):
        self._backend = _make_backend()

    def mouse_move(self, x: int, y: int, absolute: bool = True):
        self._backend.mouse_move(x, y)

    def mouse_press(self, button: str = "left"):
        self._backend.mouse_press(button)

    def mouse_release(self, button: str = "left"):
        self._backend.mouse_release(button)

    def mouse_click(self, button: str = "left"):
        self._backend.mouse_press(button)
        self._backend.mouse_release(button)

    def mouse_scroll(self, dx: int, dy: int):
        self._backend.mouse_scroll(dx, dy)

    def key_press(self, key: str):
        self._backend.key_press(key)

    def key_release(self, key: str):
        self._backend.key_release(key)

    def type_text(self, text: str):
        for ch in text:
            self._backend.key_press(ch)
            self._backend.key_release(ch)
