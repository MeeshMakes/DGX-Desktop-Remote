"""
pc-application/src/transfer/transfer_panel.py
File transfer sidebar UI — drag-drop zone, queue, remote browser.
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QTabWidget, QFrame, QComboBox,
    QProgressBar, QScrollArea, QSizePolicy, QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QFont, QDragEnterEvent, QDropEvent

from theme import (
    ACCENT, SUCCESS, ERROR, WARNING, TEXT_DIM, TEXT_MAIN,
    BG_RAISED, BG_SURFACE, BG_BASE, BORDER, BG_DEEP
)
from widgets import SectionTitle, HDivider
from .transfer_worker import TransferWorker, TransferItem


# ──────────────────────────────────────────────────────────────────────
# Drop zone widget
# ──────────────────────────────────────────────────────────────────────

class FileDropZone(QFrame):
    files_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFixedHeight(70)
        self.setStyleSheet(
            f"QFrame {{ border: 2px dashed {ACCENT}44;"
            f"border-radius: 8px; background: {BG_SURFACE}; }}"
            f"QFrame:hover {{ border-color: {ACCENT}; background: {ACCENT}11; }}"
        )
        l = QVBoxLayout(self)
        l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("📂")
        icon.setStyleSheet("font-size: 18px; border: none;")
        lbl  = QLabel("Drop files here or click to browse")
        lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px; border: none;")
        l.addWidget(icon, alignment=Qt.AlignmentFlag.AlignCenter)
        l.addWidget(lbl,  alignment=Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, ev):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select Files")
        if paths:
            self.files_dropped.emit(paths)

    def dragEnterEvent(self, ev: QDragEnterEvent):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            self.setStyleSheet(
                f"QFrame {{ border: 2px dashed {ACCENT}; border-radius: 8px;"
                f"background: {ACCENT}22; }}"
            )

    def dragLeaveEvent(self, ev):
        self.setStyleSheet(
            f"QFrame {{ border: 2px dashed {ACCENT}44; border-radius: 8px;"
            f"background: {BG_SURFACE}; }}"
            f"QFrame:hover {{ border-color: {ACCENT}; background: {ACCENT}11; }}"
        )

    def dropEvent(self, ev: QDropEvent):
        self.dragLeaveEvent(ev)
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)


# ──────────────────────────────────────────────────────────────────────
# Remote file row
# ──────────────────────────────────────────────────────────────────────

class _RemoteFileItem(QWidget):
    def __init__(self, name: str, size_str: str, parent=None):
        super().__init__(parent)
        l = QHBoxLayout(self)
        l.setContentsMargins(4, 2, 4, 2)
        ico = QLabel("📄")
        ico.setFixedWidth(20)
        nm  = QLabel(name)
        nm.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 12px;")
        sz  = QLabel(size_str)
        sz.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        sz.setFixedWidth(70)
        sz.setAlignment(Qt.AlignmentFlag.AlignRight)
        l.addWidget(ico)
        l.addWidget(nm)
        l.addStretch()
        l.addWidget(sz)


# ──────────────────────────────────────────────────────────────────────
# Remote file fetch thread
# ──────────────────────────────────────────────────────────────────────

class _ListFilesThread(QThread):
    from PyQt6.QtCore import pyqtSignal as _sig
    result = _sig(dict)

    def __init__(self, conn, folder: str):
        super().__init__()
        self._conn   = conn
        self._folder = folder

    def run(self):
        try:
            r = self._conn.rpc({"type": "list_files", "folder": self._folder}, timeout=6)
            self.result.emit(r)
        except Exception as e:
            self.result.emit({"ok": False, "error": str(e)})


# ──────────────────────────────────────────────────────────────────────
# Transfer panel
# ──────────────────────────────────────────────────────────────────────

class TransferPanel(QWidget):
    """Sidebar panel for file transfer operations."""

    def __init__(self, connection=None, parent=None):
        super().__init__(parent)
        self._conn   = connection
        self._worker: TransferWorker | None = None
        self._queue: list[TransferItem] = []
        self.setMinimumWidth(260)
        self.setMaximumWidth(340)
        self._build_ui()

    def set_connection(self, conn):
        self._conn = conn
        self._btn_refresh.setEnabled(conn is not None)
        self._btn_send.setEnabled(conn is not None)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        l = QVBoxLayout(self)
        l.setContentsMargins(0, 0, 0, 0)
        l.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(44)
        hdr.setStyleSheet(f"background: {BG_RAISED}; border-bottom: 1px solid {BORDER};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 0, 8, 0)
        hl.addWidget(QLabel("File Transfer", styleSheet=f"font-weight: 700; color: {TEXT_MAIN};"))
        hl.addStretch()
        btn_close = QPushButton("✕")
        btn_close.setFlat(True)
        btn_close.setStyleSheet(f"color: {TEXT_DIM}; font-size: 14px;")
        btn_close.clicked.connect(lambda: self.hide())
        btn_close.setFixedSize(28, 28)
        hl.addWidget(btn_close)
        l.addWidget(hdr)

        # Tabs
        self._tabs = QTabWidget()
        self._tabs.addTab(self._upload_tab(),  "⬆  Upload")
        self._tabs.addTab(self._dgx_tab(),     "🗂  DGX Files")
        l.addWidget(self._tabs)

        # Overall progress bar
        self._ovr_bar = QProgressBar()
        self._ovr_bar.setFixedHeight(4)
        self._ovr_bar.setTextVisible(False)
        self._ovr_bar.setRange(0, 100)
        self._ovr_bar.setValue(0)
        self._ovr_bar.hide()
        l.addWidget(self._ovr_bar)

        # Status label
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 10px; padding: 2px 8px;"
        )
        l.addWidget(self._status_lbl)

    def _upload_tab(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(10, 10, 10, 10)
        l.setSpacing(10)

        # Drop zone
        self._drop_zone = FileDropZone()
        self._drop_zone.files_dropped.connect(self.enqueue_paths)
        l.addWidget(self._drop_zone)

        # Destination row
        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Destination:", styleSheet=f"color: {TEXT_DIM}; font-size: 11px;"))
        self._dest_combo = QComboBox()
        self._dest_combo.addItems(["inbox", "staging", "outbox"])
        dest_row.addWidget(self._dest_combo)
        l.addLayout(dest_row)

        # Queue list
        l.addWidget(SectionTitle("Queue"))
        self._queue_list = QListWidget()
        self._queue_list.setStyleSheet(
            f"QListWidget {{ background: {BG_DEEP}; border: 1px solid {BORDER}; border-radius: 6px; }}"
            f"QListWidget::item {{ padding: 4px 8px; color: {TEXT_MAIN}; font-size: 11px; }}"
            f"QListWidget::item:selected {{ background: {ACCENT}33; }}"
        )
        l.addWidget(self._queue_list)

        # Buttons
        btn_row = QHBoxLayout()
        self._btn_clear = QPushButton("Clear")
        self._btn_clear.setFixedHeight(28)
        self._btn_clear.clicked.connect(self._clear_queue)
        self._btn_send = QPushButton("Send All")
        self._btn_send.setProperty("class", "primary")
        self._btn_send.setFixedHeight(28)
        self._btn_send.clicked.connect(self._send_all)
        self._btn_send.setEnabled(False)
        btn_row.addWidget(self._btn_clear)
        btn_row.addWidget(self._btn_send)
        l.addLayout(btn_row)

        return w

    def _dgx_tab(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(10, 10, 10, 10)
        l.setSpacing(8)

        # Folder selector + refresh
        top = QHBoxLayout()
        top.addWidget(QLabel("Folder:", styleSheet=f"color: {TEXT_DIM}; font-size: 11px;"))
        self._folder_combo = QComboBox()
        self._folder_combo.addItems(["inbox", "outbox", "staging", "archive"])
        self._folder_combo.currentTextChanged.connect(self._refresh_remote)
        top.addWidget(self._folder_combo)
        self._btn_refresh = QPushButton("↺")
        self._btn_refresh.setFixedSize(28, 28)
        self._btn_refresh.clicked.connect(self._refresh_remote)
        self._btn_refresh.setEnabled(False)
        top.addWidget(self._btn_refresh)
        l.addLayout(top)

        # Remote file list
        self._remote_list = QListWidget()
        self._remote_list.setStyleSheet(
            f"QListWidget {{ background: {BG_DEEP}; border: 1px solid {BORDER}; border-radius: 6px; }}"
            f"QListWidget::item {{ padding: 4px 8px; color: {TEXT_MAIN}; font-size: 11px; }}"
            f"QListWidget::item:selected {{ background: {ACCENT}33; }}"
        )
        self._remote_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._remote_list.customContextMenuRequested.connect(self._remote_context_menu)
        l.addWidget(self._remote_list)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_dl  = QPushButton("⬇ Download")
        btn_dl.setFixedHeight(28)
        btn_dl.clicked.connect(self._download_selected)
        btn_del = QPushButton("🗑 Delete")
        btn_del.setFixedHeight(28)
        btn_del.setProperty("class", "danger")
        btn_del.clicked.connect(self._delete_selected)
        btn_row.addWidget(btn_dl)
        btn_row.addWidget(btn_del)
        l.addLayout(btn_row)

        return w

    # ------------------------------------------------------------------
    # Upload logic
    # ------------------------------------------------------------------

    def enqueue_paths(self, paths: list[str]):
        """Called from MainWindow when files are dropped or selected."""
        folder = self._dest_combo.currentText()
        new_items = []
        for p in paths:
            path = Path(p)
            if not path.is_file():
                continue
            item = TransferItem(local_path=path, remote_folder=folder)
            self._queue.append(item)
            lw_item = QListWidgetItem(f"📄  {path.name}")
            lw_item.setData(Qt.ItemDataRole.UserRole, str(path))
            self._queue_list.addItem(lw_item)
            new_items.append(item)
        if new_items and self._conn:
            self._btn_send.setEnabled(True)

    def _clear_queue(self):
        self._queue.clear()
        self._queue_list.clear()
        self._btn_send.setEnabled(False)

    def _send_all(self):
        if not self._queue or not self._conn:
            return
        if self._worker and self._worker.isRunning():
            return

        self._btn_send.setEnabled(False)
        self._ovr_bar.show()
        self._ovr_bar.setValue(0)

        self._worker = TransferWorker(list(self._queue), self._conn)
        self._worker.progress.connect(self._on_item_progress)
        self._worker.item_complete.connect(self._on_item_complete)
        self._worker.overall_progress.connect(self._on_overall_progress)
        self._worker.batch_complete.connect(self._on_batch_complete)
        self._worker.status_msg.connect(self._status_lbl.setText)
        self._worker.start()

    def _on_item_progress(self, item_id: str, done: int, total: int):
        if total > 0:
            pct = int(done / total * 100)
            # update the queue list item label
            for i in range(self._queue_list.count()):
                w = self._queue_list.item(i)
                if w and w.data(Qt.ItemDataRole.UserRole) == item_id:
                    name = Path(item_id).name
                    w.setText(f"📤  {name}  {pct}%")
                    break

    def _on_item_complete(self, item_id: str, ok: bool, msg: str):
        for i in range(self._queue_list.count()):
            w = self._queue_list.item(i)
            if w and w.data(Qt.ItemDataRole.UserRole) == item_id:
                name = Path(item_id).name
                if ok:
                    w.setText(f"✅  {name}")
                    w.setForeground(__import__('PyQt6.QtGui', fromlist=['QColor']).QColor(SUCCESS))
                else:
                    w.setText(f"❌  {name}  — {msg}")
                    w.setForeground(__import__('PyQt6.QtGui', fromlist=['QColor']).QColor(ERROR))
                break

    def _on_overall_progress(self, done: int, total: int):
        if total > 0:
            self._ovr_bar.setValue(int(done / total * 100))

    def _on_batch_complete(self):
        self._ovr_bar.setValue(100)
        self._status_lbl.setText("All transfers complete  ✓")
        QTimer.singleShot(4000, lambda: self._status_lbl.setText(""))
        QTimer.singleShot(4000, self._ovr_bar.hide)
        self._queue.clear()

    # ------------------------------------------------------------------
    # Remote browser
    # ------------------------------------------------------------------

    def _refresh_remote(self):
        if not self._conn:
            return
        folder = self._folder_combo.currentText()
        self._remote_list.clear()
        self._status_lbl.setText(f"Loading {folder}/…")
        t = _ListFilesThread(self._conn, folder)
        t.result.connect(self._on_file_list)
        t.start()

    def _on_file_list(self, data: dict):
        self._status_lbl.setText("")
        self._remote_list.clear()
        if not data.get("ok"):
            self._remote_list.addItem(f"Error: {data.get('error', '?')}")
            return
        for f in data.get("files", []):
            item = QListWidgetItem(f"📄  {f['name']}  ({f.get('size_human','?')})")
            item.setData(Qt.ItemDataRole.UserRole, f["name"])
            self._remote_list.addItem(item)

    def _remote_context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        item = self._remote_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.addAction("⬇  Download", self._download_selected)
        menu.addAction("🗑  Delete",  self._delete_selected)
        menu.exec(self._remote_list.mapToGlobal(pos))

    def _download_selected(self):
        item = self._remote_list.currentItem()
        if not item or not self._conn:
            return
        filename = item.data(Qt.ItemDataRole.UserRole)
        folder   = self._folder_combo.currentText()
        dest_dir = QFileDialog.getExistingDirectory(self, "Save to folder")
        if not dest_dir:
            return
        self._status_lbl.setText(f"Downloading {filename} …")

        def _done(result: dict):
            if result.get("ok"):
                self._status_lbl.setText(f"Downloaded  {filename}  ✓")
            else:
                self._status_lbl.setText(f"Download failed: {result.get('error', '?')}")

        class _DLThread(QThread):
            finished_dl = __import__('PyQt6.QtCore', fromlist=['pyqtSignal']).pyqtSignal(dict)
            def __init__(s, c, fn, fo, dd):
                super().__init__()
                s._c, s._fn, s._fo, s._dd = c, fn, fo, dd
            def run(s):
                try:
                    r = s._c.get_file(s._fn, s._fo, Path(s._dd))
                    s.finished_dl.emit(r)
                except Exception as e:
                    s.finished_dl.emit({"ok": False, "error": str(e)})

        t = _DLThread(self._conn, filename, folder, dest_dir)
        t.finished_dl.connect(_done)
        t.start()

    def _delete_selected(self):
        item = self._remote_list.currentItem()
        if not item or not self._conn:
            return
        filename = item.data(Qt.ItemDataRole.UserRole)
        if QMessageBox.question(
            self, "Delete Remote File",
            f"Delete  {filename}  from DGX?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            r = self._conn.rpc({
                "type": "delete_file",
                "folder": self._folder_combo.currentText(),
                "filename": filename,
            })
            if r.get("ok"):
                self._refresh_remote()
            else:
                QMessageBox.warning(self, "Delete Error", r.get("error", "Unknown error"))
        except Exception as e:
            QMessageBox.warning(self, "Delete Error", str(e))
