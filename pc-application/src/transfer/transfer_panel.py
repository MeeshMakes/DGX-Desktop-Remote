"""
pc-application/src/transfer/transfer_panel.py

Two-pane transfer sidebar.

Layout
  ┌────────────────────────────────────┐
  │ Transfers                    [×]   │  header
  │▓▓▓▓▓▓░░░░░  Processing…           │  3 px global bar (only while active)
  ├────────────────────────────────────┤
  │ ↑  SEND TO DGX  drop PC files here │  Panel A header
  │                                    │
  │  🐍  build.sh         → Desktop   │  ← final converted+placed items
  │  🖼  photo.jpg        → Pictures  │
  │  …  (hint shown when empty)        │
  ├────────────────────────────────────┤
  │ ↓  SEND TO PC   from DGX          │  Panel B header
  │                                    │
  │  📄  report.pdf  ~/Downloads      │  ← locally-delivered items
  │  …  (hint shown when empty)        │
  ├────────────────────────────────────┤
  │ 📁 Stage  🗒 Log           Clear  │  footer
  └────────────────────────────────────┘

Panel A  (PC → DGX):
  • Drop any PC files/folders onto the panel.
  • Each file is copied to a hidden working dir, converted (e.g. .bat→.sh),
    sent to DGX BridgeStaging, then auto-placed at its final DGX destination.
  • The working copy is deleted automatically after a successful send.
  • The list shows ONLY the final converted+placed outputs (name + DGX parent dir).
  • Shift/Ctrl multi-select; click any item → opens its containing DGX folder.
  • Right-click → Open Folder on DGX | Copy path | Remove from list.

Panel B  (DGX → PC):
  • Populated when add_received_file() is called (e.g. from SharedDrivePanel).
  • Shows locally-delivered files (name + parent dir).
  • Shift/Ctrl multi-select; right-click → Show in Explorer | Copy path | Remove.
  • Items are draggable out of the panel into any Windows folder.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import (
    Qt, QMimeData, QUrl, pyqtSignal, pyqtSlot,
)
from PyQt6.QtCore import QFileInfo
from PyQt6.QtGui import (
    QDrag,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from theme import (
    ACCENT, BG_BASE, BG_DEEP, BG_RAISED, BG_SURFACE,
    BORDER, SUCCESS, TEXT_DIM, TEXT_MAIN,
)
from .transfer_session import TransferSession
from .transfer_worker import TransferWorker

log = logging.getLogger("pc.transfer.panel")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


_EXT_EMOJI: dict[str, str] = {
    ".py":   "🐍", ".sh":   "⚡", ".bash": "⚡", ".zsh": "⚡",
    ".c":    "🔧", ".cpp":  "🔧", ".h":    "🔧", ".rs":  "🔧",
    ".js":   "🟨", ".ts":   "🟦", ".json": "📋", ".xml": "📋",
    ".html": "🌐", ".css":  "🌐", ".yaml": "📋", ".toml": "📋",
    ".jpg":  "🖼", ".jpeg": "🖼", ".png":  "🖼", ".gif": "🖼",
    ".bmp":  "🖼", ".svg":  "🖼", ".webp": "🖼",
    ".pdf":  "📕", ".md":   "📝", ".txt":  "📝", ".csv": "📊",
    ".zip":  "📦", ".tar":  "📦", ".gz":   "📦", ".7z":  "📦",
    ".mp4":  "🎬", ".mov":  "🎬", ".avi":  "🎬", ".mkv": "🎬",
    ".mp3":  "🎵", ".wav":  "🎵", ".flac": "🎵",
    ".exe":  "⚙",  ".msi":  "⚙",  ".dll":  "⚙",
    ".bat":  "⚙",  ".cmd":  "⚙",  ".ps1":  "⚙",
}


def _file_emoji(name: str) -> str:
    return _EXT_EMOJI.get(Path(name).suffix.lower(), "📄")


def _shell_icon(path: str):
    """Return a QIcon from the Windows shell icon provider, or None."""
    try:
        if not Path(path).exists():
            return None
        return QFileIconProvider().icon(QFileInfo(path))
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# _DeliveredItem  — data attached to each list row
# ──────────────────────────────────────────────────────────────────────────────

class _DeliveredItem:
    """
    Metadata stored in each QListWidgetItem.

    Panel A  →  name = DGX filename (converted),   dest = absolute DGX path
    Panel B  →  name = local filename,              dest = absolute local path
    """
    __slots__ = ("name", "dest", "local_path")

    def __init__(self, name: str, dest: str, local_path: str = "") -> None:
        self.name       = name
        self.dest       = dest        # full destination-side path
        self.local_path = local_path  # only non-empty for Panel B


# ──────────────────────────────────────────────────────────────────────────────
# _ResultsView  — list widget used inside both panels
# ──────────────────────────────────────────────────────────────────────────────

class _ResultsView(QListWidget):
    """
    Final-deliverables list.

    side="dgx" — Panel A:
        • Single-click opens the DGX parent folder via RPC.
        • Right-click: Open Folder on DGX | Copy path | Remove.
        • Items NOT draggable (they live on DGX, not on the PC).

    side="pc"  — Panel B:
        • Extended Shift/Ctrl selection.
        • Right-click: Show in Explorer | Copy path | Remove.
        • Items draggable out (Drag → any Windows folder).
    """

    def __init__(self, side: str, conn_getter: Callable = None, parent=None):
        super().__init__(parent)
        self._side        = side
        self._conn_getter = conn_getter
        self._data: list[_DeliveredItem] = []

        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(side == "pc")
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setAcceptDrops(False)
        self.setSpacing(1)
        self.setStyleSheet(
            f"QListWidget {{ background: {BG_BASE}; border: none; outline: none;"
            f"  color: {TEXT_MAIN}; font-size: 11px; }}"
            f"QListWidget::item {{ padding: 4px 8px; border-radius: 3px; }}"
            f"QListWidget::item:selected {{ background: {ACCENT}33; color: {TEXT_MAIN}; }}"
            f"QListWidget::item:hover {{ background: {BG_SURFACE}; }}"
        )

        # Single-click on Panel A → open DGX folder
        if side == "dgx":
            self.itemClicked.connect(self._on_dgx_item_clicked)

    # ── Population ────────────────────────────────────────────────────

    def add_result(self, item: _DeliveredItem) -> None:
        self._data.append(item)
        dest_dir = Path(item.dest).parent.name if item.dest else ""
        label = f"{_file_emoji(item.name)}  {item.name}"
        if dest_dir:
            label += f"   →  {dest_dir}"

        row = QListWidgetItem(label)
        row.setToolTip(item.dest or item.local_path)
        row.setData(Qt.ItemDataRole.UserRole, item)

        # Windows shell icon for PC-local files
        if self._side == "pc" and item.local_path:
            icon = _shell_icon(item.local_path)
            if icon:
                row.setIcon(icon)

        self.addItem(row)

    def clear_results(self) -> None:
        self._data.clear()
        self.clear()

    # ── DGX-side click ────────────────────────────────────────────────

    def _on_dgx_item_clicked(self, lw: QListWidgetItem) -> None:
        item: _DeliveredItem = lw.data(Qt.ItemDataRole.UserRole)
        if item:
            self._rpc_open_folder(str(Path(item.dest).parent) if item.dest else "")

    def _rpc_open_folder(self, folder: str) -> None:
        conn = self._conn_getter() if self._conn_getter else None
        if not conn or not folder:
            return
        try:
            conn.rpc({"type": "open_path", "path": folder}, timeout=5)
        except Exception as exc:
            log.debug("open_path RPC skipped (%s): %s", folder, exc)

    # ── Context menu ──────────────────────────────────────────────────

    def contextMenuEvent(self, event) -> None:
        if not self.selectedItems():
            return

        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {BG_RAISED}; color: {TEXT_MAIN};"
            f"  border: 1px solid {BORDER}; border-radius: 5px; padding: 4px; }}"
            f"QMenu::item {{ padding: 5px 22px; border-radius: 3px; }}"
            f"QMenu::item:selected {{ background: {ACCENT}44; }}"
            f"QMenu::separator {{ height: 1px; background: {BORDER}; margin: 3px 6px; }}"
        )

        if self._side == "pc":
            a = menu.addAction("📂  Show in Explorer")
            a.triggered.connect(self._show_in_explorer)
        else:
            a = menu.addAction("📂  Open Folder on DGX")
            a.triggered.connect(self._open_dgx_folders_selection)

        menu.addSeparator()
        a2 = menu.addAction("📋  Copy path")
        a2.triggered.connect(self._copy_paths)
        menu.addSeparator()

        n = len(self.selectedItems())
        a3 = menu.addAction(f"✕  Remove {n} item{'s' if n > 1 else ''} from list")
        a3.triggered.connect(self._remove_selected)

        menu.exec(event.globalPos())

    def _show_in_explorer(self) -> None:
        for lw in list(self.selectedItems()):
            item: _DeliveredItem = lw.data(Qt.ItemDataRole.UserRole)
            p = Path(item.local_path or item.dest)
            try:
                if sys.platform == "win32":
                    if p.exists():
                        subprocess.Popen(["explorer", "/select,", str(p)])
                    elif p.parent.exists():
                        subprocess.Popen(["explorer", str(p.parent)])
                else:
                    subprocess.Popen(["xdg-open", str(p.parent)])
            except Exception as exc:
                log.warning("show_in_explorer failed: %s", exc)

    def _open_dgx_folders_selection(self) -> None:
        seen: set[str] = set()
        for lw in self.selectedItems():
            item: _DeliveredItem = lw.data(Qt.ItemDataRole.UserRole)
            folder = str(Path(item.dest).parent) if item.dest else ""
            if folder not in seen:
                seen.add(folder)
                self._rpc_open_folder(folder)

    def _copy_paths(self) -> None:
        paths = [
            lw.data(Qt.ItemDataRole.UserRole).dest
            or lw.data(Qt.ItemDataRole.UserRole).local_path
            for lw in self.selectedItems()
        ]
        QApplication.clipboard().setText("\n".join(p for p in paths if p))

    def _remove_selected(self) -> None:
        for lw in list(self.selectedItems()):
            idx = self.row(lw)
            if 0 <= idx < len(self._data):
                self._data.pop(idx)
            self.takeItem(self.row(lw))

    # ── Drag out  (Panel B only) ──────────────────────────────────────

    def startDrag(self, actions) -> None:
        urls = []
        for lw in self.selectedItems():
            item: _DeliveredItem = lw.data(Qt.ItemDataRole.UserRole)
            p = item.local_path or item.dest
            if p and Path(p).exists():
                urls.append(QUrl.fromLocalFile(p))
        if not urls:
            return
        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


# ──────────────────────────────────────────────────────────────────────────────
# Panel sub-header helper
# ──────────────────────────────────────────────────────────────────────────────

def _panel_header(arrow: str, title: str, subtitle: str) -> QWidget:
    w = QWidget()
    w.setFixedHeight(26)
    w.setStyleSheet(
        f"background: {BG_RAISED};"
        f"border-top: 1px solid {BORDER};"
        f"border-bottom: 1px solid {BORDER};"
    )
    l = QHBoxLayout(w)
    l.setContentsMargins(10, 0, 8, 0)
    l.setSpacing(5)

    la = QLabel(arrow)
    la.setStyleSheet(f"color: {ACCENT}; font-size: 12px; font-weight: 700;")
    la.setFixedWidth(14)
    l.addWidget(la)

    lt = QLabel(title)
    lt.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 11px; font-weight: 700;")
    l.addWidget(lt)

    ls = QLabel(subtitle)
    ls.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
    l.addWidget(ls)

    l.addStretch()
    return w


# ──────────────────────────────────────────────────────────────────────────────
# _SendToDGXPane  —  Panel A  (PC → DGX)
# ──────────────────────────────────────────────────────────────────────────────

class _SendToDGXPane(QWidget):
    """
    Top pane.  Drop PC files/folders here to convert & send to DGX.

    Displays a QStackedWidget:
        Page 0 — centred drop hint (shown when list is empty).
        Page 1 — _ResultsView of final converted+placed files.

    The entire pane acts as a drop target for local files (hasUrls).
    A subtle accent highlight appears while files are dragged over it.
    """

    files_dropped = pyqtSignal(list)   # list[str] of local absolute paths

    def __init__(self, conn_getter: Callable = None, parent=None):
        super().__init__(parent)
        self._conn_getter = conn_getter
        self.setAcceptDrops(True)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(_panel_header("↑", "SEND TO DGX", " — drop PC files here"))

        self._stack = QStackedWidget()

        # Page 0: hint
        hint = QLabel(
            "\n"
            "Drop PC files or folders here\n"
            "to convert and send to DGX.\n\n"
            "Converted results appear\n"
            "in this list when delivered."
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11px; padding: 16px;"
            f"background: {BG_DEEP};"
        )
        self._stack.addWidget(hint)   # index 0

        # Page 1: result list
        self._view = _ResultsView(side="dgx", conn_getter=self._conn_getter)
        self._stack.addWidget(self._view)  # index 1

        root.addWidget(self._stack, 1)

    # ── Public ────────────────────────────────────────────────────────

    def add_sent_file(self, name: str, dgx_dest: str) -> None:
        self._view.add_result(_DeliveredItem(name=name, dest=dgx_dest))
        self._stack.setCurrentIndex(1)

    def clear_all(self) -> None:
        self._view.clear_results()
        self._stack.setCurrentIndex(0)

    # ── Drop zone ─────────────────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            self.setStyleSheet(f"background: {ACCENT}18;")
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
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


# ──────────────────────────────────────────────────────────────────────────────
# _SendToPCPane  —  Panel B  (DGX → PC)
# ──────────────────────────────────────────────────────────────────────────────

class _SendToPCPane(QWidget):
    """
    Bottom pane.  Populated by add_received_file() calls.

    Shows files that have been fetched from DGX and delivered locally.
    Supports: extended selection, Show in Explorer, drag to any folder.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(_panel_header("↓", "SEND TO PC", " — from DGX"))

        self._stack = QStackedWidget()

        # Page 0: hint
        hint = QLabel(
            "\n"
            "Files downloaded from DGX\n"
            "(via 📂 Shared Drive) will\n"
            "appear here after delivery.\n\n"
            "Select items and drag them\n"
            "to any folder on your PC."
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 11px; padding: 16px;"
            f"background: {BG_DEEP};"
        )
        self._stack.addWidget(hint)    # index 0

        # Page 1: result list
        self._view = _ResultsView(side="pc")
        self._stack.addWidget(self._view)   # index 1

        root.addWidget(self._stack, 1)

    # ── Public ────────────────────────────────────────────────────────

    def add_received_file(self, name: str, local_path: str) -> None:
        self._view.add_result(_DeliveredItem(
            name=name,
            dest=local_path,
            local_path=local_path,
        ))
        self._stack.setCurrentIndex(1)

    def clear_all(self) -> None:
        self._view.clear_results()
        self._stack.setCurrentIndex(0)


