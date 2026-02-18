# DGX Desktop Remote — System Design Specification

**Version**: 2.0  
**Status**: Implementation-Ready  
**Last Updated**: February 18, 2026  
**Repo**: github.com/MeeshMakes/DGX-Desktop-Remote  

---

## 1. System Overview

DGX Desktop Remote consists of **two separate applications**:

| App | Runs on | Role |
|-----|---------|------|
| **PC Application** | Windows 11 PC | GUI client: renders the DGX desktop, captures user input |
| **DGX Service** | NVIDIA DGX (Ubuntu) | Headless server: captures screen, injects input, manages files |

They communicate over a direct 10 GbE Ethernet cable. See `DGX_CONNECTION_ARCHITECTURE.md` for the full network layer specification.

---

## 2. Repository Structure

```
MeeshMakes/DGX-Desktop-Remote/
├── pc-application/               # Windows client application
│   ├── src/
│   │   ├── main.py               # Entry point — QApplication startup
│   │   ├── main_window.py        # MainWindow: display area + menu bar
│   │   ├── manager_window.py     # PC Manager window (setup/settings)
│   │   ├── setup_wizard.py       # First-run IP configuration wizard
│   │   ├── system_tray.py        # System tray icon and menu
│   │   ├── network/
│   │   │   ├── connection.py     # DGXConnection (see CONN_ARCH.md)
│   │   │   └── video_receiver.py # VideoReceiver thread + FPS tracking
│   │   ├── display/
│   │   │   ├── video_canvas.py   # QLabel subclass rendering JPEG frames
│   │   │   ├── coordinate_mapper.py  # Scale coords between resolutions
│   │   │   └── cursor_tunnel.py  # Cursor hide/show on enter/leave
│   │   ├── input/
│   │   │   ├── input_filter.py   # Block/translate Windows system keys
│   │   │   └── virtual_display.py # Input interception for Virtual Mode
│   │   ├── transfer/
│   │   │   ├── file_analyzer.py  # Detect file type by magic bytes
│   │   │   ├── file_converter.py # CRLF/LF, permissions, scripts
│   │   │   ├── transfer_worker.py# QThread upload/download worker
│   │   │   └── transfer_panel.py # Sidebar transfer UI widget
│   │   └── config.py             # Load/save ~/.dgx-desktop-remote/config.json
│   ├── assets/
│   │   ├── icon.ico              # PC app icon (Windows)
│   │   ├── icon.png              # 256x256 PNG for tray / taskbar
│   │   └── icon_connected.png    # Green indicator version
│   ├── requirements.txt          # PyQt6, pywin32
│   └── build_win.py              # PyInstaller build script for Windows EXE
│
├── dgx-service/                  # Linux headless service
│   ├── src/
│   │   ├── dgx_service.py        # Entry point — starts all port listeners
│   │   ├── server.py             # PortListener / DGXService classes
│   │   ├── rpc_handler.py        # Handle all control/file RPC requests
│   │   ├── screen_capture.py     # mss screen capture + JPEG frame pump
│   │   ├── input_handler.py      # xdotool input injection
│   │   ├── resolution_monitor.py # Watch xrandr, push change events to PC
│   │   └── manager_gui.py        # DGX Manager GUI (PyQt6 service control panel)
│   ├── install/
│   │   ├── install.sh            # Install script: deps, service, autostart
│   │   ├── dgx-desktop-remote.service  # systemd unit file
│   │   └── dgx-desktop-remote.desktop  # XDG autostart file
│   └── requirements.txt          # mss, Pillow, PyQt6
│
├── shared/
│   └── protocol.py               # send_json, recv_line, recv_exact, CHUNK_SIZE
│
├── docs/
│   ├── DGX_CONNECTION_ARCHITECTURE.md
│   ├── SYSTEM_DESIGN_SPECIFICATION.md
│   ├── FILE_TRANSFER_ARCHITECTURE.md
│   └── ADVANCED_DISPLAY_ARCHITECTURE.md
│
├── setup_wizard_installer.py     # Standalone first-run installer (generates config)
├── create_shortcuts.py           # Creates desktop .lnk (PC) / .desktop (DGX)
├── README.md
└── .gitignore                    # Ignores config.json, venv/, dist/, *.egg-info/
```

**Important**: `config.json` is never committed to Git. IPs and paths are set at install time by the setup wizard and written to the user's local config file only.

---

## 3. PC Application

### 3.1 Entry Point

