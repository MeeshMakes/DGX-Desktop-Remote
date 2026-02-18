# DGX Desktop Remote — Advanced Display Architecture

**Version**: 2.0  
**Status**: Implementation-Ready  
**Last Updated**: February 18, 2026

---

## 1. Overview

DGX Desktop Remote supports two distinct display modes, switchable from the PC Manager:

| Mode | Description | How Input Works |
|------|-------------|-----------------|
| **Window Mode** | DGX desktop shown in a floating window on the PC | Mouse/keyboard forwarded only when cursor is inside the window |
| **Virtual Display Mode** | DGX appears as if it is a 4th Windows monitor — cursor flows off the edge of a real monitor into DGX seamlessly | Windows low-level mouse hook intercepts cursor leaving the monitor edge |

Both modes share the same video pipeline (JPEG frames → `VideoCanvas`). The differences are entirely in **how input is intercepted** and **how the window behaves**.

---

## 2. Coordinate System

### 2.1 The Challenge

The DGX may be running at any resolution (e.g. 3840×2160). The PC window displays that resolution in a much smaller area (e.g. a 1280×720 window). Coordinates must be mapped in both directions with no rounding error accumulation.

### 2.2 CoordinateMapper

```python
# pc-application/src/display/coordinate_mapper.py

from dataclasses import dataclass


@dataclass
class CoordinateMapper:
    """
    Maps between:
    (A) DGX native resolution   (dgx_w × dgx_h)
    (B) Canvas render size      (canvas_w x canvas_h)
    (C) Relative [0.0 – 1.0]   (neutral, for letterboxed display)

    DGX resolution is authoritative. It is read from the hello handshake
    and updated live via resolution_changed RPC push events. Any time
    the DGX resolution changes, update dgx_w / dgx_h and the mapper
    recalculates automatically.
    """

    dgx_w: int = 1920
    dgx_h: int = 1080
    canvas_w: int = 1920        # Current rendered canvas pixel dimensions
    canvas_h: int = 1080        # Updated whenever window is resized

    # ── Relative ↔ DGX ────────────────────────────────────────────────

    def relative_to_dgx(self, rx: float, ry: float) -> tuple[int, int]:
        """
        Convert a relative position [0.0–1.0] inside the display area
        to absolute DGX pixel coordinates.
        rx, ry are already clamped to [0.0, 1.0] by VideoCanvas.
        """
        x = int(rx * (self.dgx_w - 1))
        y = int(ry * (self.dgx_h - 1))
        return x, y

    def dgx_to_relative(self, dx: int, dy: int) -> tuple[float, float]:
        """Convert DGX pixel coordinate to relative [0.0–1.0]."""
        return dx / (self.dgx_w - 1), dy / (self.dgx_h - 1)

    # ── Canvas ↔ DGX ──────────────────────────────────────────────────

    def canvas_to_dgx(self, cx: int, cy: int) -> tuple[int, int]:
        """Direct canvas pixel → DGX pixel (no letterbox compensation)."""
        rx = cx / max(self.canvas_w, 1)
        ry = cy / max(self.canvas_h, 1)
        return self.relative_to_dgx(rx, ry)

    def dgx_to_canvas(self, dx: int, dy: int) -> tuple[int, int]:
        """DGX pixel → canvas pixel."""
        cx = int(dx * self.canvas_w / self.dgx_w)
        cy = int(dy * self.canvas_h / self.dgx_h)
        return cx, cy

    # ── Virtual Display Mode ──────────────────────────────────────────

    def screen_to_dgx(self, sx: int, sy: int,
                      virt_x: int, virt_y: int) -> tuple[int, int]:
        """
        Convert absolute Windows screen coordinate (sx, sy) to DGX coord.
        virt_x, virt_y = top-left corner of the virtual DGX monitor rect
        in Windows screen space.
        """
        rel_x = (sx - virt_x) / self.dgx_w
        rel_y = (sy - virt_y) / self.dgx_h
        rel_x = max(0.0, min(1.0, rel_x))
        rel_y = max(0.0, min(1.0, rel_y))
        return self.relative_to_dgx(rel_x, rel_y)
```

