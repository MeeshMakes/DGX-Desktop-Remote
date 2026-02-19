"""
dgx-service/src/manager_gui.py
PyQt6 system-tray manager for the DGX service (runs on DGX with a display).
Shows connection status, FPS, control buttons, and a slide-out transfer drawer.

Transfer drawer (📤 Transfers button):
  ┌────────────────────────────────────────┐
  │ ↑  SEND TO PC   drop DGX files here   │
  │  (drop zone / result list)             │
  │  [ 📂 SharedDrive ]  [ Clear ]         │
  ├────────────────────────────────────────┤
  │ ↓  INCOMING FROM PC  (PCTransfer)     │
  │  (list of arrived files, drag-out)     │
  │  [ ↻ Refresh ]  [ 📂 Open Folder ]    │
  └────────────────────────────────────────┘
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import logging
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QMimeData, QTimer, QUrl,
    pyqtSignal, QObject, QLockFile,
)
from PyQt6.QtGui import (
    QDrag, QIcon, QPixmap, QPainter, QColor, QBrush,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from console_window import ConsoleWindow

SHARED_DRIVE   = Path.home() / "SharedDrive"
BRIDGE_STAGING = Path.home() / "BridgeStaging"
PC_TRANSFER    = Path.home() / "Desktop" / "PC-Transfer"

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

_ACCENT    = "#6C63FF"
_BG_RAISED = "#22223a"
_BG_ITEM   = "#12121a"
_BORDER    = "#3d3d5c"
_TEXT_DIM  = "#7070A0"
_TEXT_MAIN = "#e4e4f0"
_SUCCESS   = "#22D47E"
_WARNING   = "#FFCC44"


# ──────────────────────────────────────────────────────────────────────
# Transfer drawer helpers
# ──────────────────────────────────────────────────────────────────────

_EXT_EMOJI: dict[str, str] = {
    ".py": "🐍", ".sh": "⚡", ".bash": "⚡", ".c": "🔧", ".cpp": "🔧",
    ".js": "🟨", ".ts": "🟦", ".json": "📋", ".yaml": "📋",
    ".jpg": "🖼", ".jpeg": "🖼", ".png": "🖼", ".gif": "🖼",
    ".pdf": "📕", ".md": "📝", ".txt": "📝", ".csv": "📊",
    ".zip": "📦", ".tar": "📦", ".gz": "📦",
    ".mp4": "🎬", ".mp3": "🎵", ".exe": "⚙",
}


def _emoji(name: str) -> str:
    return _EXT_EMOJI.get(Path(name).suffix.lower(), "📄")


def _human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}"
        n //= 1024
    return f"{n:.1f} TB"


class _DraggableList(QListWidget):
    """QListWidget whose items drag out as file:// URLs (for DGX desktop drops)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setAcceptDrops(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setStyleSheet(
            f"QListWidget {{ background: {_BG_ITEM}; border: 1px solid {_BORDER};"
            f"  border-radius: 5px; color: {_TEXT_MAIN}; font-size: 12px; }}"
            f"QListWidget::item {{ padding: 5px 8px; }}"
            f"QListWidget::item:selected {{ background: {_ACCENT}44; }}"
            f"QListWidget::item:hover {{ background: #2a2a42; }}"
        )

    def startDrag(self, actions) -> None:
        urls = []
        for item in self.selectedItems():
            path = item.data(Qt.ItemDataRole.UserRole)
            if path and Path(path).exists():
                urls.append(QUrl.fromLocalFile(path))
        if not urls:
            return
        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def contextMenuEvent(self, event) -> None:
        items = self.selectedItems()
        if not items:
            return
        menu = QMenu(self)
        a_open = menu.addAction("📂  Show in Files")
        a_open.triggered.connect(lambda: self._reveal(items))
        menu.addSeparator()
        n = len(items)
        a_rm = menu.addAction(f"✕  Remove {n} item{'s' if n > 1 else ''} from list")
        a_rm.triggered.connect(lambda: [self.takeItem(self.row(i)) for i in list(items)])
        menu.exec(event.globalPos())

    def _reveal(self, items) -> None:
        for item in items:
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                try:
                    subprocess.Popen(["xdg-open", str(Path(path).parent)])
                except Exception:
                    pass