```python
# pc-application/src/main.py

import sys
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from config import Config
from setup_wizard import SetupWizard
from main_window import MainWindow
from system_tray import AppSystemTray


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("DGX Desktop Remote")
    app.setWindowIcon(QIcon(str(Path(__file__).parent.parent / "assets" / "icon.ico")))
    app.setQuitOnLastWindowClosed(False)   # Keep alive in system tray

    # Load or create config
    config = Config.load()

    # First-run: show setup wizard if no config exists
    if not config.is_configured():
        wizard = SetupWizard(config)
        if not wizard.exec():
            sys.exit(0)
        config.save()

    # Main display window
    window = MainWindow(config)

    # System tray icon
    tray = AppSystemTray(app, window)
    tray.show()

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```

### 3.2 Configuration File

```json
// ~/.dgx-desktop-remote/config.json  (never in Git — listed in .gitignore)
{
    "pc_ip":          "10.0.0.2",
    "dgx_ip":         "10.0.0.1",
    "rpc_port":       22010,
    "video_port":     22011,
    "input_port":     22012,
    "pc_listen_port": 12010,
    "display_mode":   "window",
    "window": {
        "width":       1920,
        "height":      1080,
        "pinned":      false,
        "start_minimized": false
    },
    "video": {
        "target_fps":  60,
        "jpeg_quality": 85,
        "scaling_mode": "fit"
    },
    "auto_connect":   false,
    "show_fps_overlay": false
}
```

```python
# pc-application/src/config.py

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


CONFIG_DIR  = Path.home() / ".dgx-desktop-remote"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Config:
    pc_ip:          str  = "10.0.0.2"
    dgx_ip:         str  = "10.0.0.1"
    rpc_port:       int  = 22010
    video_port:     int  = 22011
    input_port:     int  = 22012
    pc_listen_port: int  = 12010
    display_mode:   str  = "window"   # "window" | "virtual_display"
    target_fps:     int  = 60
    jpeg_quality:   int  = 85
    pinned:         bool = False
    auto_connect:   bool = False
    show_fps:       bool = False

    def is_configured(self) -> bool:
        return CONFIG_FILE.exists()

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with CONFIG_FILE.open("w") as f:
            json.dump(asdict(self), f, indent=4)

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_FILE.exists():
            return cls()
        with CONFIG_FILE.open() as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
```

### 3.3 Main Window