### 2.3 Resolution Change Events (DGX → PC push)

The DGX service monitors the display resolution with a background thread. If the DGX resolution changes (user changes display settings, different GPU mode, etc.), the PC is notified immediately and the mapper is updated.

```python
# dgx-service/src/resolution_monitor.py

import threading, subprocess, re, time, logging
from typing import Optional, Callable

log = logging.getLogger("dgx.res_monitor")


class ResolutionMonitor(threading.Thread):
    """
    Polls xrandr every 2 seconds.
    If resolution changes, calls on_change(old, new).
    Also exposes the current resolution for hello response.
    """

    def __init__(self, on_change: Optional[Callable] = None):
        super().__init__(daemon=True)
        self.on_change   = on_change
        self._stop_event = threading.Event()
        self._current    = _query_xrandr()

    @property
    def current(self) -> dict:
        return self._current.copy()

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.wait(2.0):
            new = _query_xrandr()
            if new != self._current:
                old = self._current
                self._current = new
                log.info(f"Resolution changed: {old} → {new}")
                if self.on_change:
                    self.on_change(old, new)


def _query_xrandr() -> dict:
    """Returns {'width': int, 'height': int, 'refresh_hz': int} from xrandr."""
    try:
        out = subprocess.check_output(["xrandr"], timeout=2).decode()
        m   = re.search(r"(\d+)x(\d+)\s+([\d.]+)\*", out)
        if m:
            return {"width": int(m.group(1)), "height": int(m.group(2)),
                    "refresh_hz": int(float(m.group(3)))}
    except Exception:
        pass
    return {"width": 1920, "height": 1080, "refresh_hz": 60}
```

**PC side** — handle the push event in the RPC connection thread:

```python
# In rpc_handler.py control loop on PC side
# When a "resolution_changed" message arrives as a push (not a response)
# the RPC loop on PC side needs to handle unsolicited messages

# In connection.py, after recv_line in rpc():
if header.get("type") == "resolution_changed":
    new = header["new"]
    self._on_resolution_changed(new["width"], new["height"], new["refresh_hz"])
    # Read the next real response
    continue
```

---

## 3. Window Mode

### 3.1 Behavior

- The DGX display is rendered in a `QMainWindow`.
- When the PC user moves their cursor inside the window, Windows cursor is hidden (`Qt.CursorShape.BlankCursor`) and all mouse/keyboard events are forwarded to DGX.
- When cursor leaves the window, the Windows cursor is restored.
- The window can be pinned (Always on Top), resized, or fullscreened.
- The DGX desktop scales to fit the window, maintaining aspect ratio (letterboxed).

### 3.2 Cursor Tunnel

The cursor tunnel makes it feel like you are operating a second computer directly. The Windows cursor disappears inside the DGX window and the DGX cursor reflects where you are.

```python
# pc-application/src/display/video_canvas.py
# (Relevant methods only — full class in SYSTEM_DESIGN_SPECIFICATION.md)

    def enterEvent(self, event):
        """Cursor entered the DGX window → begin tunnel."""
        self._in_tunnel = True
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.setFocus()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Cursor left the DGX window → end tunnel."""
        self._in_tunnel = False
        self.unsetCursor()     # Restores default Windows cursor
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        """Forward cursor position to DGX in real coordinates."""
        if self.connection and self.connection.connected and self.mapper:
            # event.position() is in canvas pixels (float)
            pm = self.pixmap()
            if pm and not pm.isNull():
                # Account for letterbox offset inside QLabel
                off_x = (self.width()  - pm.width())  // 2
                off_y = (self.height() - pm.height()) // 2
                rel_x = (event.position().x() - off_x) / pm.width()
                rel_y = (event.position().y() - off_y) / pm.height()
                rel_x = max(0.0, min(1.0, rel_x))
                rel_y = max(0.0, min(1.0, rel_y))
                dx, dy = self.mapper.relative_to_dgx(rel_x, rel_y)
                self.connection.send_mouse_move(dx, dy)
        super().mouseMoveEvent(event)
```