# ──────────────────────────────────────────────────────────────────────────────
# TransferPanel  —  the public-facing sidebar widget
# ──────────────────────────────────────────────────────────────────────────────

class TransferPanel(QWidget):
    """
    Sidebar panel.  Wire up:

        panel.set_connection(conn)

        # PC → DGX (called by MainWindow on canvas file-drop)
        panel.enqueue_drop(paths, dgx_dest_dir)

        # DGX → PC (called by SharedDrivePanel after each download)
        panel.add_received_file(name, local_path)
    """

    def __init__(self, connection=None, parent=None):
        super().__init__(parent)
        self._conn:    Optional[object]      = connection
        self._session: TransferSession       = TransferSession()
        self._workers: list[TransferWorker]  = []
        self._pending: dict[str, tuple[int, int]] = {}  # item_id → (done, total)

        self.setMinimumWidth(260)
        self.setMaximumWidth(440)
        self._build_ui()

    def set_connection(self, conn) -> None:
        self._conn = conn

    def _get_conn(self):
        return self._conn

    # ── Build ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Title bar ────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setFixedHeight(36)
        hdr.setStyleSheet(
            f"background: {BG_RAISED}; border-bottom: 1px solid {BORDER};"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 0, 8, 0)
        hl.setSpacing(0)

        title_lbl = QLabel("Transfers")
        title_lbl.setStyleSheet(
            f"color: {TEXT_MAIN}; font-weight: 700; font-size: 13px;"
        )
        hl.addWidget(title_lbl)
        hl.addStretch()

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 10px; padding-right: 6px;"
        )
        hl.addWidget(self._status_lbl)

        btn_close = QPushButton("×")
        btn_close.setFlat(True)
        btn_close.setFixedSize(28, 28)
        btn_close.setStyleSheet(
            f"QPushButton {{ color: {TEXT_DIM}; font-size: 14px; background: transparent;"
            f"  border: none; border-radius: 4px; }}"
            f"QPushButton:hover {{ color: {TEXT_MAIN}; background: {BG_SURFACE}; }}"
        )
        btn_close.clicked.connect(self.hide)
        hl.addWidget(btn_close)
        root.addWidget(hdr)

        # ── Global 3 px progress bar ─────────────────────────────────
        self._bar = QProgressBar()
        self._bar.setFixedHeight(3)
        self._bar.setTextVisible(False)
        self._bar.setRange(0, 1000)
        self._bar.setValue(0)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {BG_RAISED}; border: none; margin: 0; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; }}"
        )
        self._bar.setVisible(False)
        root.addWidget(self._bar)

        # ── Panel A  (PC → DGX) ──────────────────────────────────────
        self._pane_a = _SendToDGXPane(conn_getter=self._get_conn)
        self._pane_a.files_dropped.connect(self._on_a_drop)
        root.addWidget(self._pane_a, 1)

        # ── Divider ───────────────────────────────────────────────────
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {BORDER}; border: none;")
        root.addWidget(div)

        # ── Panel B  (DGX → PC) ──────────────────────────────────────
        self._pane_b = _SendToPCPane()
        root.addWidget(self._pane_b, 1)

        # ── Footer ───────────────────────────────────────────────────
        ftr = QWidget()
        ftr.setFixedHeight(32)
        ftr.setStyleSheet(
            f"background: {BG_RAISED}; border-top: 1px solid {BORDER};"
        )
        fl = QHBoxLayout(ftr)
        fl.setContentsMargins(8, 0, 8, 0)
        fl.setSpacing(4)

        def _fb(text: str, tip: str = "") -> QPushButton:
            b = QPushButton(text)
            b.setFixedHeight(22)
            b.setStyleSheet(
                f"QPushButton {{ background: {BG_SURFACE}; color: {TEXT_DIM};"
                f"  border: 1px solid {BORDER}; border-radius: 4px;"
                f"  font-size: 10px; padding: 0 6px; }}"
                f"QPushButton:hover {{ color: {TEXT_MAIN}; border-color: {ACCENT}66; }}"
            )
            if tip:
                b.setToolTip(tip)
            return b

        btn_stage = _fb("📁 Stage", "Open local bridge-prep staging folder")
        btn_stage.clicked.connect(self._session.open_stage_dir)
        fl.addWidget(btn_stage)

        btn_log = _fb("🗒 Log", "Open session transfer log")
        btn_log.clicked.connect(self._session.open_log)
        fl.addWidget(btn_log)

        fl.addStretch()

        btn_clear = _fb("Clear", "Clear both result lists")
        btn_clear.clicked.connect(self._clear_all)
        fl.addWidget(btn_clear)

        root.addWidget(ftr)

    # ── Public API ────────────────────────────────────────────────────

    def enqueue_drop(self, paths: list[str], dgx_dest_dir: str = "") -> None:
        """
        Start a PC→DGX transfer job.
        Called by MainWindow when the user drops files onto the DGX canvas
        OR when Panel A receives a direct file-drop.
        """
        if not self._conn:
            log.warning("enqueue_drop: no active connection")
            return
        self._start_job(paths, dgx_dest_dir)

    def enqueue_paths(self, paths: list[str], dgx_dest: str = "") -> None:
        """Alias kept for backward-compat."""
        self.enqueue_drop(paths, dgx_dest)

    def add_received_file(self, name: str, local_path: str) -> None:
        """
        Populate Panel B with a file delivered from DGX to the PC.
        Call this from SharedDrivePanel (or any DGX-download path) after
        the file has been written to disk.
        """
        self._pane_b.add_received_file(name, local_path)

    # ── Job lifecycle ─────────────────────────────────────────────────

    def _on_a_drop(self, paths: list[str]) -> None:
        self.enqueue_drop(paths)

    def _start_job(self, paths: list[str], dgx_dest_dir: str) -> None:
        job = self._session.make_job(paths, dgx_dest_dir)
        if not job.items:
            log.warning("_start_job: no transferable items in drop")
            return

        worker = TransferWorker(
            job, self._conn, self._session,
            auto_place=True,   # convert → send → place at final DGX dest
        )
        worker.item_progress.connect(self._on_item_progress)
        worker.item_status.connect(self._on_item_status)
        worker.item_sent.connect(self._on_item_sent)
        worker.job_complete.connect(self._on_job_complete)
        self._workers.append(worker)
        worker.start()

        self._bar.setVisible(True)
        self._status_lbl.setText("Processing…")
        log.info("Started job %s: %d item(s) → %s",
                 job.job_id, len(job.items), dgx_dest_dir or "~/Desktop")

    # ── Worker signal handlers ────────────────────────────────────────

    def _on_item_progress(self, item_id: str, done: int, total: int) -> None:
        self._pending[item_id] = (done, total)
        total_b = sum(t for _, t in self._pending.values())
        done_b  = sum(d for d, _ in self._pending.values())
        if total_b > 0:
            self._bar.setVisible(True)
            self._bar.setValue(int(done_b * 1000 / total_b))

    def _on_item_status(self, item_id: str, status: str, msg: str) -> None:
        if status in ("done", "bridge", "failed"):
            self._pending.pop(item_id, None)
            self._maybe_hide_bar()

    @pyqtSlot(str, str, str)
    def _on_item_sent(self, item_id: str, dgx_name: str, dgx_path: str) -> None:
        """Worker reports a file successfully placed on DGX."""
        self._pane_a.add_sent_file(dgx_name, dgx_path)

    @pyqtSlot(str, int, int)
    def _on_job_complete(self, job_id: str, ok: int, fail: int) -> None:
        log.info("Job %s complete: %d ok, %d failed", job_id, ok, fail)
        self._workers = [w for w in self._workers if w.isRunning()]
        self._maybe_hide_bar()

    def _maybe_hide_bar(self) -> None:
        if not self._pending and not any(w.isRunning() for w in self._workers):
            self._bar.setVisible(False)
            self._bar.setValue(0)
            self._status_lbl.setText("")

    def _clear_all(self) -> None:
        self._pane_a.clear_all()
        self._pane_b.clear_all()
