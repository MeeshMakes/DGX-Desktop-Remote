"""
pc-application/src/system_tray.py
System tray icon + context menu for DGX Desktop Remote.
"""

from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QBrush
from PyQt6.QtCore import Qt, QSize


def _make_tray_icon(connected: bool = False) -> QIcon:
    """Render a small DGX icon with a status dot."""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Body (rounded square)
    body_color = QColor("#6C63FF") if connected else QColor("#3b3b5c")
    p.setBrush(QBrush(body_color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(8, 8, 48, 44, 8, 8)

    # Screen lines
    p.setPen(QColor("#ffffff" if connected else "#888888"))
    p.setBrush(Qt.BrushStyle.NoBrush)
    line_col = QColor("#c0c0d8" if connected else "#606070")
    p.setPen(line_col)
    for y in [26, 33, 40]:
        p.drawLine(18, y, 46, y)

    # Status dot
    dot_color = QColor("#22D47E") if connected else QColor("#FF4F5E")
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(dot_color))
    p.drawEllipse(46, 46, 14, 14)

    p.end()
    return QIcon(pm)


class AppSystemTray(QSystemTrayIcon):

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._win = main_window
        self.setIcon(_make_tray_icon(False))
        self.setToolTip("DGX Desktop Remote — Disconnected")

        self._menu = QMenu()
        self._act_show    = self._menu.addAction("Show Window")
        self._menu.addSeparator()
        self._act_connect = self._menu.addAction("Connect")
        self._act_disconnect = self._menu.addAction("Disconnect")
        self._act_disconnect.setEnabled(False)
        self._menu.addSeparator()
        self._act_quit    = self._menu.addAction("Quit")

        self._act_show.triggered.connect(self._show_window)
        self._act_connect.triggered.connect(self._win._connect)
        self._act_disconnect.triggered.connect(self._win._disconnect)
        self._act_quit.triggered.connect(QApplication.quit)

        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activate)

    def set_connected(self, connected: bool, host: str = ""):
        self.setIcon(_make_tray_icon(connected))
        if connected:
            self.setToolTip(f"DGX Desktop Remote — {host}")
            self._act_connect.setEnabled(False)
            self._act_disconnect.setEnabled(True)
        else:
            self.setToolTip("DGX Desktop Remote — Disconnected")
            self._act_connect.setEnabled(True)
            self._act_disconnect.setEnabled(False)

    def _show_window(self):
        self._win.showNormal()
        self._win.raise_()
        self._win.activateWindow()

    def _on_activate(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()