class _DropZone(QWidget):
    """
    Drop zone + result list for 'Send to PC'.
    Accepts drops of local DGX files, copies them to ~/SharedDrive/,
    and notifies the PC via svc.push_file_to_pc().
    """

    _item_ready = pyqtSignal(str, str, int, bool)   # name, path, size, pushed

    def __init__(self, svc, parent=None):
        super().__init__(parent)
        self._svc    = svc
        self.setAcceptDrops(True)
        self._item_ready.connect(self._add_item_safe)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # Drop-hint label (shown when list is empty)
        self._hint = QLabel(
            "Drop DGX files or folders here\n"
            "to copy them to ~/SharedDrive/\n"
            "and send them to the PC."
        )
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setStyleSheet(
            f"color: {_TEXT_DIM}; font-size: 12px; padding: 20px;"
            f"border: 2px dashed {_BORDER}; border-radius: 6px;"
        )
        root.addWidget(self._hint)

        # Result list (hidden until something lands)
        self._list = _DraggableList()
        self._list.setVisible(False)
        root.addWidget(self._list, 1)

        # Footer buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_shared = QPushButton("📂 SharedDrive")
        btn_shared.setToolTip("Open ~/SharedDrive/ on DGX")
        btn_shared.clicked.connect(lambda: subprocess.Popen(
            ["xdg-open", str(SHARED_DRIVE)]))
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self._clear)
        btn_row.addWidget(btn_shared)
        btn_row.addStretch()
        btn_row.addWidget(btn_clear)
        root.addLayout(btn_row)

    # ── Drop handling ─────────────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            self.setStyleSheet(f"background: {_ACCENT}18; border-radius: 6px;")
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.setStyleSheet("")

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        self.setStyleSheet("")
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            for p in paths:
                threading.Thread(
                    target=self._process_file,
                    args=(p,),
                    daemon=True,
                ).start()
        else:
            event.ignore()

    def _process_file(self, src_path: str) -> None:
        src = Path(src_path)
        if not src.exists():
            return
        SHARED_DRIVE.mkdir(parents=True, exist_ok=True)
        dest = SHARED_DRIVE / src.name
        n = 1
        while dest.exists():
            dest = SHARED_DRIVE / f"{src.stem} ({n}){src.suffix}"
            n += 1
        try:
            if src.is_dir():
                shutil.copytree(str(src), str(dest))
            else:
                shutil.copy2(str(src), str(dest))
        except Exception as exc:
            logging.getLogger("dgx.manager").warning(
                "Failed to copy %s to SharedDrive: %s", src, exc)
            return

        size   = dest.stat().st_size
        pushed = self._svc.push_file_to_pc(dest.name, size) if self._svc else False
        # Signal back to GUI thread
        self._item_ready.emit(dest.name, str(dest), size, pushed)

    def _add_item_safe(self, name: str, path: str, size: int, pushed: bool) -> None:
        status = "→ PC  ✓" if pushed else "staged (no PC connected)"
        label  = f"{_emoji(name)}  {name}   ({_human(size)})   [{status}]"
        item   = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setForeground(QColor(_SUCCESS if pushed else _WARNING))
        self._list.addItem(item)
        self._hint.setVisible(False)
        self._list.setVisible(True)

    def _clear(self) -> None:
        self._list.clear()
        self._list.setVisible(False)
        self._hint.setVisible(True)