```python
# pc-application/src/main_window.py

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QToolBar, QPushButton, QLabel, QStatusBar, QSizePolicy
)
from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QIcon, QKeyEvent
from config import Config
from network.connection import DGXConnection
from display.video_canvas import VideoCanvas
from display.coordinate_mapper import CoordinateMapper
from transfer.transfer_panel import TransferPanel


class MainWindow(QMainWindow):

    def __init__(self, config: Config):
        super().__init__()
        self.config     = config
        self.connection = DGXConnection(
            on_frame=self._on_frame,
            on_disconnect=self._on_disconnect
        )
        self.dgx_info   = {}          # Populated on connect: resolution, hostname
        self.mapper     = None        # CoordinateMapper — created after connect

        self._build_ui()
        self._apply_window_flags()

        if config.auto_connect:
            QTimer.singleShot(500, self._connect)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.setWindowTitle("DGX Desktop Remote")
        self.resize(1280, 720)   # Default window size; not necessarily DGX native res

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Video canvas — the DGX display
        self.canvas = VideoCanvas(self)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.canvas)

        # Collapsible file transfer sidebar (slides in from right)
        self.transfer_panel = TransferPanel(self.connection, self)
        self.transfer_panel.setVisible(False)

        # Menu bar / toolbar
        self._build_toolbar()

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._lbl_status = QLabel("● Disconnected")
        self._lbl_status.setStyleSheet("color: #888;")
        self._lbl_fps    = QLabel("")
        self.status_bar.addWidget(self._lbl_status)
        self.status_bar.addPermanentWidget(self._lbl_fps)

        # FPS update timer
        fps_timer = QTimer(self)
        fps_timer.timeout.connect(self._update_fps)
        fps_timer.start(1000)

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

        # Hamburger menu button
        self._btn_menu = QPushButton("☰")
        self._btn_menu.setToolTip("Settings / Manager")
        self._btn_menu.setFixedWidth(36)
        self._btn_menu.clicked.connect(self._open_manager)
        tb.addWidget(self._btn_menu)

        tb.addSeparator()

        # Connect / Disconnect toggle
        self._btn_connect = QPushButton("Connect")
        self._btn_connect.setFixedWidth(90)
        self._btn_connect.clicked.connect(self._toggle_connection)
        tb.addWidget(self._btn_connect)

        # Pin window (Always on Top toggle)
        self._btn_pin = QPushButton("📌 Pin")
        self._btn_pin.setCheckable(True)
        self._btn_pin.setChecked(self.config.pinned)
        self._btn_pin.setFixedWidth(60)
        self._btn_pin.clicked.connect(self._toggle_pin)
        tb.addWidget(self._btn_pin)

        # File transfer toggle
        self._btn_files = QPushButton("📁 Files")
        self._btn_files.setCheckable(True)
        self._btn_files.setFixedWidth(70)
        self._btn_files.clicked.connect(self._toggle_transfer_panel)
        tb.addWidget(self._btn_files)

        # Stretch spacer
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        # DGX hostname label in toolbar (populated on connect)
        self._lbl_host = QLabel("")
        self._lbl_host.setStyleSheet("color: #aaa; font-size: 11px; padding-right: 8px;")
        tb.addWidget(self._lbl_host)

    def _apply_window_flags(self):
        flags = Qt.WindowType.Window
        if self.config.pinned:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _toggle_connection(self):
        if self.connection.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        self._btn_connect.setText("Connecting…")
        self._btn_connect.setEnabled(False)
        try:
            info = self.connection.connect()
            self.dgx_info = info
            disp = info.get("display", {})
            self.mapper = CoordinateMapper(
                dgx_w=disp.get("width", 1920),
                dgx_h=disp.get("height", 1080)
            )
            self.canvas.mapper = self.mapper
            self._btn_connect.setText("Disconnect")
            self._btn_connect.setEnabled(True)
            self._lbl_status.setText("● Connected")
            self._lbl_status.setStyleSheet("color: #4caf50; font-weight: bold;")
            host = info.get("hostname", "DGX")
            self._lbl_host.setText(f"Connected to {host}  |  "
                                    f"{disp.get('width')}×{disp.get('height')} "
                                    f"@ {disp.get('refresh_hz')}Hz")
        except Exception as e:
            self._btn_connect.setText("Connect")
            self._btn_connect.setEnabled(True)
            self._lbl_status.setText(f"● Connection failed: {e}")
            self._lbl_status.setStyleSheet("color: #f44336;")

    def _disconnect(self):
        self.connection.disconnect()

    def _on_disconnect(self):
        # Called from background thread
        from PyQt6.QtCore import QMetaObject, Qt
        QMetaObject.invokeMethod(self, "_on_disconnect_ui", Qt.ConnectionType.QueuedConnection)

    def _on_disconnect_ui(self):
        self._btn_connect.setText("Connect")
        self._lbl_status.setText("● Disconnected")
        self._lbl_status.setStyleSheet("color: #888;")
        self._lbl_host.setText("")
        self.canvas.clear()

    # ------------------------------------------------------------------
    # Video
    # ------------------------------------------------------------------

    def _on_frame(self, jpeg_data: bytes):
        """Called from video receiver thread. Update canvas."""
        self.canvas.update_frame(jpeg_data)

    def _update_fps(self):
        if self.config.show_fps and hasattr(self, 'canvas'):
            fps = self.canvas.fps_actual
            if fps > 0:
                self._lbl_fps.setText(f"  {fps:.1f} fps")

    # ------------------------------------------------------------------
    # UI Actions
    # ------------------------------------------------------------------

    def _toggle_pin(self, pinned: bool):
        self.config.pinned = pinned
        self.config.save()
        # Toggle always-on-top by hiding/showing forces Wayland/X11 to re-evaluate
        self._apply_window_flags()
        self.show()

    def _toggle_transfer_panel(self, visible: bool):
        self.transfer_panel.setVisible(visible)

    def _open_manager(self):
        from manager_window import ManagerWindow
        mgr = ManagerWindow(self.config, self)
        mgr.exec()

    # ------------------------------------------------------------------
    # Keyboard input (forwarded to DGX when connected and canvas focused)
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent):
        if not self.connection.connected:
            return super().keyPressEvent(event)
        key  = _qt_key_to_name(event.key())
        mods = _qt_mods_to_list(event.modifiers())
        if key:
            self.connection.send_key_press(key, mods)

    def keyReleaseEvent(self, event: QKeyEvent):
        if not self.connection.connected:
            return super().keyReleaseEvent(event)
        key  = _qt_key_to_name(event.key())
        mods = _qt_mods_to_list(event.modifiers())
        if key:
            self.connection.send_key_release(key, mods)


def _qt_key_to_name(key: int) -> str:
    """Convert Qt key integer to xdotool-compatible key name."""
    from PyQt6.QtCore import Qt
    _MAP = {
        Qt.Key.Key_Return:   "Return",
        Qt.Key.Key_Enter:    "KP_Enter",
        Qt.Key.Key_Escape:   "Escape",
        Qt.Key.Key_Tab:      "Tab",
        Qt.Key.Key_Backspace:"BackSpace",
        Qt.Key.Key_Delete:   "Delete",
        Qt.Key.Key_Up:       "Up",
        Qt.Key.Key_Down:     "Down",
        Qt.Key.Key_Left:     "Left",
        Qt.Key.Key_Right:    "Right",
        Qt.Key.Key_Home:     "Home",
        Qt.Key.Key_End:      "End",
        Qt.Key.Key_PageUp:   "Prior",
        Qt.Key.Key_PageDown: "Next",
        Qt.Key.Key_F1:       "F1",
        Qt.Key.Key_F2:       "F2",
        Qt.Key.Key_F3:       "F3",
        Qt.Key.Key_F4:       "F4",
        Qt.Key.Key_F5:       "F5",
        Qt.Key.Key_F6:       "F6",
        Qt.Key.Key_F7:       "F7",
        Qt.Key.Key_F8:       "F8",
        Qt.Key.Key_F9:       "F9",
        Qt.Key.Key_F10:      "F10",
        Qt.Key.Key_F11:      "F11",
        Qt.Key.Key_F12:      "F12",
        Qt.Key.Key_Space:    "space",
    }
    if key in _MAP:
        return _MAP[key]
    # Printable ASCII characters
    if 32 <= key <= 126:
        return chr(key).lower()
    return ""


def _qt_mods_to_list(mods) -> list:
    from PyQt6.QtCore import Qt
    out = []
    if mods & Qt.KeyboardModifier.ControlModifier: out.append("ctrl")
    if mods & Qt.KeyboardModifier.ShiftModifier:   out.append("shift")
    if mods & Qt.KeyboardModifier.AltModifier:     out.append("alt")
    if mods & Qt.KeyboardModifier.MetaModifier:    out.append("super")
    return out
```