### 3.3 Fullscreen Mode

The window can go fullscreen (F11 toggle), which removes the title bar and uses the entire monitor for the DGX display:

```python
# In MainWindow:

    def keyPressEvent(self, event):
        from PyQt6.QtCore import Qt
        if event.key() == Qt.Key.Key_F11:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
            return
        # ... forward other keys to DGX as normal ...
```

---

## 4. Virtual Display Mode

### 4.1 Concept

In Virtual Display Mode, the DGX desktop is presented as if it were a physical 4th monitor connected to the PC. The user can seamlessly move the cursor from a Windows monitor into the DGX area without clicking. Keyboard input follows the cursor.

This is achieved entirely in **software on the PC side** — no Windows Display Driver required:

1. A borderless, always-on-top window is positioned to the side of all physical monitors (in Windows multi-monitor coordinate space).
2. A **Windows global low-level mouse hook** (`WH_MOUSE_LL`) intercepts all mouse movement at the OS level.
3. When the hook detects the cursor moving into the virtual DGX monitor's area, it:
   - Constrains the Windows cursor to stay at the edge of the last real monitor
   - Translates further movement into DGX input events
4. When the user's intent is to return to Windows (cursor hits the opposite edge), it:
   - Releases the Windows cursor back to the real monitors
   - Stops sending input to DGX

### 4.2 Windows Mouse Hook (pywin32/ctypes)