class _IncomingPane(QWidget):
    """
    Shows files that arrived from the PC (in ~/BridgeStaging/ and
    ~/Desktop/PC-Transfer/inbox/).  Items are draggable to the DGX desktop.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        self._list = _DraggableList()
        self._list.setMinimumHeight(100)
        root.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_refresh = QPushButton("↻ Refresh")
        btn_refresh.clicked.connect(self._refresh)
        btn_open = QPushButton("📂 Open Folder")
        btn_open.setToolTip("Open ~/BridgeStaging/ and ~/Desktop/PC-Transfer/inbox/")
        btn_open.clicked.connect(self._open_folders)
        btn_row.addWidget(btn_refresh)
        btn_row.addStretch()
        btn_row.addWidget(btn_open)
        root.addLayout(btn_row)

        self._refresh()

    def _refresh(self) -> None:
        self._list.clear()
        files: list[Path] = []
        # BridgeStaging (all sessions flattened)
        if BRIDGE_STAGING.exists():
            files += sorted(BRIDGE_STAGING.rglob("*"), key=lambda p: p.stat().st_mtime
                            if p.is_file() else 0, reverse=True)
        # PC-Transfer inbox
        inbox = PC_TRANSFER / "inbox"
        if inbox.exists():
            files += sorted(inbox.iterdir(), key=lambda p: p.stat().st_mtime
                            if p.is_file() else 0, reverse=True)
        for f in files:
            if not f.is_file():
                continue
            size  = f.stat().st_size
            label = f"{_emoji(f.name)}  {f.name}   ({_human(size)})"
            item  = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            item.setToolTip(str(f))
            self._list.addItem(item)
        if self._list.count() == 0:
            placeholder = QListWidgetItem("  (no incoming files yet)")
            placeholder.setForeground(QColor(_TEXT_DIM))
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(placeholder)

    def _open_folders(self) -> None:
        for folder in (BRIDGE_STAGING, PC_TRANSFER / "inbox"):
            folder.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.Popen(["xdg-open", str(folder)])
            except Exception:
                pass


class _TransferDrawer(QWidget):
    """
    Slide-out transfer drawer embedded in the ManagerWindow.

    Top pane:    Send to PC    (DGX → PC)
    Bottom pane: Incoming from PC  (PC → DGX)
    """

    def __init__(self, svc, parent=None):
        super().__init__(parent)
        self._svc = svc
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(8)

        # ── Divider ───────────────────────────────────────────────────
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"background: {_BORDER};")
        line.setFixedHeight(1)
        root.addWidget(line)

        # ── Send to PC ────────────────────────────────────────────────
        grp_send = QGroupBox("↑  Send to PC  (DGX → PC)")
        sv = QVBoxLayout(grp_send)
        self._drop_zone = _DropZone(self._svc)
        self._drop_zone.setMinimumHeight(150)
        sv.addWidget(self._drop_zone)
        root.addWidget(grp_send)

        # ── Incoming from PC ──────────────────────────────────────────
        grp_inc = QGroupBox("↓  Incoming from PC  (PC → DGX)")
        iv = QVBoxLayout(grp_inc)
        self._incoming = _IncomingPane()
        iv.addWidget(self._incoming)
        root.addWidget(grp_inc)

    def refresh_incoming(self) -> None:
        self._incoming._refresh()


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
        # Normal top-level window: shows in taskbar, X button enabled
        self.setWindowFlags(Qt.WindowType.Window)

        # Console window — captures all logs, auto-opens on errors
        self.console = ConsoleWindow(self, title="DGX Service — Console")
        self.console.attach()   # hook into root logger

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
        btn_minimize = QPushButton("Minimize")
        btn_minimize.clicked.connect(self.showMinimized)
        btn_console = QPushButton("🖥  Console")
        btn_console.setToolTip("Show live log / error console")
        btn_console.clicked.connect(self._toggle_console)
        self._btn_transfers = QPushButton("\U0001f4e4  Transfers")
        self._btn_transfers.setToolTip("Show/hide the file Transfer Drawer")
        self._btn_transfers.setCheckable(True)
        self._btn_transfers.clicked.connect(self._toggle_drawer)
        btn_row.addWidget(btn_console)
        btn_row.addWidget(self._btn_transfers)
        btn_row.addWidget(btn_minimize)
        btn_row.addWidget(self._btn_stop)
        l.addLayout(btn_row)

        # Transfer drawer (hidden by default, revealed by 'Transfers' button)
        self._drawer = _TransferDrawer(self._svc)
        self._drawer.setVisible(False)
        l.addWidget(self._drawer)

    def _toggle_console(self):
        if self.console.isVisible():
            self.console.hide()
        else:
            self.console.show()
            self.console.raise_()

    def _toggle_drawer(self, checked: bool) -> None:
        self._drawer.setVisible(checked)
        if checked:
            self._drawer.refresh_incoming()
        self.adjustSize()

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

    def closeEvent(self, event):
        """X button — stop the service and terminate the process."""
        event.accept()
        if self._svc:
            threading.Thread(
                target=lambda: (__import__('time').sleep(0.1), self._svc.stop(), QApplication.quit()),
                daemon=True,
            ).start()
        else:
            QApplication.quit()

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
    import tempfile

    app = QApplication.instance() or QApplication(sys.argv)

    # ── Single-instance guard ─────────────────────────────────────────
    _lock = QLockFile(os.path.join(tempfile.gettempdir(), "dgx-desktop-remote-manager.lock"))
    if not _lock.tryLock(100):
        logging.getLogger("dgx_service").warning(
            "Manager GUI already running — refusing to open a second instance."
        )
        return
    # Keep lock alive for the lifetime of the app
    app._single_instance_lock = _lock

    app.setStyleSheet(_STYLE)
    app.setQuitOnLastWindowClosed(True)   # quitting the window quits the app

    win  = ManagerWindow(service)
    win.show()
    win.raise_()
    win.activateWindow()

    def _show_manager():
        win.showNormal()
        win.raise_()
        win.activateWindow()

    tray  = QSystemTrayIcon(_make_icon())
    tray.setToolTip("DGX Desktop Remote Service")
    menu  = QMenu()
    menu.addAction("Show Manager", _show_manager)
    menu.addSeparator()
    menu.addAction("Quit",         lambda: (service.stop(), app.quit()))
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda r: _show_manager() if r == QSystemTrayIcon.ActivationReason.DoubleClick else None
    )
    tray.show()

    app.exec()
