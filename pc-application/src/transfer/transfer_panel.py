"""
pc-application/src/transfer/transfer_panel.py

Transfer Queue Panel - sidebar transfer UI.

Layout
  +----------------------------------+
  |  Transfers                  [x]  |  <- header
  |AAAAAAAAAAAAAAAAAAAAAA            |  <- 3px global progress bar (hidden when idle)
  +----------------------------------+
  |  scroll area with job rows        |
  |  +----------------------------+  |
  |  | [icon] 2 files -> Desktop  |  |  <- job header (status text, no bar)
  |  |   [icon] build.sh  staged  |  |  <- file row: icon + name + status text
  |  |   [icon] setup.sh  done    |  |
  |  | +---DGX---+-Local----------+ |  |  <- tabs: real file viewer, Ctrl+scroll
  |  | | [thumb] build.sh        | |  |
  |  | | [icon]  setup.sh        | |  |
  |  | +--------+----------------+ |  |
  |  +----------------------------+  |
  +----------------------------------+
  |  Staging   Log   [Clear done]    |  <- footer
  +----------------------------------+

DGX tab  = ~/BridgeStaging/<session>/ on the DGX  (thumbnails via RPC)
Local tab = bridge-prep/<session>/ on the PC       (Windows shell icons/thumbs)

Ctrl+scroll inside either file view cycles icon sizes:
  32px = list mode (icon + name)   ->  48 / 72 / 96 / 128px grid (with thumbnails)
"""

import base64
import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QThread, QSize, QFileInfo, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWidgets import QListView

from theme import (
    ACCENT, BG_BASE, BG_DEEP, BG_RAISED, BG_SURFACE,
    BORDER, ERROR, SUCCESS, TEXT_DIM, TEXT_MAIN, WARNING,
)
from .transfer_session import TransferItem, TransferJob, TransferSession
from .transfer_worker import TransferWorker

log = logging.getLogger("pc.transfer.panel")


# -----------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"}
_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv"}

# Fallback emoji icons used when platform icons are unavailable (DGX tab)
_EXT_EMOJI = {
    ".py":   "\U0001f40d",  # snake
    ".sh":   "\u26a1",      # lightning
    ".bash": "\u26a1",
    ".c":    "\U0001f527",
    ".cpp":  "\U0001f527",
    ".h":    "\U0001f527",
    ".js":   "\U0001f7e8",
    ".ts":   "\U0001f7e6",
    ".json": "\U0001f4cb",
    ".html": "\U0001f310",
    ".xml":  "\U0001f4cb",
    ".yaml": "\U0001f4cb",
    ".toml": "\U0001f4cb",
    ".jpg":  "\U0001f5bc",
    ".jpeg": "\U0001f5bc",
    ".png":  "\U0001f5bc",
    ".gif":  "\U0001f5bc",
    ".bmp":  "\U0001f5bc",
    ".svg":  "\U0001f5bc",
    ".pdf":  "\U0001f4d5",
    ".md":   "\U0001f4dd",
    ".txt":  "\U0001f4dd",
    ".zip":  "\U0001f4e6",
    ".tar":  "\U0001f4e6",
    ".gz":   "\U0001f4e6",
    ".mp4":  "\U0001f3ac",
    ".mp3":  "\U0001f3b5",
    ".wav":  "\U0001f3b5",
}


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# -----------------------------------------------------------------------
# Background threads
# -----------------------------------------------------------------------

