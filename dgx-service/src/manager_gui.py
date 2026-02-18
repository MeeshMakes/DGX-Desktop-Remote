"""
dgx-service/src/manager_gui.py
PyQt6 system-tray manager for the DGX service (runs on DGX with a display).
Shows connection status, FPS, control buttons.
"""

import sys
import threading
import os

from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QWidget,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QSpinBox, QCheckBox, QDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui  import QIcon, QPixmap, QPainter, QColor, QBrush

# ──────────────────────────────────────────────────────────────────────
# Stylesheet (lighter variant for DGX Ubuntu desktop readability)
# ──────────────────────────────────────────────────────────────────────
_STYLE = """
QWidget         { background: #1a1a2e; color: #e4e4f0; font-family: 'Ubuntu', sans-serif; font-size: 13px; }
QPushButton     { background: #2d2d4a; border: 1px solid #3d3d5c; border-radius: 6px; padding: 6px 14px; }
QPushButton:hover { background: #3d3d5c; }
QPushButton.primary { background: #6C63FF; border: none; color: #fff; font-weight: 600; }
QPushButton.danger  { background: #FF4F5E; border: none; color: #fff; font-weight: 600; }
QLabel          { background: transparent; }
QGroupBox       { border: 1px solid #3d3d5c; border-radius: 8px; margin-top: 12px; padding: 10px; }
QGroupBox::title { background: transparent; subcontrol-origin: margin; left: 10px; top: -7px; padding: 0 4px; color: #6C63FF; font-weight: 600; }
QSpinBox        { background: #12121a; border: 1px solid #3d3d5c; border-radius: 5px; padding: 4px 8px; }
"""


# ──────────────────────────────────────────────────────────────────────
# Signal bridge (so background threads can update UI)
# ──────────────────────────────────────────────────────────────────────

class _Bridge(QObject):
    status_changed = pyqtSignal(str, str)   # (status_text, color)
    stats_updated  = pyqtSignal(int, int)   # (fps, clients)


# ──────────────────────────────────────────────────────────────────────
# Manager window
# ──────────────────────────────────────────────────────────────────────