### 3.4 Video Canvas

```python
# pc-application/src/display/video_canvas.py

import time
from collections import deque
from PyQt6.QtWidgets import QLabel
from PyQt6.QtCore import Qt, QPoint, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage, QCursor


class VideoCanvas(QLabel):
    """
    Renders incoming JPEG frames.
    Intercepts mouse events and forwards them to DGX via the connection.
    Handles cursor hide/show for cursor tunnel mode.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: #111111;")
        self.setMinimumSize(640, 360)
        self.setMouseTracking(True)           # Track mouse even without button pressed
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.connection  = None               # Set by main window after connect
        self.mapper      = None               # CoordinateMapper
        self._in_tunnel  = False              # True when cursor is inside canvas
        self._fps_times  = deque(maxlen=60)   # Ring buffer of frame timestamps
        self.fps_actual  = 0.0

    def update_frame(self, jpeg_data: bytes):
        """Thread-safe: called from VideoReceiver thread."""
        # Qt is OK loading images on background thread, but must invoke on main thread
        img = QImage.fromData(jpeg_data, "JPEG")
        if img.isNull():
            return
        pixmap = QPixmap.fromImage(img).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        # Must update UI on main thread
        from PyQt6.QtCore import QMetaObject, Q_ARG, Qt
        QMetaObject.invokeMethod(self, "setPixmap", Qt.ConnectionType.QueuedConnection,
                                  Q_ARG(QPixmap, pixmap))
        # Track FPS
        now = time.monotonic()
        self._fps_times.append(now)
        if len(self._fps_times) >= 2:
            elapsed = self._fps_times[-1] - self._fps_times[0]
            if elapsed > 0:
                self.fps_actual = (len(self._fps_times) - 1) / elapsed

    def clear(self):
        self.setPixmap(QPixmap())
        self.fps_actual = 0.0

    # ------------------------------------------------------------------
    # Mouse events → DGX input
    # ------------------------------------------------------------------

    def enterEvent(self, event):
        """Cursor entered the DGX display window."""
        self._in_tunnel = True
        self.setCursor(Qt.CursorShape.BlankCursor)   # Hide Windows cursor
        self.setFocus()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Cursor left the DGX display window."""
        self._in_tunnel = False
        self.unsetCursor()                           # Restore Windows cursor
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        if self.connection and self.connection.connected and self.mapper:
            dx, dy = self._canvas_to_dgx(event.position())
            self.connection.send_mouse_move(dx, dy)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self.connection and self.connection.connected and self.mapper:
            btn = _qt_btn(event.button())
            dx, dy = self._canvas_to_dgx(event.position())
            self.connection.send_mouse_press(btn, dx, dy)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self.connection and self.connection.connected and self.mapper:
            btn = _qt_btn(event.button())
            dx, dy = self._canvas_to_dgx(event.position())
            self.connection.send_mouse_release(btn, dx, dy)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if self.connection and self.connection.connected:
            dy = 1 if event.angleDelta().y() > 0 else -1
            dx, ddy = self._canvas_to_dgx(event.position())
            self.connection.send_mouse_scroll(dy * 3, dx, ddy)
        super().wheelEvent(event)

    def _canvas_to_dgx(self, pos) -> tuple[int, int]:
        """Map canvas pixel position to DGX display coordinates."""
        # The pixmap may be letter-boxed inside the label; get actual rendered rect
        pm = self.pixmap()
        if pm is None or pm.isNull() or not self.mapper:
            return int(pos.x()), int(pos.y())
        w, h = pm.width(), pm.height()
        # Offset of rendered image inside the label
        off_x = (self.width()  - w) // 2
        off_y = (self.height() - h) // 2
        rel_x = (pos.x() - off_x) / w
        rel_y = (pos.y() - off_y) / h
        rel_x = max(0.0, min(1.0, rel_x))
        rel_y = max(0.0, min(1.0, rel_y))
        return self.mapper.relative_to_dgx(rel_x, rel_y)


def _qt_btn(btn) -> str:
    from PyQt6.QtCore import Qt
    return {
        Qt.MouseButton.LeftButton:  "left",
        Qt.MouseButton.RightButton: "right",
        Qt.MouseButton.MiddleButton:"middle"
    }.get(btn, "left")
```

