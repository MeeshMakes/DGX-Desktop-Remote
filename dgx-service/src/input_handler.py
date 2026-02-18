"""
dgx-service/src/input_handler.py
Inject mouse and keyboard input into the DGX X session using xdotool.
"""

import subprocess
import shutil
import logging
from typing import Optional

log = logging.getLogger(__name__)

_XDOTOOL = shutil.which("xdotool")

# Map PC Qt key names → xdotool key names (subset covering common keys)
_KEY_MAP: dict[str, str] = {
    "F1":  "F1",  "F2":  "F2",  "F3":  "F3",  "F4":  "F4",
    "F5":  "F5",  "F6":  "F6",  "F7":  "F7",  "F8":  "F8",
    "F9":  "F9",  "F10": "F10", "F11": "F11", "F12": "F12",
    "Return":  "Return",  "Enter":  "Return",
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
}

# X11 mouse button numbers
_MOUSE_BTN = {
    "left":   1,
    "middle": 2,
    "right":  3,
    "x1":     8,
    "x2":     9,
}


def _xdo(*args: str) -> bool:
    """Run xdotool with given args. Returns True on success."""
    if not _XDOTOOL:
        log.error("xdotool not found — cannot inject input")
        return False
    try:
        subprocess.run(
            [_XDOTOOL, *args],
            check=True,
            capture_output=True,
            timeout=0.5,
        )
        return True
    except subprocess.CalledProcessError as e:
        log.warning("xdotool error: %s", e.stderr.decode(errors="replace"))
        return False
    except subprocess.TimeoutExpired:
        log.warning("xdotool timed out")
        return False


class InputHandler:
    """Public API called by the RPC handler for each input event."""

    @staticmethod
    def mouse_move(x: int, y: int, absolute: bool = True):
        if absolute:
            _xdo("mousemove", "--sync", str(x), str(y))
        else:
            _xdo("mousemove_relative", "--sync", str(x), str(y))

    @staticmethod
    def mouse_press(button: str             = "left"):
        btn = _MOUSE_BTN.get(button.lower(), 1)
        _xdo("mousedown", str(btn))

    @staticmethod
    def mouse_release(button: str           = "left"):
        btn = _MOUSE_BTN.get(button.lower(), 1)
        _xdo("mouseup", str(btn))

    @staticmethod
    def mouse_click(button: str             = "left"):
        btn = _MOUSE_BTN.get(button.lower(), 1)
        _xdo("click", str(btn))

    @staticmethod
    def mouse_scroll(dx: int, dy: int):
        """Positive dy = scroll down (button 5), negative = scroll up (button 4)."""
        if dy > 0:
            for _ in range(abs(dy)):
                _xdo("click", "5")
        elif dy < 0:
            for _ in range(abs(dy)):
                _xdo("click", "4")
        if dx > 0:
            for _ in range(abs(dx)):
                _xdo("click", "7")
        elif dx < 0:
            for _ in range(abs(dx)):
                _xdo("click", "6")

    @staticmethod
    def key_press(key: str):
        xkey = _KEY_MAP.get(key, key)
        _xdo("keydown", xkey)

    @staticmethod
    def key_release(key: str):
        xkey = _KEY_MAP.get(key, key)
        _xdo("keyup", xkey)

    @staticmethod
    def type_text(text: str):
        """Type a string using xdotool type (good for printable ASCII)."""
        _xdo("type", "--clearmodifiers", "--delay", "0", text)