class ManagerWindow(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self._svc    = service
        self._bridge = _Bridge()
        self._bridge.status_changed.connect(self._on_status_changed)
        self._bridge.stats_updated.connect(self._on_stats_updated)

        self.setWindowTitle("DGX Desktop Remote — Service Manager")
        self.setMinimumWidth(400)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self._build_ui()

        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh_stats)
        self._timer.start(1000)

    def _build_ui(self):
        l = QVBoxLayout(self)
        l.setSpacing(10)

        # Header
        hdr = QLabel("DGX Desktop Remote")
        hdr.setStyleSheet("font-size: 20px; font-weight: 700; color: #6C63FF; padding-bottom: 4px;")
        l.addWidget(hdr)
        sub = QLabel("Service Manager")
        sub.setStyleSheet("color: #888; font-size: 12px;")
        l.addWidget(sub)

        # ── DGX IP (auto-fill) ────────────────────────────────────────
        grp_ip = QGroupBox("This DGX's IP Address")
        ip_row = QHBoxLayout(grp_ip)
        self._ip_field = QLineEdit()
        self._ip_field.setPlaceholderText("e.g.  10.0.0.1")
        self._ip_field.setReadOnly(True)
        self._ip_field.setStyleSheet("background: #12121a; border: 1px solid #3d3d5c; border-radius: 5px; padding: 4px 8px;")
        ip_row.addWidget(self._ip_field)
        btn_autofill = QPushButton("Auto-Fill")
        btn_autofill.setFixedWidth(90)
        btn_autofill.clicked.connect(self._autofill_ip)
        btn_autofill.setProperty("class", "primary")
        ip_row.addWidget(btn_autofill)
        l.addWidget(grp_ip)
        # Auto-fill on start
        self._autofill_ip()

        # Status
        grp_status = QGroupBox("Status")
        fl = QFormLayout(grp_status)
        self._lbl_status  = QLabel("Running")
        self._lbl_status.setStyleSheet("color: #22D47E; font-weight: 600;")
        self._lbl_clients = QLabel("0")
        self._lbl_fps     = QLabel("—")
        self._lbl_res     = QLabel("—")
        self._lbl_ports   = QLabel(self._ports_str())
        fl.addRow("Service:",      self._lbl_status)
        fl.addRow("Clients:",      self._lbl_clients)
        fl.addRow("Capture FPS:",  self._lbl_fps)
        fl.addRow("Resolution:",   self._lbl_res)
        fl.addRow("Active Ports:", self._lbl_ports)
        l.addWidget(grp_status)

        # Settings
        grp_set = QGroupBox("Capture Settings")
        fl2 = QFormLayout(grp_set)
        self._fps_spin     = QSpinBox(); self._fps_spin.setRange(5, 60);    self._fps_spin.setValue(60)
        self._quality_spin = QSpinBox(); self._quality_spin.setRange(40, 100); self._quality_spin.setValue(85)
        fl2.addRow("Target FPS:",    self._fps_spin)
        fl2.addRow("JPEG Quality:", self._quality_spin)
        btn_apply = QPushButton("Apply")
        btn_apply.setProperty("class", "primary")
        btn_apply.clicked.connect(self._apply_settings)
        fl2.addRow("", btn_apply)
        l.addWidget(grp_set)

        # Control buttons
        btn_row = QHBoxLayout()
        self._btn_stop = QPushButton("Stop Service")
        self._btn_stop.setProperty("class", "danger")
        self._btn_stop.clicked.connect(self._stop_service)
        btn_hide = QPushButton("Minimize to Tray")
        btn_hide.clicked.connect(self.hide)
        btn_row.addWidget(btn_hide)
        btn_row.addWidget(self._btn_stop)
        l.addLayout(btn_row)

    def _ports_str(self) -> str:
        if not self._svc:
            return "—"
        return (
            f"RPC {self._svc.rpc_port}  ·  "
            f"Video {self._svc.video_port}  ·  "
            f"Input {self._svc.input_port}  ·  "
            f"Discovery {22000}"
        )

    def _autofill_ip(self):
        """Detect this DGX's IP on the PC-facing interface."""
        try:
            import socket as _s
            with _s.socket(_s.AF_INET, _s.SOCK_DGRAM) as s:
                s.connect(("10.0.0.2", 80))
                ip = s.getsockname()[0]
        except Exception:
            try:
                import socket as _s
                ip = _s.gethostbyname(_s.gethostname())
            except Exception:
                ip = "10.0.0.1"
        self._ip_field.setText(ip)

    def _refresh_stats(self):
        if not self._svc:
            return
        w, h = self._svc.resolution_monitor.current
        self._lbl_res.setText(f"{w} × {h}")
        fps = getattr(self._svc.capture, "_fps", "—")
        self._lbl_fps.setText(str(fps))
        self._lbl_ports.setText(self._ports_str())

    def _apply_settings(self):
        if self._svc:
            self._svc.capture.set_params(
                fps=self._fps_spin.value(),
                quality=self._quality_spin.value(),
            )

    def _stop_service(self):
        if self._svc:
            threading.Thread(
                target=lambda: (__import__("time").sleep(0.3), self._svc.stop(), QApplication.quit()),
                daemon=True,
            ).start()

    def _on_status_changed(self, text: str, color: str):
        self._lbl_status.setText(text)
        self._lbl_status.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _on_stats_updated(self, fps: int, clients: int):
        self._lbl_fps.setText(str(fps))
        self._lbl_clients.setText(str(clients))


# ──────────────────────────────────────────────────────────────────────
# Tray icon
# ──────────────────────────────────────────────────────────────────────

def _make_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor("#6C63FF")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(8, 8, 48, 44, 8, 8)
    p.setPen(QColor("#c0c0d8"))
    for y in [26, 33, 40]:
        p.drawLine(18, y, 46, y)
    p.setBrush(QBrush(QColor("#22D47E")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(46, 46, 14, 14)
    p.end()
    return QIcon(pm)


def run_manager_gui(service):
    """Call from dgx_service.py main thread to run the Qt manager."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(_STYLE)
    app.setQuitOnLastWindowClosed(False)

    win  = ManagerWindow(service)

    tray  = QSystemTrayIcon(_make_icon())
    tray.setToolTip("DGX Desktop Remote Service")
    menu  = QMenu()
    menu.addAction("Show Manager", win.show)
    menu.addSeparator()
    menu.addAction("Quit",         lambda: (service.stop(), app.quit()))
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda r: win.show() if r == QSystemTrayIcon.ActivationReason.DoubleClick else None
    )
    tray.show()

    app.exec()