---

## 4. PC Manager Window

```python
# pc-application/src/manager_window.py

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QSpinBox, QComboBox, QPushButton, QGroupBox, QFormLayout,
    QCheckBox, QDialogButtonBox
)
from PyQt6.QtCore import Qt
from config import Config


class ManagerWindow(QDialog):
    """
    Settings / Manager dialog. Opened via the hamburger menu button.
    Allows reconfiguring all settings without re-running setup wizard.
    """

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("DGX Desktop Remote — Manager")
        self.setMinimumWidth(460)
        self._build_ui()
        self._populate()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Network Settings ─────────────────────────────────────────
        net = QGroupBox("Network")
        nf  = QFormLayout(net)
        self._f_pc_ip  = QLineEdit(); nf.addRow("PC IP Address:", self._f_pc_ip)
        self._f_dgx_ip = QLineEdit(); nf.addRow("DGX IP Address:", self._f_dgx_ip)
        layout.addWidget(net)

        # ── Display Settings ─────────────────────────────────────────
        disp = QGroupBox("Display")
        df   = QFormLayout(disp)
        self._f_mode = QComboBox()
        self._f_mode.addItems(["Window Mode", "Virtual Display Mode"])
        df.addRow("Display Mode:", self._f_mode)
        self._f_fps = QSpinBox()
        self._f_fps.setRange(10, 60); self._f_fps.setSuffix(" fps")
        df.addRow("Target FPS:", self._f_fps)
        self._f_quality = QSpinBox()
        self._f_quality.setRange(50, 100); self._f_quality.setSuffix("%")
        df.addRow("JPEG Quality:", self._f_quality)
        layout.addWidget(disp)

        # ── Behavior Settings ─────────────────────────────────────────
        beh = QGroupBox("Behavior")
        bf  = QFormLayout(beh)
        self._f_autoconnect = QCheckBox("Connect automatically when app starts")
        self._f_show_fps    = QCheckBox("Show FPS overlay in status bar")
        bf.addRow(self._f_autoconnect)
        bf.addRow(self._f_show_fps)
        layout.addWidget(beh)

        # ── Tools ─────────────────────────────────────────────────────
        tools = QGroupBox("Tools")
        tl    = QHBoxLayout(tools)
        btn_shortcut = QPushButton("Create Desktop Shortcut")
        btn_shortcut.clicked.connect(self._create_shortcut)
        tl.addWidget(btn_shortcut)
        btn_firewall = QPushButton("Open Firewall Instructions")
        btn_firewall.clicked.connect(self._show_firewall)
        tl.addWidget(btn_firewall)
        layout.addWidget(tools)

        # ── Buttons ───────────────────────────────────────────────────
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                               QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save_and_close)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _populate(self):
        self._f_pc_ip.setText(self.config.pc_ip)
        self._f_dgx_ip.setText(self.config.dgx_ip)
        self._f_mode.setCurrentIndex(0 if self.config.display_mode == "window" else 1)
        self._f_fps.setValue(self.config.target_fps)
        self._f_quality.setValue(self.config.jpeg_quality)
        self._f_autoconnect.setChecked(self.config.auto_connect)
        self._f_show_fps.setChecked(self.config.show_fps)

    def _save_and_close(self):
        self.config.pc_ip        = self._f_pc_ip.text().strip()
        self.config.dgx_ip       = self._f_dgx_ip.text().strip()
        self.config.display_mode = "window" if self._f_mode.currentIndex() == 0 else "virtual_display"
        self.config.target_fps   = self._f_fps.value()
        self.config.jpeg_quality = self._f_quality.value()
        self.config.auto_connect = self._f_autoconnect.isChecked()
        self.config.show_fps     = self._f_show_fps.isChecked()
        self.config.save()
        self.accept()

    def _create_shortcut(self):
        from PyQt6.QtWidgets import QMessageBox
        try:
            from create_shortcuts import create_windows_shortcut
            create_windows_shortcut()
            QMessageBox.information(self, "Shortcut", "Desktop shortcut created.")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _show_firewall(self):
        from PyQt6.QtWidgets import QMessageBox
        msg = ("Run in PowerShell as Administrator:\n\n"
               "New-NetFirewallRule `\n"
               "  -DisplayName 'DGX-Desktop-Remote' `\n"
               "  -Direction Inbound -Protocol TCP `\n"
               "  -LocalPort 12010 -RemoteAddress 10.0.0.1 `\n"
               "  -Action Allow")
        QMessageBox.information(self, "Firewall Setup", msg)