```python
# pc-application/src/input/virtual_display.py

import ctypes
import ctypes.wintypes
import threading
import logging
from typing import Callable, Optional

log = logging.getLogger("pc.virtual_display")

# Windows constants
WH_MOUSE_LL   = 14
WM_MOUSEMOVE  = 0x0200
WM_LBUTTONDOWN= 0x0201
WM_LBUTTONUP  = 0x0202
WM_RBUTTONDOWN= 0x0204
WM_RBUTTONUP  = 0x0205
WM_MOUSEWHEEL = 0x020A
HC_ACTION     = 0

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt",          ctypes.wintypes.POINT),
        ("mouseData",   ctypes.wintypes.DWORD),
        ("flags",       ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class VirtualDisplayInputManager:
    """
    Installs a Windows low-level mouse hook.
    Intercepts mouse movement when cursor enters the virtual DGX display area.
    Forwards input to DGX and prevents the Windows cursor from entering that area.
    """

    def __init__(self,
                 connection,
                 mapper,
                 virtual_monitor_rect: dict):
        """
        virtual_monitor_rect: {"x": int, "y": int, "w": int, "h": int}
            Coordinates in Windows screen space where the virtual DGX monitor lives.
            Example: PC has monitors at 0-3840 wide.
            Virtual DGX at x=3840, y=0, w=1920, h=1080 → right side.
        """
        self.connection = connection
        self.mapper     = mapper
        self.rect       = virtual_monitor_rect
        self._hook_id   = None
        self._in_dgx    = False          # True when cursor is "in DGX"
        self._hook_proc = None           # Keep reference to prevent GC
        self._thread    = None
        # Edge where cursor exits DGX back to Windows
        self._exit_edge_x = 0           # Determined in start()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Install the hook and start the message pump thread."""
        self._exit_edge_x = self.rect["x"]   # Left edge of virtual monitor = exit point
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("Virtual display input manager started")

    def stop(self):
        """Remove hook and stop message pump."""
        if self._hook_id:
            user32.UnhookWindowsHookEx(self._hook_id)
            self._hook_id = None
        log.info("Virtual display input manager stopped")

    # ------------------------------------------------------------------
    # Hook installation (must run on its own thread — needs message pump)
    # ------------------------------------------------------------------

    def _run(self):
        proc = HOOKPROC(self._low_level_mouse_proc)
        self._hook_proc = proc   # Prevent garbage collection
        self._hook_id = user32.SetWindowsHookExA(
            WH_MOUSE_LL, proc, kernel32.GetModuleHandleW(None), 0
        )
        if not self._hook_id:
            log.error("Failed to install mouse hook")
            return

        # Message pump — required to dispatch hook callbacks
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageA(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageA(ctypes.byref(msg))

    # ------------------------------------------------------------------
    # Hook callback — called for every mouse event system-wide
    # ------------------------------------------------------------------

    def _low_level_mouse_proc(self, nCode: int, wParam: int, lParam: int) -> int:
        if nCode < HC_ACTION:
            return user32.CallNextHookEx(self._hook_id, nCode, wParam, lParam)

        hs = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        x, y = hs.pt.x, hs.pt.y

        r = self.rect
        inside_virtual = (r["x"] <= x < r["x"] + r["w"] and
                           r["y"] <= y < r["y"] + r["h"])

        if inside_virtual and not self._in_dgx:
            self._on_enter_dgx()

        if inside_virtual and self._in_dgx:
            self._forward_event(wParam, hs)
            # Clamp Windows cursor to the edge of the real monitor
            # (prevent Windows from moving its own cursor into virtual space)
            real_edge_x = r["x"] - 1
            user32.SetCursorPos(real_edge_x, y)
            return 1   # Block: don't let Windows process this event normally

        if not inside_virtual and self._in_dgx:
            # Cursor moved back out through the exit edge → return to Windows
            self._on_leave_dgx()

        # Not intercepted — let Windows handle normally
        return user32.CallNextHookEx(self._hook_id, nCode, wParam, lParam)

    # ------------------------------------------------------------------
    # Enter / Leave DGX virtual area
    # ------------------------------------------------------------------

    def _on_enter_dgx(self):
        self._in_dgx = True
        log.debug("Cursor entered virtual DGX display")
        # Hide Windows cursor system-wide while in DGX
        user32.ShowCursor(False)

    def _on_leave_dgx(self):
        self._in_dgx = False
        log.debug("Cursor left virtual DGX display")
        user32.ShowCursor(True)

    # ------------------------------------------------------------------
    # Forward intercepted events to DGX
    # ------------------------------------------------------------------

    def _forward_event(self, wParam: int, hs: MSLLHOOKSTRUCT):
        x, y   = hs.pt.x, hs.pt.y
        r      = self.rect
        dx, dy = self.mapper.screen_to_dgx(x, y, r["x"], r["y"])

        if wParam == WM_MOUSEMOVE:
            self.connection.send_mouse_move(dx, dy)

        elif wParam == WM_LBUTTONDOWN:
            self.connection.send_mouse_press("left", dx, dy)

        elif wParam == WM_LBUTTONUP:
            self.connection.send_mouse_release("left", dx, dy)

        elif wParam == WM_RBUTTONDOWN:
            self.connection.send_mouse_press("right", dx, dy)

        elif wParam == WM_RBUTTONUP:
            self.connection.send_mouse_release("right", dx, dy)

        elif wParam == WM_MOUSEWHEEL:
            # mouseData high word = wheel delta (120 per notch)
            delta = ctypes.c_short(hs.mouseData >> 16).value
            dy_scroll = 1 if delta > 0 else -1
            self.connection.send_mouse_scroll(dy_scroll * 3, dx, dy)
```

---

## 5. Mode Switching

When the user switches between Window Mode and Virtual Display Mode (via the Manager window), the application saves the new mode to config and presents a restart dialog:

```python
# pc-application/src/manager_window.py — save_and_close()

def _save_and_close(self):
    old_mode = self.config.display_mode
    # ... (save all fields) ...
    new_mode = "window" if self._f_mode.currentIndex() == 0 else "virtual_display"
    self.config.display_mode = new_mode
    self.config.save()

    if old_mode != new_mode:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.information(
            self,
            "Restart Required",
            f"Display mode changed to {new_mode.replace('_', ' ').title()}.\n\n"
            "Please restart DGX Desktop Remote for the change to take effect."
        )
    self.accept()
```

**Virtual Display Mode initialization** in `main.py`:

```python
def main():
    # ... (load config, run wizard if needed) ...

    window = MainWindow(config)
    tray   = AppSystemTray(app, window)
    tray.show()

    if config.display_mode == "virtual_display":
        # Set window as borderless, position it outside real monitor space
        window.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        virt_rect = _compute_virtual_monitor_rect(config)
        window.setGeometry(virt_rect["x"], virt_rect["y"],
                            virt_rect["w"], virt_rect["h"])
        window.show()

        # Start virtual display input manager after connect
        # (deferred until DGX resolution is known)
        config._virt_rect = virt_rect

    else:
        window.show()

    sys.exit(app.exec())


def _compute_virtual_monitor_rect(config) -> dict:
    """
    Determine where to position the virtual DGX monitor window
    in Windows multi-monitor screen space.
    Place it to the right of all real monitors by default.
    The DGX resolution determines its size (from hello handshake; use
    config defaults until connected).
    """
    import ctypes
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    # Total virtual desktop width
    screen_w = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    screen_y = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN (top-left Y of virtual desktop)

    return {
        "x": screen_w,          # Immediately right of all real monitors
        "y": screen_y,
        "w": 1920,               # Updated after hello handshake
        "h": 1080
    }
```

---

## 6. Virtual Display Mode — Connection Callback

After connecting and receiving DGX resolution, update the virtual monitor size and start the input manager:

```python
# In MainWindow._connect() (Virtual Display Mode branch):

def _connect(self):
    # ... (existing connection code) ...
    info = self.connection.connect()
    disp = info.get("display", {})
    dgx_w = disp.get("width", 1920)
    dgx_h = disp.get("height", 1080)

    self.mapper = CoordinateMapper(dgx_w=dgx_w, dgx_h=dgx_h)
    self.canvas.mapper = self.mapper

    if self.config.display_mode == "virtual_display":
        # Resize virtual window to actual DGX resolution
        virt_rect = self.config._virt_rect
        virt_rect["w"] = dgx_w
        virt_rect["h"] = dgx_h
        self.setGeometry(virt_rect["x"], virt_rect["y"], dgx_w, dgx_h)

        # Start input interception
        self._virt_input = VirtualDisplayInputManager(
            connection=self.connection,
            mapper=self.mapper,
            virtual_monitor_rect=virt_rect
        )
        self._virt_input.start()
    # ... (rest of connect success logic) ...
```

---

## 7. Keyboard Forwarding in Virtual Display Mode

In Virtual Display Mode, keyboard focus follows the cursor. When `_in_dgx` is True in the `VirtualDisplayInputManager`, keyboard input should also be captured. A Windows low-level keyboard hook handles this:

```python
# Additional hook in VirtualDisplayInputManager

WH_KEYBOARD_LL  = 13
WM_KEYDOWN      = 0x0100
WM_KEYUP        = 0x0101
WM_SYSKEYDOWN   = 0x0104

KBDLLHOOKSTRUCT_FIELDS = [
    ("vkCode",      ctypes.wintypes.DWORD),
    ("scanCode",    ctypes.wintypes.DWORD),
    ("flags",       ctypes.wintypes.DWORD),
    ("time",        ctypes.wintypes.DWORD),
    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = KBDLLHOOKSTRUCT_FIELDS


KBDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)

# VK → xdotool key name mapping (subset; full table in input_filter.py)
_VK_MAP = {
    0x08: "BackSpace", 0x09: "Tab",    0x0D: "Return",  0x1B: "Escape",
    0x20: "space",     0x25: "Left",   0x26: "Up",      0x27: "Right",
    0x28: "Down",      0x2E: "Delete", 0x70: "F1",      0x71: "F2",
    0x72: "F3",        0x73: "F4",     0x74: "F5",      0x75: "F6",
    0x76: "F7",        0x77: "F8",     0x78: "F9",      0x79: "F10",
    0x7A: "F11",       0x7B: "F12",
}

# Virtual keys to block from reaching Windows when cursor is in DGX
_BLOCKED_VK = {0x5B, 0x5C}   # Win keys blocked while in DGX (prevent Start menu)


# Installation (add to _run() alongside mouse hook):
def _install_keyboard_hook(self):
    kproc = KBDPROC(self._low_level_kb_proc)
    self._kb_proc = kproc
    self._kb_hook = user32.SetWindowsHookExA(
        WH_KEYBOARD_LL, kproc, kernel32.GetModuleHandleW(None), 0
    )

def _low_level_kb_proc(self, nCode, wParam, lParam) -> int:
    if nCode < HC_ACTION or not self._in_dgx:
        return user32.CallNextHookEx(self._kb_hook, nCode, wParam, lParam)

    ks = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
    vk = ks.vkCode

    # Block dangerous Windows shortcuts while in DGX
    if vk in _BLOCKED_VK:
        return 1   # Block Win key from opening Start Menu

    key_name = _VK_MAP.get(vk)
    if not key_name:
        # Printable ASCII
        ch = ctypes.c_char()
        if user32.ToUnicode(vk, ks.scanCode, None, ctypes.byref(ch), 2, 0) == 1:
            key_name = ch.value.decode("utf-8", errors="ignore").lower()

    if key_name:
        # Determine active modifiers from Windows key state
        mods = []
        if user32.GetAsyncKeyState(0x11) & 0x8000: mods.append("ctrl")
        if user32.GetAsyncKeyState(0x10) & 0x8000: mods.append("shift")
        if user32.GetAsyncKeyState(0x12) & 0x8000: mods.append("alt")

        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            self.connection.send_key_press(key_name, mods)
        else:
            self.connection.send_key_release(key_name, mods)

    return 1   # Block: prevent Windows from also processing this key
```

---

## 8. DGX Side: Resolution Push to All Clients

In `server.py` / `rpc_handler.py`, the resolution monitor needs to notify all active RPC connections when resolution changes:

```python
# dgx-service/src/dgx_service.py — wiring it together

from resolution_monitor import ResolutionMonitor

# A shared set of active RPC connections (thread-safe)
import threading
_rpc_connections: set = set()
_rpc_lock = threading.Lock()

def register_rpc_conn(conn):
    with _rpc_lock:
        _rpc_connections.add(conn)

def unregister_rpc_conn(conn):
    with _rpc_lock:
        _rpc_connections.discard(conn)

def on_resolution_changed(old: dict, new: dict):
    """Push resolution change to all connected PC clients."""
    with _rpc_lock:
        dead = set()
        for conn in _rpc_connections:
            try:
                from shared.protocol import send_json
                send_json(conn, {
                    "type":       "resolution_changed",
                    "old":        old,
                    "new":        new,
                    "refresh_hz": new.get("refresh_hz", 60)
                })
            except Exception:
                dead.add(conn)
        _rpc_connections -= dead

# In rpc_handler.handle_rpc_connection():
# At start:  register_rpc_conn(conn)
# At end:    unregister_rpc_conn(conn)
```

---

## 9. Mode Comparison

| Feature | Window Mode | Virtual Display Mode |
|---------|-----------|--------------------|
| Window style | Normal QMainWindow, resizable | Borderless, always-on-top |
| Input activation | Cursor enters window | Cursor crosses monitor edge |
| Cursor hide | Qt `BlankCursor` inside canvas | Windows `ShowCursor(False)` globally |
| Keyboard capture | PyQt6 `keyPressEvent` (window focused) | WH_KEYBOARD_LL hook (system-wide when in DGX) |
| Win key behavior | Blocked via Qt | Blocked via keyboard hook |
| Drag-and-drop | ✓ Drop onto canvas | ✓ Drop onto borderless window |
| Resize | User can resize window | Fixed to DGX native resolution |
| Setup complexity | None | Positioning virtual monitor rect correctly |

---

## 10. Virtual Monitor Positioning

The virtual monitor can be placed on any side of the real monitors. The default is **right of all real monitors** (most natural for two-monitor setups).