class _ListBridgeStagingThread(QThread):
    result = pyqtSignal(list)   # list[dict]

    def __init__(self, conn, session_id: str, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._session_id = session_id

    def run(self):
        try:
            r = self._conn.rpc(
                {"type": "list_bridge_staging", "session_id": self._session_id},
                timeout=8,
            )
            self.result.emit(r.get("files", []) if r.get("ok") else [])
        except Exception as exc:
            log.warning("list_bridge_staging failed: %s", exc)
            self.result.emit([])


class _GetThumbnailThread(QThread):
    """Fetch a single file thumbnail from the DGX and return raw PNG bytes."""
    got_thumb = pyqtSignal(str, bytes)  # filename, png_bytes

    def __init__(self, conn, session_id: str, filename: str, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._session_id = session_id
        self._filename = filename

    def run(self):
        try:
            r = self._conn.rpc(
                {
                    "type": "get_thumbnail",
                    "session_id": self._session_id,
                    "filename": self._filename,
                },
                timeout=10,
            )
            if r.get("ok") and r.get("data"):
                self.got_thumb.emit(
                    self._filename, base64.b64decode(r["data"])
                )
        except Exception as exc:
            log.debug("get_thumbnail failed for %s: %s", self._filename, exc)


# -----------------------------------------------------------------------
# _FileView — list/grid icon view with Ctrl+scroll zoom and real thumbnails
# -----------------------------------------------------------------------

class _FileView(QListWidget):
    """
    A QListWidget configured as a proper file browser.

    * Ctrl+scroll cycles icon sizes: 32 (list), 48, 72, 96, 128 (grid)
    * In list mode (32 px): one item per line, Windows shell icon + name + size
    * In grid mode (>= 48 px): icon grid; real thumbnails loaded for images/videos
    * Fully draggable (DragOnly) so items can be dropped on the DGX canvas
    """

    ICON_SIZES = [32, 48, 72, 96, 128]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._size_idx = 0
        self._thumb_threads: list[QThread] = []
        self._icon_provider: Optional[QFileIconProvider] = None
        try:
            self._icon_provider = QFileIconProvider()
        except Exception:
            pass

        self._apply_view()
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(False)
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(
            f"QListWidget {{ background: {BG_BASE}; border: none; outline: none;"
            f"  color: {TEXT_MAIN}; font-size: 11px; }}"
            f"QListWidget::item {{ padding: 2px 2px; }}"
            f"QListWidget::item:selected {{ background: {ACCENT}33; color: {TEXT_MAIN}; }}"
            f"QListWidget::item:hover {{ background: {BG_SURFACE}; }}"
        )

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self._size_idx = min(self._size_idx + 1, len(self.ICON_SIZES) - 1)
            else:
                self._size_idx = max(self._size_idx - 1, 0)
            self._apply_view()
            event.accept()
        else:
            super().wheelEvent(event)

    def _apply_view(self):
        sz = self.ICON_SIZES[self._size_idx]
        self.setIconSize(QSize(sz, sz))
        if sz <= 36:
            self.setViewMode(QListView.ViewMode.ListMode)
            self.setGridSize(QSize())
            self.setWordWrap(False)
            self.setSpacing(1)
        else:
            self.setViewMode(QListView.ViewMode.IconMode)
            self.setGridSize(QSize(sz + 20, sz + 34))
            self.setWordWrap(True)
            self.setSpacing(4)

    # ---- Populate methods -------------------------------------------

    def load_local(self, folder: Path):
        """
        Populate from a real local directory.
        Uses QFileIconProvider for Windows shell icons.
        Loads actual image thumbnails when in grid mode.
        """
        self.clear()
        # cancel old thumb threads
        for t in self._thumb_threads:
            t.quit()
        self._thumb_threads.clear()

        if not folder.exists():
            self.addItem(QListWidgetItem("  (folder not found)"))
            return
        try:
            entries = sorted(
                folder.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError as exc:
            self.addItem(QListWidgetItem(f"  (error: {exc})"))
            return

        sz = self.ICON_SIZES[self._size_idx]
        for f in entries:
            item = QListWidgetItem()
            if f.is_file():
                size_str = _human_size(f.stat().st_size)
            else:
                size_str = ""
            if sz <= 36:
                item.setText(f"{f.name}  {size_str}".rstrip())
            else:
                item.setText(f"{f.name}\n{size_str}".rstrip("\n"))
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            item.setToolTip(str(f))

            ext = f.suffix.lower()
            if sz > 36 and ext in _IMAGE_EXTS and f.is_file():
                # Real image thumbnail
                px = QPixmap(str(f))
                if not px.isNull():
                    px = px.scaled(
                        sz, sz,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    item.setIcon(QIcon(px))
                elif self._icon_provider:
                    item.setIcon(self._icon_provider.icon(QFileInfo(str(f))))
            elif self._icon_provider:
                if f.is_dir():
                    item.setIcon(
                        self._icon_provider.icon(QFileIconProvider.IconType.Folder)
                    )
                else:
                    item.setIcon(self._icon_provider.icon(QFileInfo(str(f))))

            self.addItem(item)

        if self.count() == 0:
            self.addItem(QListWidgetItem("  (empty)"))

    def load_dgx(self, files: list[dict], conn, session_id: str):
        """
        Populate from a DGX file list (name, size_human).
        Fetches image/video thumbnails asynchronously via RPC.
        """
        self.clear()
        for t in self._thumb_threads:
            t.quit()
        self._thumb_threads.clear()

        sz = self.ICON_SIZES[self._size_idx]
        for info in files:
            name = info.get("name", "?")
            size_str = info.get("size_human", "")
            item = QListWidgetItem()
            if sz <= 36:
                item.setText(f"{name}  {size_str}".rstrip())
            else:
                item.setText(f"{name}\n{size_str}".rstrip("\n"))
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(name)

            # For image/video in grid mode, request thumbnail from DGX
            ext = Path(name).suffix.lower()
            if sz > 36 and conn and (ext in _IMAGE_EXTS or ext in _VIDEO_EXTS):
                t = _GetThumbnailThread(conn, session_id, name, parent=self)
                t.got_thumb.connect(
                    lambda fn, data, it=item: self._apply_thumb(it, data)
                )
                t.start()
                self._thumb_threads.append(t)

            self.addItem(item)

        if self.count() == 0:
            self.addItem(QListWidgetItem("  (empty)"))

    def _apply_thumb(self, item: QListWidgetItem, png_bytes: bytes):
        try:
            _ = self.count()  # raises if widget deleted
        except RuntimeError:
            return
        px = QPixmap()
        px.loadFromData(png_bytes)
        if not px.isNull():
            sz = self.ICON_SIZES[self._size_idx]
            px = px.scaled(
                sz, sz,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            item.setIcon(QIcon(px))


# -----------------------------------------------------------------------
# Single file row inside a job transfer (no per-file progress bar)
# -----------------------------------------------------------------------

class _FileRow(QWidget):
    retry_requested = pyqtSignal(str)   # item_id

    _STATUS_STYLE = {
        "queued":    f"color: {TEXT_DIM};",
        "running":   f"color: {ACCENT};",
        "verifying": f"color: {WARNING};",
        "done":      f"color: {SUCCESS};",
        "bridge":    f"color: {ACCENT};",
        "failed":    f"color: {ERROR};",
    }
    _STATUS_ICON = {
        "queued":    "\u23f3",
        "running":   "\u2b06",
        "verifying": "\U0001f50d",
        "done":      "\u2705",
        "bridge":    "\U0001f3af",
        "failed":    "\u274c",
    }

    def __init__(self, item: TransferItem, parent=None):
        super().__init__(parent)
        self._item = item
        self.setFixedHeight(24)

        l = QHBoxLayout(self)
        l.setContentsMargins(20, 0, 8, 0)
        l.setSpacing(6)

        self._icon = QLabel(self._STATUS_ICON["queued"])
        self._icon.setFixedWidth(16)
        self._icon.setStyleSheet("font-size: 11px;")
        l.addWidget(self._icon)

        display_name = (
            item.dgx_name
            if item.dgx_name and item.dgx_name != item.local_path.name
            else item.local_path.name
        )
        self._name = QLabel(display_name)
        self._name.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 11px;")
        self._name.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        l.addWidget(self._name, 1)

        self._msg = QLabel("")
        self._msg.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
        self._msg.setFixedWidth(70)
        self._msg.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        l.addWidget(self._msg)

        self._btn_retry = QPushButton("\u21ba")
        self._btn_retry.setFixedSize(20, 20)
        self._btn_retry.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {ACCENT};"
            f"font-size: 12px; border: none; }}"
        )
        self._btn_retry.hide()
        self._btn_retry.clicked.connect(lambda: self.retry_requested.emit(item.item_id))
        l.addWidget(self._btn_retry)

    def update_progress(self, done: int, total: int):
        if total > 0:
            pct = int(done / total * 100)
            self._msg.setText(f"{pct}%")

    def update_status(self, status: str, msg: str = ""):
        self._icon.setText(self._STATUS_ICON.get(status, self._STATUS_ICON["queued"]))
        self._icon.setStyleSheet(
            "font-size: 11px; " + self._STATUS_STYLE.get(status, "")
        )
        if status == "bridge":
            self._msg.setText("staged")
            self._msg.setStyleSheet(f"color: {ACCENT}; font-size: 10px;")
        elif status == "verifying":
            self._msg.setText("verifying...")
            self._msg.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
        elif status == "done":
            self._msg.setText("done")
            self._msg.setStyleSheet(f"color: {SUCCESS}; font-size: 10px;")
        elif status == "failed":
            short = (msg[:18] + "...") if len(msg) > 18 else msg
            self._msg.setText(short or "error")
            self._msg.setStyleSheet(f"color: {ERROR}; font-size: 10px;")
            self._msg.setToolTip(msg)
            self._btn_retry.show()
        elif status == "queued":
            self._msg.setText("queued")
            self._msg.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")


# -----------------------------------------------------------------------
# _FolderView — two-tab file navigator (DGX staging / Local prep)
#
#  Each tab has:
#   - path label
#   - "Open folder" button  (DGX: opens Nautilus on DGX; Local: opens Explorer)
#   - Refresh button
#   - _FileView (list/grid, Ctrl+scroll, thumbnails)
# -----------------------------------------------------------------------

def _tb_btn_factory(text: str, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setFixedSize(22, 22)
    b.setStyleSheet(
        f"QPushButton {{ background: transparent; color: {TEXT_DIM};"
        f"  font-size: 12px; border: none; border-radius: 3px; }}"
        f"QPushButton:hover {{ color: {TEXT_MAIN}; background: {BG_SURFACE}; }}"
    )
    if tip:
        b.setToolTip(tip)
    return b


class _FolderView(QWidget):
    """
    Shown inside a _JobRow once files reach the DGX bridge staging area.

    DGX tab  – lists ~/BridgeStaging/<session>/ (with thumbnail RPC).
    Local tab – lists bridge-prep/<session>/ from disk (Windows icons/thumbs).

    Open buttons restore the ability to jump straight to each folder.
    """

    def __init__(self, session_id: str, local_path: Path, parent=None):
        super().__init__(parent)
        self._session_id = session_id
        self._local_path = local_path
        self._conn: Optional[object] = None
        self._list_thread: Optional[_ListBridgeStagingThread] = None
        self._build_ui()
        self.setVisible(False)

    # ---- Build UI ---------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: 1px solid {BORDER}; background: {BG_BASE}; }}"
            f"QTabBar::tab {{ padding: 4px 12px; font-size: 10px;"
            f"  color: {TEXT_DIM}; background: {BG_RAISED};"
            f"  border: 1px solid {BORDER}; border-bottom: none;"
            f"  border-radius: 3px 3px 0 0; margin-right: 2px; }}"
            f"QTabBar::tab:selected {{ color: {TEXT_MAIN}; background: {ACCENT}22;"
            f"  border-bottom: 1px solid {ACCENT}; }}"
        )

        # ---- DGX tab ------------------------------------------------
        dgx_w = QWidget()
        dv = QVBoxLayout(dgx_w)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(2)

        dgx_tb = QWidget()
        dgx_tb.setFixedHeight(24)
        dtbl = QHBoxLayout(dgx_tb)
        dtbl.setContentsMargins(4, 0, 4, 0)
        dtbl.setSpacing(4)

        lbl_d = QLabel("~/BridgeStaging/")
        lbl_d.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9px;")
        dtbl.addWidget(lbl_d)
        dtbl.addStretch()

        hint_d = QLabel("Ctrl+\u2195 zoom")
        hint_d.setStyleSheet(f"color: {TEXT_DIM}; font-size: 8px;")
        dtbl.addWidget(hint_d)

        self._btn_open_dgx = _tb_btn_factory("\U0001f4c2", "Open in DGX file manager")
        self._btn_open_dgx.clicked.connect(self._open_dgx_folder)
        dtbl.addWidget(self._btn_open_dgx)

        btn_ref_dgx = _tb_btn_factory("\u27f3", "Refresh DGX listing")
        btn_ref_dgx.clicked.connect(self._refresh_dgx)
        dtbl.addWidget(btn_ref_dgx)

        dv.addWidget(dgx_tb)

        self._dgx_view = _FileView()
        self._dgx_view.setMinimumHeight(130)
        self._dgx_view.setMaximumHeight(260)
        dv.addWidget(self._dgx_view)

        tabs.addTab(dgx_w, "\U0001f4e1  DGX")

        # ---- Local tab ----------------------------------------------
        local_w = QWidget()
        lv = QVBoxLayout(local_w)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(2)

        local_tb = QWidget()
        local_tb.setFixedHeight(24)
        ltbl = QHBoxLayout(local_tb)
        ltbl.setContentsMargins(4, 0, 4, 0)
        ltbl.setSpacing(4)

        lbl_l = QLabel(str(self._local_path))
        lbl_l.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9px;")
        lbl_l.setToolTip(str(self._local_path))
        ltbl.addWidget(lbl_l)
        ltbl.addStretch()

        hint_l = QLabel("Ctrl+\u2195 zoom")
        hint_l.setStyleSheet(f"color: {TEXT_DIM}; font-size: 8px;")
        ltbl.addWidget(hint_l)

        self._btn_open_local = _tb_btn_factory("\U0001f4c2", "Open in Windows Explorer")
        self._btn_open_local.clicked.connect(self._open_local_folder)
        ltbl.addWidget(self._btn_open_local)

        btn_ref_loc = _tb_btn_factory("\u27f3", "Refresh local listing")
        btn_ref_loc.clicked.connect(self._refresh_local)
        ltbl.addWidget(btn_ref_loc)

        lv.addWidget(local_tb)

        self._local_view = _FileView()
        self._local_view.setMinimumHeight(130)
        self._local_view.setMaximumHeight(260)
        lv.addWidget(self._local_view)

        tabs.addTab(local_w, "\U0001f4bb  Local")

        root.addWidget(tabs)

    # ---- Public methods ---------------------------------------------

    def activate(self, conn):
        """Show the panel and populate both file lists."""
        self._conn = conn
        self.setVisible(True)
        self._refresh_local()
        self._refresh_dgx()

    # ---- Open folder actions ----------------------------------------

    def _open_dgx_folder(self):
        if not self._conn:
            return
        try:
            self._conn.rpc(
                {"type": "open_bridge_folder", "session_id": self._session_id},
                timeout=5,
            )
        except Exception as exc:
            log.warning("open_bridge_folder RPC failed: %s", exc)

    def _open_local_folder(self):
        folder = self._local_path
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            log.warning("open local folder failed: %s", exc)

    # ---- Refresh ----------------------------------------------------

    def _refresh_local(self):
        self._local_view.load_local(self._local_path)

    def _refresh_dgx(self):
        if not self._conn:
            return
        self._dgx_view.clear()
        self._dgx_view.addItem(QListWidgetItem("  loading..."))
        self._list_thread = _ListBridgeStagingThread(
            self._conn, self._session_id, parent=self
        )
        self._list_thread.result.connect(self._on_dgx_list)
        self._list_thread.start()

    @pyqtSlot(list)
    def _on_dgx_list(self, files: list):
        try:
            self._dgx_view.load_dgx(files, self._conn, self._session_id)
        except RuntimeError:
            pass


# -----------------------------------------------------------------------
# Job row (one per drop)
# -----------------------------------------------------------------------

class _JobRow(QWidget):
    def __init__(self, job: TransferJob, session: TransferSession, parent=None):
        super().__init__(parent)
        self._job = job
        self._session = session
        self._file_rows: dict[str, _FileRow] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(2)

        # Header row
        hdr = QWidget()
        hdr.setFixedHeight(28)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.setSpacing(6)

        icon = QLabel("\U0001f4e6")
        icon.setFixedWidth(18)
        hl.addWidget(icon)

        count = len(job.items)
        noun = "file" if count == 1 else "files"
        dest = job.dgx_dest_dir.replace("~", "").rstrip("/").split("/")[-1] or "Desktop"
        lbl = QLabel(f"{count} {noun} \u2192 {dest}")
        lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 12px; font-weight: 600;")
        hl.addWidget(lbl)
        hl.addStretch()

        self._job_lbl = QLabel("queued")
        self._job_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
        hl.addWidget(self._job_lbl)

        root.addWidget(hdr)

        # File sub-rows (one per transferred item)
        for item in job.items:
            row = _FileRow(item)
            root.addWidget(row)
            self._file_rows[item.item_id] = row

        # Folder view tabs — hidden until files reach DGX bridge staging
        self._folder_view = _FolderView(
            session_id=job.session_id,
            local_path=session.local_prep_path,
        )
        root.addWidget(self._folder_view)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        root.addWidget(sep)

    # ---- Slot helpers (called from TransferPanel) -------------------

    def on_item_progress(self, item_id: str, done: int, total: int):
        row = self._file_rows.get(item_id)
        if row:
            row.update_progress(done, total)

    def on_item_status(self, item_id: str, status: str, msg: str):
        row = self._file_rows.get(item_id)
        if row:
            row.update_status(status, msg)
        self._recalc_job_state()

    def on_job_progress(self, done: int, total: int):
        if done < total:
            self._job_lbl.setText(f"{done}/{total}")
            self._job_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
        else:
            self._job_lbl.setText("done")

    def show_bridge(self, conn):
        """Activate the folder view tabs once files are in DGX staging."""
        self._folder_view.activate(conn)

    def _recalc_job_state(self):
        items = self._job.items
        statuses = [i.status for i in items]
        if all(s == "done" for s in statuses):
            self._job_lbl.setText("\u2705 done")
            self._job_lbl.setStyleSheet(f"color: {SUCCESS}; font-size: 10px;")
        elif (
            all(s in ("bridge", "done") for s in statuses)
            and any(s == "bridge" for s in statuses)
        ):
            self._job_lbl.setText("\U0001f3af ready")
            self._job_lbl.setStyleSheet(f"color: {ACCENT}; font-size: 10px;")
        elif any(s == "failed" for s in statuses):
            fc = sum(1 for s in statuses if s == "failed")
            self._job_lbl.setText(f"\u26a0 {fc} failed")
            self._job_lbl.setStyleSheet(f"color: {ERROR}; font-size: 10px;")
        elif any(s == "running" for s in statuses):
            rc = sum(1 for s in statuses if s == "running")
            self._job_lbl.setText(f"\u2191 {rc} sending")
            self._job_lbl.setStyleSheet(f"color: {ACCENT}; font-size: 10px;")
        elif any(s == "verifying" for s in statuses):
            self._job_lbl.setText("\U0001f50d verifying")
            self._job_lbl.setStyleSheet(f"color: {WARNING}; font-size: 10px;")


# -----------------------------------------------------------------------
# Transfer Queue Panel (the public-facing widget)
# -----------------------------------------------------------------------

class TransferPanel(QWidget):
    """
    Sidebar panel.  Wire up:
      panel.set_connection(conn)
      panel.enqueue_drop(paths, dgx_dest_dir)  <- called from MainWindow on drop
    """

    def __init__(self, connection=None, parent=None):
        super().__init__(parent)
        self._conn: Optional[object] = connection
        self._session: TransferSession = TransferSession()
        self._workers: list[TransferWorker] = []
        self._job_rows: dict[str, _JobRow] = {}
        # Global progress: item_id -> (done_bytes, total_bytes)
        self._item_progress: dict[str, tuple[int, int]] = {}

        self.setMinimumWidth(280)
        self.setMaximumWidth(400)
        self._build_ui()

    def set_connection(self, conn):
        self._conn = conn

    # ---- Build UI ---------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(36)
        hdr.setStyleSheet(
            f"background: {BG_RAISED}; border-bottom: 1px solid {BORDER};"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(12, 0, 8, 0)
        hl.setSpacing(0)
        title = QLabel("Transfers")
        title.setStyleSheet(
            f"color: {TEXT_MAIN}; font-weight: 700; font-size: 13px;"
        )
        hl.addWidget(title)
        hl.addStretch()

        self._activity_lbl = QLabel("")
        self._activity_lbl.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 10px; padding-right: 4px;"
        )
        hl.addWidget(self._activity_lbl)

        btn_close = QPushButton("\u2715")
        btn_close.setFlat(True)
        btn_close.setFixedSize(28, 28)
        btn_close.setStyleSheet(f"color: {TEXT_DIM}; font-size: 14px;")
        btn_close.clicked.connect(self.hide)
        hl.addWidget(btn_close)
        root.addWidget(hdr)

        # Global 3-px progress bar — visible only during active transfer
        self._global_bar = QProgressBar()
        self._global_bar.setFixedHeight(3)
        self._global_bar.setTextVisible(False)
        self._global_bar.setRange(0, 100)
        self._global_bar.setValue(0)
        self._global_bar.setStyleSheet(
            f"QProgressBar {{ background: {BG_RAISED}; border: none; margin: 0; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; }}"
        )
        self._global_bar.setVisible(False)
        root.addWidget(self._global_bar)

        # Scroll area for job rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {BG_DEEP}; }}"
        )

        self._jobs_widget = QWidget()
        self._jobs_widget.setStyleSheet(f"background: {BG_DEEP};")
        self._jobs_layout = QVBoxLayout(self._jobs_widget)
        self._jobs_layout.setContentsMargins(0, 0, 0, 0)
        self._jobs_layout.setSpacing(0)
        self._jobs_layout.addStretch()

        self._empty_lbl = QLabel(
            "Drop files onto the DGX view\nto transfer them here."
        )
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 12px; padding: 24px;"
        )
        self._jobs_layout.insertWidget(0, self._empty_lbl)

        scroll.setWidget(self._jobs_widget)
        root.addWidget(scroll, 1)

        # Footer
        ftr = QWidget()
        ftr.setFixedHeight(36)
        ftr.setStyleSheet(
            f"background: {BG_RAISED}; border-top: 1px solid {BORDER};"
        )
        fl = QHBoxLayout(ftr)
        fl.setContentsMargins(8, 0, 8, 0)
        fl.setSpacing(4)

        def _ftr_btn(text: str, tip: str = "") -> QPushButton:
            b = QPushButton(text)
            b.setFixedHeight(24)
            b.setStyleSheet(
                f"QPushButton {{ background: {BG_SURFACE}; color: {TEXT_DIM};"
                f"border: 1px solid {BORDER}; border-radius: 4px;"
                f"font-size: 10px; padding: 0 6px; }}"
                f"QPushButton:hover {{ color: {TEXT_MAIN}; border-color: {ACCENT}66; }}"
            )
            if tip:
                b.setToolTip(tip)
            return b

        btn_stage = _ftr_btn("\U0001f4c1 Staging", "Open PC staging folder")
        btn_stage.clicked.connect(self._session.open_stage_dir)
        fl.addWidget(btn_stage)

        btn_log = _ftr_btn("\U0001f5d2 Log", "Open session transfer log")
        btn_log.clicked.connect(self._session.open_log)
        fl.addWidget(btn_log)

        fl.addStretch()

        btn_clear = _ftr_btn("Clear done")
        btn_clear.clicked.connect(self._clear_done)
        fl.addWidget(btn_clear)

        root.addWidget(ftr)

    # ---- Public API -------------------------------------------------

    def enqueue_drop(self, paths: list[str], dgx_dest_dir: str = ""):
        """Called from MainWindow when files are dropped onto the DGX canvas."""
        if not self._conn:
            log.warning("enqueue_drop: no connection")
            return

        job = self._session.make_job(paths, dgx_dest_dir)
        if not job.items:
            log.warning("enqueue_drop: no transferable items in drop")
            return

        self._empty_lbl.hide()
        row = _JobRow(job, self._session)
        count = self._jobs_layout.count()
        self._jobs_layout.insertWidget(count - 1, row)
        self._job_rows[job.job_id] = row

        worker = TransferWorker(job, self._conn, self._session)
        worker.item_progress.connect(
            lambda iid, done, total, r=row: (
                r.on_item_progress(iid, done, total),
                self._update_global_progress(iid, done, total),
            )
        )
        worker.item_status.connect(
            lambda iid, st, msg, r=row: self._on_item_status(iid, st, msg, r)
        )
        worker.job_progress.connect(
            lambda done, total, r=row: r.on_job_progress(done, total)
        )
        worker.job_bridge_ready.connect(self._on_job_bridge_ready)
        worker.job_complete.connect(self._on_job_complete)

        self._workers.append(worker)
        worker.start()

        self._global_bar.setVisible(True)
        self._activity_lbl.setText("transferring...")

        log.info(
            "Started job %s: %d items -> %s",
            job.job_id, len(job.items), dgx_dest_dir or "~/Desktop",
        )

    def enqueue_paths(self, paths: list[str], dgx_dest: str = ""):
        self.enqueue_drop(paths, dgx_dest)

    # ---- Global progress bar ----------------------------------------

    def _update_global_progress(self, item_id: str, done: int, total: int):
        self._item_progress[item_id] = (done, total)
        total_b = sum(t for _, t in self._item_progress.values())
        done_b = sum(d for d, _ in self._item_progress.values())
        if total_b > 0:
            self._global_bar.setVisible(True)
            self._global_bar.setValue(int(done_b * 100 / total_b))

    def _on_item_status(self, item_id: str, status: str, msg: str, row: _JobRow):
        row.on_item_status(item_id, status, msg)
        if status in ("done", "failed", "bridge"):
            self._item_progress.pop(item_id, None)
            self._recalc_global_bar()

    def _recalc_global_bar(self):
        if not self._item_progress and not any(
            w.isRunning() for w in self._workers
        ):
            self._global_bar.setVisible(False)
            self._global_bar.setValue(0)
            self._activity_lbl.setText("")

    # ---- Internal signal handlers -----------------------------------

    @pyqtSlot(str, int, int)
    def _on_job_complete(self, job_id: str, ok: int, fail: int):
        log.info("Job %s finished: %d ok, %d failed", job_id, ok, fail)
        self._workers = [w for w in self._workers if w.isRunning()]
        self._recalc_global_bar()

    @pyqtSlot(str, str)
    def _on_job_bridge_ready(self, job_id: str, session_id: str):
        """Files landed in DGX staging — activate the in-panel folder navigator."""
        log.info("Job %s bridge-ready (session %s)", job_id, session_id)
        row = self._job_rows.get(job_id)
        if row:
            row.show_bridge(self._conn)
        self._recalc_global_bar()

    def _clear_done(self):
        for job_id, row in list(self._job_rows.items()):
            if all(
                i.status in ("done", "failed")
                for i in self._job_rows[job_id]._job.items
            ):
                row.setParent(None)
                row.deleteLater()
                del self._job_rows[job_id]
        if not self._job_rows:
            self._empty_lbl.show()