```

---

## 5. Setup Wizard

```python
# pc-application/src/setup_wizard.py

from PyQt6.QtWidgets import (
    QWizard, QWizardPage, QLabel, QLineEdit,
    QVBoxLayout, QFormLayout, QCheckBox
)
from PyQt6.QtCore import Qt
from config import Config


class SetupWizard(QWizard):
    """
    Runs on first launch to configure IPs and basic settings.
    No default IPs shown — user must type them in. Nothing hardcoded.
    Written config goes to ~/.dgx-desktop-remote/config.json only.
    """

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("DGX Desktop Remote — First-Time Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(540, 380)
        self.addPage(WelcomePage())
        self.addPage(NetworkPage(config))
        self.addPage(OptionsPage(config))
        self.addPage(FinishPage())

    def accept(self):
        # OptionsPage commits itself during validatePage()
        super().accept()


class WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Welcome")
        l = QVBoxLayout(self)
        l.addWidget(QLabel(
            "Welcome to DGX Desktop Remote.\n\n"
            "This wizard will configure the connection between your PC and the DGX.\n\n"
            "You will need:\n"
            "  • The static IP address of your PC (10.x.x.x)\n"
            "  • The static IP address of your DGX\n"
            "  • A 10 GbE Ethernet cable already plugged in."
        ))


class NetworkPage(QWizardPage):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.setTitle("Network Configuration")
        self.setSubTitle("Enter the static IP addresses for both machines.")
        f = QFormLayout(self)
        self._pc  = QLineEdit(placeholder="e.g. 10.0.0.2")
        self._dgx = QLineEdit(placeholder="e.g. 10.0.0.1")
        f.addRow("PC IP Address:",  self._pc)
        f.addRow("DGX IP Address:", self._dgx)
        self.registerField("pc_ip*",  self._pc)   # * = required field
        self.registerField("dgx_ip*", self._dgx)

    def validatePage(self) -> bool:
        import re
        ip_re = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
        if not ip_re.match(self._pc.text()):
            self._pc.setStyleSheet("border: 1px solid red;")
            return False
        if not ip_re.match(self._dgx.text()):
            self._dgx.setStyleSheet("border: 1px solid red;")
            return False
        self.config.pc_ip  = self._pc.text().strip()
        self.config.dgx_ip = self._dgx.text().strip()
        return True


class OptionsPage(QWizardPage):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.setTitle("Options")
        l = QVBoxLayout(self)
        self._auto    = QCheckBox("Auto-connect when app starts")
        self._tray    = QCheckBox("Start minimized to system tray")
        self._shortcut= QCheckBox("Create a desktop shortcut")
        self._shortcut.setChecked(True)
        l.addWidget(self._auto)
        l.addWidget(self._tray)
        l.addWidget(self._shortcut)
        l.addStretch()

    def validatePage(self) -> bool:
        self.config.auto_connect = self._auto.isChecked()
        if self._shortcut.isChecked():
            try:
                from create_shortcuts import create_windows_shortcut
                create_windows_shortcut()
            except Exception:
                pass
        return True


class FinishPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Setup Complete")
        l = QVBoxLayout(self)
        l.addWidget(QLabel(
            "Configuration saved.\n\n"
            "Click Finish to launch DGX Desktop Remote.\n\n"
            "Make sure the DGX service is running before clicking Connect."
        ))
```

---

## 6. Desktop Shortcut Creation

```python
# create_shortcuts.py

import sys
from pathlib import Path


def create_windows_shortcut():
    """Create a .lnk shortcut on the Windows Desktop using win32com."""
    import win32com.client

    desktop = Path.home() / "Desktop"
    target  = Path(sys.executable)
    main    = Path(__file__).parent / "pc-application" / "src" / "main.py"

    shell    = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(desktop / "DGX Desktop Remote.lnk"))
    shortcut.TargetPath       = str(target)
    shortcut.Arguments        = f'"{main}"'
    shortcut.WorkingDirectory = str(main.parent)
    shortcut.IconLocation     = str(Path(__file__).parent / "pc-application" / "assets" / "icon.ico")
    shortcut.Description      = "Open DGX Desktop Remote"
    shortcut.save()