```python
# pc-application/src/input/virtual_display_setup.py

import ctypes
import ctypes.wintypes


def get_all_monitor_rects() -> list:
    """Returns list of {'x', 'y', 'w', 'h'} for all real Windows monitors."""
    monitors = []

    def callback(hmon, hdc, lprect, lparam):
        r = ctypes.cast(lprect, ctypes.POINTER(ctypes.wintypes.RECT)).contents
        monitors.append({"x": r.left, "y": r.top,
                          "w": r.right - r.left, "h": r.bottom - r.top})
        return True

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HMONITOR, ctypes.wintypes.HDC,
        ctypes.POINTER(ctypes.wintypes.RECT), ctypes.wintypes.LPARAM
    )
    ctypes.windll.user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
    return monitors


def compute_virtual_rect(side: str, dgx_w: int, dgx_h: int) -> dict:
    """
    Compute where to place the virtual DGX monitor window.
    side: "right" | "left" | "top" | "bottom"
    """
    monitors  = get_all_monitor_rects()
    right_x   = max(m["x"] + m["w"] for m in monitors)
    left_x    = min(m["x"]          for m in monitors)
    top_y     = min(m["y"]          for m in monitors)
    bottom_y  = max(m["y"] + m["h"] for m in monitors)
    primary_h = next((m["h"] for m in monitors if m["x"] == 0 and m["y"] == 0), 1080)
    primary_w = next((m["w"] for m in monitors if m["x"] == 0 and m["y"] == 0), 1920)

    positions = {
        "right":  {"x": right_x,          "y": top_y,          "w": dgx_w, "h": dgx_h},
        "left":   {"x": left_x - dgx_w,   "y": top_y,          "w": dgx_w, "h": dgx_h},
        "top":    {"x": 0,                 "y": top_y - dgx_h,  "w": dgx_w, "h": dgx_h},
        "bottom": {"x": 0,                 "y": bottom_y,       "w": dgx_w, "h": dgx_h},
    }
    return positions.get(side, positions["right"])
```

**In the Manager window**, the user selects which side:

```python
# Manager window additions — Virtual Monitor Position
self._f_virt_side = QComboBox()
self._f_virt_side.addItems(["Right of all monitors", "Left of all monitors",
                              "Above all monitors",   "Below all monitors"])
```

---

## 11. Implementation Checklist

```
Window Mode
[ ] Run PC app, open Manager, confirm mode = "Window Mode"
[ ] Click Connect → window shows DGX desktop
[ ] Move cursor into window → Windows cursor disappears
[ ] Move cursor around → DGX cursor tracks exactly
[ ] Move cursor out of window → Windows cursor reappears
[ ] Press keyboard keys inside window → appears on DGX
[ ] Resize window → DGX desktop scales to fill (letterboxed)
[ ] F11 → fullscreen, F11 again → back to windowed
[ ] Pin button → window stays on top of other apps
[ ] DGX changes resolution → mapper updates, scale recalculates

Virtual Display Mode
[ ] Open Manager → switch to Virtual Display Mode → restart app
[ ] PC app opens as borderless window positioned right of all monitors
[ ] Connect → window resizes to DGX native resolution
[ ] Move PC cursor to far right edge → cursor enters DGX (Windows cursor hides)
[ ] Mouse movements continue on DGX → DGX cursor follows
[ ] Type keyboard → goes to DGX (NOT to Windows)
[ ] Win key does not open Start Menu while in DGX
[ ] Move DGX cursor to left edge → cursor exits back to Windows

Coordinate Mapping
[ ] DGX resolution = 1920x1080, Window = 960x540
[ ] Click top-left corner → DGX receives (0, 0)
[ ] Click bottom-right corner → DGX receives (1919, 1079)
[ ] Click center → DGX receives (960, 540)
[ ] DGX changes to 3840x2160 → mapper updates → corners still correct

Resolution Monitor
[ ] DGX service running, PC connected
[ ] Change DGX resolution via xrandr or display settings
[ ] PC app receives "resolution_changed" RPC push
[ ] mapper.dgx_w, mapper.dgx_h update automatically
[ ] Video canvas rescales without reconnecting
```
