"""
pc-application/src/main_window.py
The primary application window. Contains the DGX display canvas,
toolbar, status bar, FPS/ping overlays, and wires up all subsystems.
"""

import sys
import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QToolBar, QStatusBar, QLabel, QPushButton,
    QSizePolicy, QSplitter, QApplication, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer, QSize, QThread, pyqtSignal, QMutex
from PyQt6.QtGui import QIcon, QKeyEvent, QFont, QAction

from config import Config
from theme import ACCENT, SUCCESS, ERROR, WARNING, TEXT_DIM, BG_DEEP, BG_RAISED, BORDER
from display.video_canvas import VideoCanvas
from display.coordinate_mapper import CoordinateMapper
from network.connection import DGXConnection
from widgets import StatusPill, StatBadge, ToolButton, HDivider, VDivider

log = logging.getLogger("pc.mainwindow")


# ──────────────────────────────────────────────────────────────────────
# Connection worker (runs connect() in background thread)
# ──────────────────────────────────────────────────────────────────────

class _ConnectWorker(QThread):
    success = pyqtSignal(dict)
    failure = pyqtSignal(str)

    def __init__(self, connection: DGXConnection, config: Config,
                 rpc_port: int = 0, video_port: int = 0, input_port: int = 0):
        super().__init__()
        self._conn   = connection
        self._config = config
        # Use explicitly supplied ports if given (from negotiation), else config defaults
        self._rpc_port   = rpc_port   or config.rpc_port
        self._video_port = video_port or config.video_port
        self._input_port = input_port or config.input_port

    def run(self):
        try:
            info = self._conn.connect(
                dgx_ip     = self._config.dgx_ip,
                rpc_port   = self._rpc_port,
                video_port = self._video_port,
                input_port = self._input_port
            )
            self.success.emit(info)
        except Exception as e:
            self.failure.emit(str(e))


# ──────────────────────────────────────────────────────────────────────
# Negotiate-then-Connect worker — port negotiation FIRST, then connect
# ──────────────────────────────────────────────────────────────────────

class _NegotiateConnectWorker(QThread):
    """Runs full port negotiation then connection in one background thread."""
    success  = pyqtSignal(dict)   # connection info dict
    failure  = pyqtSignal(str)    # error message
    progress = pyqtSignal(str)    # status text for UI

    def __init__(self, connection: DGXConnection, config: Config):
        super().__init__()
        self._conn   = connection
        self._config = config

    def run(self):
        from network.port_negotiator import negotiate_ports
        try:
            # ── Step 1: port negotiation ──────────────────────────────
            self.progress.emit("Negotiating ports with DGX…")
            ports = negotiate_ports(self._config.dgx_ip, timeout=8)
            if ports:
                rpc_port   = ports["rpc"]
                video_port = ports["video"]
                input_port = ports["input"]
                # Persist negotiated ports
                self._config.rpc_port        = rpc_port
                self._config.video_port      = video_port
                self._config.input_port      = input_port
                self._config.last_rpc_port   = rpc_port
                self._config.last_video_port = video_port
                self._config.last_input_port = input_port
                self._config.save()
                self.progress.emit(
                    f"Ports agreed  RPC={rpc_port}  Video={video_port}  Input={input_port}"
                )
            else:
                # Fall back to last-known ports from config
                rpc_port   = self._config.last_rpc_port   or self._config.rpc_port
                video_port = self._config.last_video_port or self._config.video_port
                input_port = self._config.last_input_port or self._config.input_port
                self.progress.emit(
                    f"Negotiation failed – using saved ports {rpc_port}/{video_port}/{input_port}"
                )

            # ── Step 2: actually connect ──────────────────────────────
            self.progress.emit(f"Connecting to {self._config.dgx_ip}…")
            info = self._conn.connect(
                dgx_ip     = self._config.dgx_ip,
                rpc_port   = rpc_port,
                video_port = video_port,
                input_port = input_port
            )
            self.success.emit(info)
        except Exception as e:
            self.failure.emit(str(e))