def create_linux_desktop_file():
    """Create a .desktop launcher file on the DGX Desktop."""
    import os
    home    = Path.home()
    desktop = home / "Desktop"
    apps    = home / ".local" / "share" / "applications"

    content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=DGX Desktop Remote Manager
Comment=DGX remote desktop service manager
Exec=python3 {Path(__file__).parent}/dgx-service/src/manager_gui.py
Icon={Path(__file__).parent}/pc-application/assets/icon.png
Terminal=false
Categories=Network;RemoteAccess;
"""
    for d in [desktop, apps]:
        d.mkdir(parents=True, exist_ok=True)
        f = d / "dgx-desktop-remote.desktop"
        f.write_text(content)
        os.chmod(f, 0o755)


if __name__ == "__main__":
    if sys.platform == "win32":
        create_windows_shortcut()
        print("Windows shortcut created.")
    else:
        create_linux_desktop_file()
        print("Linux .desktop file created.")
```

---

## 7. DGX Service Entry Point

```python
# dgx-service/src/dgx_service.py

import logging
import signal
import sys
from server import DGXService
from rpc_handler import handle_rpc_connection
from screen_capture import handle_video_connection
from input_handler import handle_input_connection
from resolution_monitor import ResolutionMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
log = logging.getLogger("dgx")


def main():
    service = DGXService(
        rpc_handler=handle_rpc_connection,
        video_handler=handle_video_connection,
        input_handler=handle_input_connection
    )
    service.start()

    # Resolution monitor pushes change events to all connected RPC clients
    res_monitor = ResolutionMonitor()
    res_monitor.start()

    log.info("DGX service running. Press Ctrl+C to stop.")

    def shutdown(sig, frame):
        log.info("Shutting down...")
        service.stop()
        res_monitor.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    signal.pause()


if __name__ == "__main__":
    main()
```

---

## 8. DGX Autostart

### 8.1 systemd Unit File

```ini
# /etc/systemd/system/dgx-desktop-remote.service

[Unit]
Description=DGX Desktop Remote Service
After=network.target graphical-session.target
Wants=graphical-session.target

[Service]
Type=simple
User=%i
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/%i/.Xauthority
WorkingDirectory=/home/%i/DGX-Desktop-Remote
ExecStart=/usr/bin/python3 /home/%i/DGX-Desktop-Remote/dgx-service/src/dgx_service.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
```

**Enable and start**:
```bash
# Replace 'username' with your DGX username
sudo cp dgx-service/install/dgx-desktop-remote.service /etc/systemd/system/
sudo sed -i 's/%i/username/g' /etc/systemd/system/dgx-desktop-remote.service
sudo systemctl daemon-reload
sudo systemctl enable dgx-desktop-remote.service
sudo systemctl start  dgx-desktop-remote.service
sudo systemctl status dgx-desktop-remote.service
```

### 8.2 XDG Autostart Fallback

```ini
# ~/.config/autostart/dgx-desktop-remote.desktop

[Desktop Entry]
Type=Application
Name=DGX Desktop Remote Service
Exec=python3 /home/USERNAME/DGX-Desktop-Remote/dgx-service/src/dgx_service.py
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Comment=Start DGX remote desktop service at login
```

---

## 9. Install Script

```bash
#!/usr/bin/env bash
# dgx-service/install/install.sh
# Run as regular user (not root), will sudo where needed

set -e
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
USER_NAME="$(whoami)"

echo "=== DGX Desktop Remote — DGX Install ==="
echo "Repo: $REPO_DIR"
echo "User: $USER_NAME"

# System packages
sudo apt-get update -qq
sudo apt-get install -y python3-pip xdotool scrot

# Python packages
pip3 install --user mss Pillow PyQt6

# Install systemd service
sudo cp "$REPO_DIR/dgx-service/install/dgx-desktop-remote.service" \
        /etc/systemd/system/
sudo sed -i "s/%i/$USER_NAME/g" \
        /etc/systemd/system/dgx-desktop-remote.service

# Configure static IP via netplan
NIC=$(ip link show | grep -E "^[0-9]+: e" | head -1 | awk -F': ' '{print $2}')
echo "Detected NIC: $NIC"
sudo tee /etc/netplan/01-dgx-desktop-remote.yaml > /dev/null <<EOF
network:
  version: 2
  ethernets:
    $NIC:
      addresses:
        - 10.0.0.1/24
      dhcp4: false
      optional: true
EOF
sudo netplan apply

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable --now dgx-desktop-remote.service

# Create desktop shortcut
python3 "$REPO_DIR/create_shortcuts.py"

echo ""
echo "=== Install Complete ==="
echo "  DGX IP: 10.0.0.1"
echo "  Service: sudo systemctl status dgx-desktop-remote"
echo "  Logs:    journalctl -u dgx-desktop-remote -f"
```

---

## 10. System Tray

```python
# pc-application/src/system_tray.py

from pathlib import Path
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction


class AppSystemTray(QSystemTrayIcon):

    def __init__(self, app, main_window):
        icon = QIcon(str(Path(__file__).parent.parent / "assets" / "icon.png"))
        super().__init__(icon, app)
        self.app         = app
        self.main_window = main_window
        self.setToolTip("DGX Desktop Remote")
        self._build_menu()
        self.activated.connect(self._on_activate)

    def _build_menu(self):
        menu = QMenu()

        show_action = QAction("Show Window", self)
        show_action.triggered.connect(self.main_window.show)
        menu.addAction(show_action)

        menu.addSeparator()

        connect_action = QAction("Connect", self)
        connect_action.triggered.connect(self.main_window._connect)
        menu.addAction(connect_action)

        menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.app.quit)
        menu.addAction(quit_action)

        self.setContextMenu(menu)

    def _on_activate(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.main_window.show()
            self.main_window.raise_()
            self.main_window.activateWindow()
```

---

## 11. DGX Manager GUI

```python
# dgx-service/src/manager_gui.py
# Run on DGX: python3 manager_gui.py
# Provides GUI to start/stop/restart the DGX service

import subprocess
import sys
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QGroupBox, QFormLayout
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QFont


class DGXManagerGUI(QWidget):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DGX Desktop Remote — Service Manager")
        self.setMinimumSize(500, 500)
        self._build_ui()
        self._status_timer = QTimer()
        self._status_timer.timeout.connect(self._refresh)
        self._status_timer.start(3000)
        self._refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Service status indicator
        status_box = QGroupBox("Service Status")
        sf = QFormLayout(status_box)
        self._lbl_status = QLabel("Checking…")
        self._lbl_pid    = QLabel("")
        sf.addRow("Status:", self._lbl_status)
        sf.addRow("PID:",    self._lbl_pid)
        layout.addWidget(status_box)

        # Controls
        ctrl_layout = QHBoxLayout()
        self._btn_start   = QPushButton("▶  Start")
        self._btn_stop    = QPushButton("■  Stop")
        self._btn_restart = QPushButton("↺  Restart")
        for btn in (self._btn_start, self._btn_stop, self._btn_restart):
            btn.setMinimumHeight(36)
            ctrl_layout.addWidget(btn)
        self._btn_start.clicked.connect(lambda: self._run_cmd(["systemctl", "--user", "start",   "dgx-desktop-remote"]))
        self._btn_stop.clicked.connect( lambda: self._run_cmd(["systemctl", "--user", "stop",    "dgx-desktop-remote"]))
        self._btn_restart.clicked.connect(lambda: self._run_cmd(["systemctl","--user", "restart","dgx-desktop-remote"]))
        layout.addLayout(ctrl_layout)

        # Log viewer
        layout.addWidget(QLabel("Service Log (last 50 lines):"))
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Monospace", 9))
        layout.addWidget(self._log)

    def _refresh(self):
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "dgx-desktop-remote"],
                capture_output=True, text=True, timeout=2
            )
            active = result.stdout.strip() == "active"
            self._lbl_status.setText("● Running" if active else "● Stopped")
            self._lbl_status.setStyleSheet(f"color: {'#4caf50' if active else '#f44336'}; font-weight: bold;")

            # Fetch PID
            pid_result = subprocess.run(
                ["systemctl", "--user", "show", "--property=MainPID", "dgx-desktop-remote"],
                capture_output=True, text=True, timeout=2
            )
            pid = pid_result.stdout.strip().replace("MainPID=", "")
            self._lbl_pid.setText(pid if pid != "0" else "—")

            # Fetch last 50 log lines
            log_result = subprocess.run(
                ["journalctl", "--user", "-u", "dgx-desktop-remote", "-n", "50", "--no-pager"],
                capture_output=True, text=True, timeout=2
            )
            self._log.setPlainText(log_result.stdout)
            self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())

        except Exception as e:
            self._lbl_status.setText(f"Error: {e}")

    def _run_cmd(self, cmd: list):
        try:
            subprocess.run(["sudo"] + cmd, timeout=5)
        except Exception:
            subprocess.run(cmd, timeout=5)
        QTimer.singleShot(1500, self._refresh)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("DGX Service Manager")
    win = DGXManagerGUI()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
```