# ──────────────────────────────────────────────────────────────────────
# Main Window
# ──────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.dgx_info: dict = {}
        self.mapper: Optional[CoordinateMapper] = None

        # Connection object (created fresh on each connect)
        self.conn: Optional[DGXConnection] = None

        self._connect_worker: Optional[_ConnectWorker] = None
        self._negotiate_worker: Optional[_NegotiateConnectWorker] = None
        self._transfer_panel_visible = False
        self._is_connecting = False          # guard against overlapping attempts
        self._watchdog_suppressed = False    # True while a connect attempt is in flight

        self._build_ui()
        self._restore_geometry()

        # ── Watchdog auto-reconnect timer ─────────────────────────────
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(max(3, config.reconnect_interval) * 1000)
        self._watchdog.timeout.connect(self._watchdog_tick)

        if config.auto_connect and config.dgx_ip:
            QTimer.singleShot(800, self._connect)

        if config.auto_reconnect and config.dgx_ip:
            self._watchdog.start()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.setWindowTitle("DGX Desktop Remote")
        self.setMinimumSize(800, 500)
        self.setStyleSheet(f"background-color: {BG_DEEP};")

        # ── Central layout ───────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        self._central_layout = QHBoxLayout(central)
        self._central_layout.setContentsMargins(0, 0, 0, 0)
        self._central_layout.setSpacing(0)

        # ── Video canvas ─────────────────────────────────────────────
        self.canvas = VideoCanvas(self)
        self.canvas.files_dropped.connect(self._on_files_dropped)
        self._central_layout.addWidget(self.canvas)

        # ── Transfer sidebar (hidden by default) ─────────────────────
        self._sidebar_container = QWidget()
        self._sidebar_container.setFixedWidth(320)
        self._sidebar_container.setStyleSheet(
            f"background: {BG_RAISED}; border-left: 1px solid {BORDER};"
        )
        self._sidebar_container.setVisible(False)
        self._central_layout.addWidget(self._sidebar_container)
        # Sidebar content is loaded lazily to avoid circular imports
        self._sidebar_built = False

        # ── Toolbar ──────────────────────────────────────────────────
        self._build_toolbar()

        # ── Status bar ───────────────────────────────────────────────
        self._build_statusbar()

        # ── Overlay info (FPS / Ping counters over canvas) ───────────
        self._build_canvas_overlay()

        # ── FPS / Ping update timer ───────────────────────────────────
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._update_stats)
        self._stats_timer.start(500)

    def _build_toolbar(self):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        tb.setStyleSheet(
            f"QToolBar {{ background: {BG_RAISED}; border-bottom: 1px solid {BORDER};"
            f"spacing: 4px; padding: 4px 8px; }}"
        )
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

        # App identifier
        brand = QLabel("  DGX Remote")
        brand.setStyleSheet(
            f"font-weight: 700; font-size: 13px; color: {ACCENT};"
            f"letter-spacing: 1px; padding-right: 8px;"
        )
        tb.addWidget(brand)

        tb.addSeparator()

        # Connect button
        self._btn_connect = QPushButton("  Connect")
        self._btn_connect.setProperty("class", "primary")
        self._btn_connect.setFixedWidth(110)
        self._btn_connect.setFixedHeight(28)
        self._btn_connect.clicked.connect(self._toggle_connection)
        tb.addWidget(self._btn_connect)

        tb.addSeparator()

        # Pin (Always on Top)
        self._btn_pin = ToolButton("📌", "Pin window (Always on Top)", checkable=True)
        self._btn_pin.setChecked(self.config.pinned)
        self._btn_pin.clicked.connect(self._toggle_pin)
        tb.addWidget(self._btn_pin)

        # Fullscreen
        self._btn_fullscreen = ToolButton("⛶", "Toggle fullscreen  (F11)", checkable=True)
        self._btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        tb.addWidget(self._btn_fullscreen)

        tb.addSeparator()

        # File transfer sidebar
        self._btn_files = ToolButton("📁", "File transfer sidebar", checkable=True)
        self._btn_files.clicked.connect(self._toggle_sidebar)
        tb.addWidget(self._btn_files)

        # Spacer
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        spacer.setStyleSheet("background: transparent;")
        tb.addWidget(spacer)

        # DGX host info label
        self._lbl_host = QLabel("")
        self._lbl_host.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11px; padding-right: 6px;"
        )
        tb.addWidget(self._lbl_host)

        # Settings / Manager
        self._btn_menu = ToolButton("⚙", "Manager / Settings")
        self._btn_menu.clicked.connect(self._open_manager)
        tb.addWidget(self._btn_menu)

    def _build_statusbar(self):
        sb = QStatusBar()
        sb.setFixedHeight(26)
        self.setStatusBar(sb)

        self._status_pill = StatusPill()
        sb.addWidget(self._status_pill)

        sb.addWidget(_sep())

        self._badge_fps  = StatBadge("FPS")
        self._badge_ping = StatBadge("PING")
        sb.addWidget(self._badge_fps)
        sb.addWidget(self._badge_ping)

        sb.addWidget(_sep())

        self._lbl_dgx_res = QLabel("")
        self._lbl_dgx_res.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11px; padding: 0 6px;"
        )
        sb.addWidget(self._lbl_dgx_res)

        # Right side — bytes received
        sb.addPermanentWidget(_sep())
        self._lbl_bytes = QLabel("")
        self._lbl_bytes.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11px; padding: 0 8px;"
        )
        sb.addPermanentWidget(self._lbl_bytes)

    def _build_canvas_overlay(self):
        """
        Semi-transparent info overlay in the top-right corner of the canvas.
        Visible only when config.show_fps is True and connected.
        """
        self._overlay = QWidget(self.canvas)
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        ol = QHBoxLayout(self._overlay)
        ol.setContentsMargins(6, 6, 6, 6)
        ol.setSpacing(4)
        self._ol_fps  = _overlay_lbl("0 fps")
        self._ol_ping = _overlay_lbl("0 ms")
        ol.addWidget(self._ol_fps)
        ol.addWidget(self._ol_ping)
        self._overlay.adjustSize()
        self._overlay.setVisible(False)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _toggle_connection(self):
        if self.conn and self.conn.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        if not self.config.dgx_ip:
            QMessageBox.warning(self, "Not Configured",
                                "No DGX IP set.\nOpen Manager to configure.")
            return

        if self._is_connecting:
            return   # already in progress

        self._is_connecting        = True
        self._watchdog_suppressed  = True
        self._btn_connect.setText("  Connecting…")
        self._btn_connect.setEnabled(False)
        self._status_pill.set_state("connecting")

        self.conn = DGXConnection(
            on_frame       = self.canvas.update_frame,
            on_disconnect  = self._on_disconnect_signal,
            on_ping_update = self._on_ping_update
        )

        # Use negotiate-then-connect worker so ports are always fresh
        self._negotiate_worker = _NegotiateConnectWorker(self.conn, self.config)
        self._negotiate_worker.progress.connect(self._on_connect_progress)
        self._negotiate_worker.success.connect(self._on_connect_success)
        self._negotiate_worker.failure.connect(self._on_connect_failure)
        self._negotiate_worker.start()

    def _on_connect_progress(self, msg: str):
        """Show live negotiation/connect status in the status bar."""
        self._status_pill.set_state("connecting")
        self._lbl_host.setText(msg)

    def _on_connect_success(self, info: dict):
        self._is_connecting       = False
        self._watchdog_suppressed = False
        self.dgx_info = info
        disp = info.get("display", {})
        w = disp.get("width", 1920)
        h = disp.get("height", 1080)
        hz = disp.get("refresh_hz", 60)

        self.mapper = CoordinateMapper(dgx_w=w, dgx_h=h)
        self.canvas.connection = self.conn
        self.canvas.mapper     = self.mapper

        self._btn_connect.setText("  Disconnect")
        self._btn_connect.setEnabled(True)
        self._btn_connect.setProperty("class", "danger")
        self._btn_connect.style().unpolish(self._btn_connect)
        self._btn_connect.style().polish(self._btn_connect)

        self._status_pill.set_state("connected")
        host = info.get("hostname", "DGX")
        self._lbl_host.setText(f"{host}  •  {w}×{h} @ {hz}Hz")
        self._lbl_dgx_res.setText(f"{w}×{h}")
        self._overlay.setVisible(self.config.show_fps)

        # Notify tray
        if hasattr(self, "tray"):
            self.tray.set_connected(True, self.config.dgx_ip)

        # Keep watchdog running — it will detect future disconnects
        if self.config.auto_reconnect and not self._watchdog.isActive():
            self._watchdog.start()

        log.info(f"Connected to {host} @ {self.config.dgx_ip}  [{w}×{h}@{hz}]")

    def _on_connect_failure(self, error: str):
        self._is_connecting       = False
        self._watchdog_suppressed = False
        self._btn_connect.setText("  Connect")
        self._btn_connect.setEnabled(True)
        self._btn_connect.setProperty("class", "primary")
        self._btn_connect.style().unpolish(self._btn_connect)
        self._btn_connect.style().polish(self._btn_connect)
        self._status_pill.set_state("error")
        self._lbl_host.setText(f"Failed – retrying…" if self.config.auto_reconnect else f"Failed: {error}")
        log.warning(f"Connection failed: {error}")
        # Watchdog will fire again on next tick and retry automatically

    def _disconnect(self):
        if self.conn:
            self.conn.disconnect()

    def _on_disconnect_signal(self):
        """Called from network thread — schedule UI update on main thread."""
        from PyQt6.QtCore import QMetaObject
        QMetaObject.invokeMethod(self, "_on_disconnect_ui",
                                  Qt.ConnectionType.QueuedConnection)

    def _on_disconnect_ui(self):
        self._is_connecting       = False
        self._watchdog_suppressed = False
        self.canvas.connection = None
        self.canvas.mapper     = None
        self.canvas.clear_frame()
        self.mapper = None
        self._btn_connect.setText("  Connect")
        self._btn_connect.setEnabled(True)
        self._btn_connect.setProperty("class", "primary")
        self._btn_connect.style().unpolish(self._btn_connect)
        self._btn_connect.style().polish(self._btn_connect)
        reconnecting = self.config.auto_reconnect and bool(self.config.dgx_ip)
        self._status_pill.set_state("connecting" if reconnecting else "disconnected")
        self._lbl_host.setText("Waiting for DGX…" if reconnecting else "")
        self._lbl_dgx_res.setText("")
        self._overlay.setVisible(False)
        self._badge_fps.set_value("—")
        self._badge_ping.set_value("—")
        # Notify tray
        if hasattr(self, "tray"):
            self.tray.set_connected(False)
        log.info("Disconnected from DGX")
        # Watchdog will pick up and retry on next tick

    # ------------------------------------------------------------------
    # Watchdog auto-reconnect
    # ------------------------------------------------------------------

    def _watchdog_tick(self):
        """Called every reconnect_interval seconds; reconnects if needed."""
        if self._watchdog_suppressed or self._is_connecting:
            return   # connect attempt already in flight
        connected = bool(self.conn and self.conn.connected)
        if not connected and self.config.auto_reconnect and self.config.dgx_ip:
            log.debug("Watchdog: not connected — attempting reconnect")
            self._connect()

    # ------------------------------------------------------------------
    # Stats update (every 500 ms)
    # ------------------------------------------------------------------

    def _update_stats(self):
        if not self.conn or not self.conn.connected:
            return
        fps  = self.conn.fps_actual
        ping = self.conn.ping_ms

        fps_str  = f"{fps:.0f} fps"
        ping_str = f"{ping:.1f} ms" if ping > 0 else "— ms"

        self._badge_fps.set_value(fps_str)
        self._badge_ping.set_value(ping_str)

        if self.config.show_fps:
            self._ol_fps.setText(f"  {fps_str}  ")
            self._ol_ping.setText(f"  {ping_str}  ")

        # Bytes received
        mb = self.conn.bytes_recv / 1_048_576
        if mb > 1024:
            self._lbl_bytes.setText(f"⬇  {mb/1024:.2f} GB received")
        elif mb > 0:
            self._lbl_bytes.setText(f"⬇  {mb:.1f} MB received")

    def _on_ping_update(self, ping_ms: float):
        pass   # Handled by _update_stats timer

    # ------------------------------------------------------------------
    # Keyboard forwarding (captures from main window, routes to canvas)
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent):
        # F11 — fullscreen toggle
        if event.key() == Qt.Key.Key_F11:
            self._toggle_fullscreen()
            return
        # Escape — exit fullscreen
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
            self._btn_fullscreen.setChecked(False)
            return

        if self.conn and self.conn.connected:
            key  = _qt_key_name(event.key())
            mods = _qt_mods(event.modifiers())
            if key:
                self.canvas.inject_key_press(key, mods)
            return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        if self.conn and self.conn.connected:
            key  = _qt_key_name(event.key())
            mods = _qt_mods(event.modifiers())
            if key:
                self.canvas.inject_key_release(key, mods)
            return
        super().keyReleaseEvent(event)

    # ------------------------------------------------------------------
    # Toolbar actions
    # ------------------------------------------------------------------

    def _toggle_pin(self, checked: bool):
        self.config.pinned = checked
        self.config.save()
        flags = Qt.WindowType.Window
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self._btn_fullscreen.setChecked(False)
        else:
            self.showFullScreen()
            self._btn_fullscreen.setChecked(True)

    def _toggle_sidebar(self, visible: bool):
        if visible and not self._sidebar_built:
            self._build_sidebar_content()
        self._sidebar_container.setVisible(visible)

    def _build_sidebar_content(self):
        """Lazily build the file transfer sidebar."""
        from transfer.transfer_panel import TransferPanel
        layout = QVBoxLayout(self._sidebar_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._transfer_panel = TransferPanel(
            connection=self.conn,
            config=self.config,
            parent=self._sidebar_container
        )
        layout.addWidget(self._transfer_panel)
        self._sidebar_built = True

    def _on_files_dropped(self, paths: list):
        """Files dropped onto the canvas — open sidebar and add to queue."""
        if not self._sidebar_built:
            self._btn_files.setChecked(True)
            self._toggle_sidebar(True)
        self._sidebar_container.setVisible(True)
        self._btn_files.setChecked(True)
        if hasattr(self, "_transfer_panel"):
            self._transfer_panel.enqueue_paths(paths)

    def _open_manager(self):
        from manager_window import ManagerWindow
        dlg = ManagerWindow(self.config, self)
        dlg.exec()

    # ------------------------------------------------------------------
    # Overlay positioning on resize
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._overlay.adjustSize()
        self._overlay.move(
            self.canvas.width() - self._overlay.width() - 8, 8
        )

    # ------------------------------------------------------------------
    # Geometry persistence
    # ------------------------------------------------------------------

    def _restore_geometry(self):
        c = self.config
        if c.win_x >= 0 and c.win_y >= 0:
            self.move(c.win_x, c.win_y)
        self.resize(max(800, c.win_w), max(500, c.win_h))

    def closeEvent(self, event):
        # Stop watchdog before closing so it doesn't fire during shutdown
        self._watchdog.stop()
        self._is_connecting = False
        geo = self.geometry()
        self.config.win_x = geo.x()
        self.config.win_y = geo.y()
        self.config.win_w = geo.width()
        self.config.win_h = geo.height()
        self.config.save()
        if self.conn:
            self.conn.disconnect()
        super().closeEvent(event)


# ──────────────────────────────────────────────────────────────────────
# Key mapping helpers
# ──────────────────────────────────────────────────────────────────────

def _qt_key_name(key: int) -> str:
    from PyQt6.QtCore import Qt
    _MAP = {
        Qt.Key.Key_Return:    "Return",
        Qt.Key.Key_Enter:     "KP_Enter",
        Qt.Key.Key_Escape:    "Escape",
        Qt.Key.Key_Tab:       "Tab",
        Qt.Key.Key_Backtab:   "ISO_Left_Tab",
        Qt.Key.Key_Backspace: "BackSpace",
        Qt.Key.Key_Delete:    "Delete",
        Qt.Key.Key_Insert:    "Insert",
        Qt.Key.Key_Up:        "Up",
        Qt.Key.Key_Down:      "Down",
        Qt.Key.Key_Left:      "Left",
        Qt.Key.Key_Right:     "Right",
        Qt.Key.Key_Home:      "Home",
        Qt.Key.Key_End:       "End",
        Qt.Key.Key_PageUp:    "Prior",
        Qt.Key.Key_PageDown:  "Next",
        Qt.Key.Key_Space:     "space",
        Qt.Key.Key_F1:  "F1",  Qt.Key.Key_F2:  "F2",  Qt.Key.Key_F3:  "F3",
        Qt.Key.Key_F4:  "F4",  Qt.Key.Key_F5:  "F5",  Qt.Key.Key_F6:  "F6",
        Qt.Key.Key_F7:  "F7",  Qt.Key.Key_F8:  "F8",  Qt.Key.Key_F9:  "F9",
        Qt.Key.Key_F10: "F10", Qt.Key.Key_F11: "F11", Qt.Key.Key_F12: "F12",
    }
    if key in _MAP:
        return _MAP[key]
    if 32 <= key <= 126:
        return chr(key).lower()
    return ""


def _qt_mods(mods) -> list:
    from PyQt6.QtCore import Qt
    out = []
    if mods & Qt.KeyboardModifier.ControlModifier: out.append("ctrl")
    if mods & Qt.KeyboardModifier.ShiftModifier:   out.append("shift")
    if mods & Qt.KeyboardModifier.AltModifier:     out.append("alt")
    if mods & Qt.KeyboardModifier.MetaModifier:    out.append("super")
    return out


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _sep() -> QWidget:
    """Thin vertical separator for status bar."""
    f = QWidget()
    f.setFixedWidth(1)
    f.setFixedHeight(14)
    f.setStyleSheet(f"background: {BORDER};")
    return f


def _overlay_lbl(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        "color: rgba(255,255,255,0.85);"
        "background: rgba(0,0,0,0.55);"
        "border-radius: 4px;"
        "padding: 2px 6px;"
        "font-size: 11px;"
        "font-family: 'Cascadia Code', 'Consolas', monospace;"
    )
    return lbl
