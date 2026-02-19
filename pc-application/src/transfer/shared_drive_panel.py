"""
pc-application/src/transfer/shared_drive_panel.py

Shared Drive panel — browse ~/SharedDrive/ on the DGX.
  • Upload files from PC into the shared drive
  • Download files from the shared drive to PC
  • Delete files from the shared drive
  • Open the folder in the DGX file manager (Nautilus)

The DGX path ~/SharedDrive/ is always accessible from both sides, making it
the bidirectional exchange area.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (
    QThread, Qt, QTimer, pyqtSignal, pyqtSlot,
)
from PyQt6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget, QProgressBar,
    QMessageBox,
)

from theme import (
    ACCENT, BG_BASE, BG_RAISED, BG_SURFACE, BORDER,
    ERROR, SUCCESS, TEXT_DIM, TEXT_MAIN, WARNING,
)

log = logging.getLogger("pc.shared_drive")

_DOWNLOADS_DIR = Path.home() / "Downloads"


# ──────────────────────────────────────────────────────────────────────
# Background workers
# ──────────────────────────────────────────────────────────────────────

class _ListThread(QThread):
    result = pyqtSignal(dict)

    def __init__(self, conn):
        super().__init__()
        self._conn = conn

    def run(self):
        try:
            r = self._conn.rpc({"type": "list_shared"}, timeout=10)
            self.result.emit(r)
        except Exception as exc:
            self.result.emit({"ok": False, "error": str(exc)})


class _UploadThread(QThread):
    file_done     = pyqtSignal(str, bool, str)   # filename, ok, error
    file_progress = pyqtSignal(str, int, int)     # filename, done, total
    all_done      = pyqtSignal()

    def __init__(self, conn, paths: list[str]):
        super().__init__()
        self._conn  = conn
        self._paths = paths

    def run(self):
        for path_str in self._paths:
            p = Path(path_str)
            try:
                result = self._conn.send_file(
                    local_path    = path_str,
                    remote_folder = "SharedDrive",
                    progress_cb   = lambda done, total, fn=p.name: (
                        self.file_progress.emit(fn, done, total)
                    ),
                )
                ok  = result.get("ok", False)
                err = result.get("error", "") if not ok else ""
                self.file_done.emit(p.name, ok, err)
            except Exception as exc:
                self.file_done.emit(p.name, False, str(exc))
        self.all_done.emit()


class _DownloadThread(QThread):
    progress = pyqtSignal(int, int)   # done_bytes, total_bytes
    done     = pyqtSignal(dict)       # result dict

    def __init__(self, conn, filename: str, local_path: str):
        super().__init__()
        self._conn       = conn
        self._filename   = filename
        self._local_path = local_path

    def run(self):
        try:
            result = self._conn.get_file(
                filename   = self._filename,
                folder     = "SharedDrive",
                local_dest = self._local_path,
                progress_cb= lambda done, total: self.progress.emit(done, total),
            )
            self.done.emit(result)
        except Exception as exc:
            self.done.emit({"ok": False, "error": str(exc)})


# ──────────────────────────────────────────────────────────────────────
# File row widget
# ──────────────────────────────────────────────────────────────────────

class _FileItem(QListWidgetItem):
    def __init__(self, info: dict):
        super().__init__()
        self.info = info
        self._refresh_text()

    def _refresh_text(self):
        name  = self.info.get("name", "?")
        human = self.info.get("size_human", "")
        mtime = self.info.get("mtime", 0)
        dt    = datetime.fromtimestamp(mtime).strftime("%b %d, %H:%M") if mtime else ""
        self.setText(f"  {name}   ({human})   {dt}")
        self.setToolTip(name)


# ──────────────────────────────────────────────────────────────────────
# Main panel
# ──────────────────────────────────────────────────────────────────────

class SharedDrivePanel(QWidget):
    """
    Browseable, bidirectional file panel for ~/SharedDrive/ on the DGX.
    Drop from PC → DGX; download from DGX → PC.
    """

    def __init__(self, connection=None, parent=None):
        super().__init__(parent)
        self._conn: Optional[object] = connection
        self._files: list[dict] = []
        self._list_thread:     Optional[_ListThread]     = None
        self._upload_thread:   Optional[_UploadThread]   = None
        self._download_thread: Optional[_DownloadThread] = None
        self._build_ui()
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setFixedHeight(40)
        hdr.setStyleSheet(
            f"background: {BG_RAISED}; border-bottom: 1px solid {BORDER};"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(10, 0, 8, 0)
        hl.setSpacing(6)

        lbl = QLabel("📂  Shared Drive")
        lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {TEXT_MAIN}; background: transparent;"
        )
        hl.addWidget(lbl)
        hl.addStretch()

        self._btn_open_dgx = QPushButton("Open on DGX")
        self._btn_open_dgx.setFixedHeight(26)
        self._btn_open_dgx.setStyleSheet(
            f"font-size: 11px; padding: 0 8px; background: {BG_SURFACE};"
            f"color: {ACCENT}; border: 1px solid {BORDER}; border-radius: 4px;"
        )
        self._btn_open_dgx.clicked.connect(self._open_on_dgx)
        hl.addWidget(self._btn_open_dgx)

        self._btn_refresh = QPushButton("⟳")
        self._btn_refresh.setFixedSize(26, 26)
        self._btn_refresh.setToolTip("Refresh file list")
        self._btn_refresh.setStyleSheet(
            f"font-size: 14px; background: {BG_SURFACE}; color: {TEXT_MAIN};"
            f"border: 1px solid {BORDER}; border-radius: 4px;"
        )
        self._btn_refresh.clicked.connect(self.refresh)
        hl.addWidget(self._btn_refresh)
        root.addWidget(hdr)

        # ── Path hint ─────────────────────────────────────────────────
        self._path_lbl = QLabel("~/SharedDrive/")
        self._path_lbl.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 10px; padding: 4px 12px 2px;"
            f"background: {BG_BASE};"
        )
        root.addWidget(self._path_lbl)

        # ── File list ─────────────────────────────────────────────────
        self._list = QListWidget()
        self._list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )
        self._list.setStyleSheet(
            f"QListWidget {{"
            f"  background: {BG_BASE}; border: none; outline: none;"
            f"  color: {TEXT_MAIN}; font-size: 12px;"
            f"}}"
            f"QListWidget::item:selected {{"
            f"  background: {ACCENT}22; color: {TEXT_MAIN};"
            f"}}"
            f"QListWidget::item:hover {{ background: {BG_SURFACE}; }}"
        )
        self._list.itemDoubleClicked.connect(self._download_selected)
        root.addWidget(self._list, 1)

        # ── Status / progress ─────────────────────────────────────────
        self._status_lbl = QLabel("Not connected")
        self._status_lbl.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 10px; padding: 2px 12px;"
            f"background: {BG_BASE};"
        )
        root.addWidget(self._status_lbl)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(3)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            f"QProgressBar {{ background: {BG_RAISED}; border: none; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; }}"
        )
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # ── Action bar ────────────────────────────────────────────────
        act = QWidget()
        act.setStyleSheet(
            f"background: {BG_RAISED}; border-top: 1px solid {BORDER};"
        )
        act.setFixedHeight(44)
        al = QHBoxLayout(act)
        al.setContentsMargins(8, 0, 8, 0)
        al.setSpacing(6)

        def _btn(text, tip, slot, danger=False):
            b = QPushButton(text)
            b.setFixedHeight(30)
            b.setToolTip(tip)
            color = ERROR if danger else ACCENT
            b.setStyleSheet(
                f"font-size: 11px; padding: 0 10px; background: {BG_SURFACE};"
                f"color: {color}; border: 1px solid {BORDER}; border-radius: 4px;"
            )
            b.clicked.connect(slot)
            return b

        self._btn_upload   = _btn("⬆ Upload",   "Add files to Shared Drive",      self._upload_files)
        self._btn_download = _btn("⬇ Download", "Download selected file to PC",   self._download_selected)
        self._btn_delete   = _btn("✕ Delete",   "Delete selected file from drive", self._delete_selected, danger=True)

        al.addWidget(self._btn_upload)
        al.addWidget(self._btn_download)
        al.addStretch()
        al.addWidget(self._btn_delete)
        root.addWidget(act)

        self._set_controls_enabled(False)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def set_connection(self, conn):
        self._conn = conn
        if conn and getattr(conn, "connected", False):
            self._set_controls_enabled(True)
            self._status_lbl.setText("Loading…")
            QTimer.singleShot(400, self.refresh)
        else:
            self._set_controls_enabled(False)
            self._list.clear()
            self._status_lbl.setText("Not connected")

    def _set_controls_enabled(self, enabled: bool):
        self._btn_upload.setEnabled(enabled)
        self._btn_download.setEnabled(enabled)
        self._btn_delete.setEnabled(enabled)
        self._btn_refresh.setEnabled(enabled)
        self._btn_open_dgx.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Drag-and-drop (drop files onto panel to upload)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.isLocalFile()]
        if paths and self._conn and getattr(self._conn, "connected", False):
            self._start_upload(paths)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh(self):
        if not self._conn or not getattr(self._conn, "connected", False):
            return
        self._status_lbl.setText("Loading…")
        self._btn_refresh.setEnabled(False)
        self._list_thread = _ListThread(self._conn)
        self._list_thread.result.connect(self._on_list_result)
        self._list_thread.start()

    @pyqtSlot(dict)
    def _on_list_result(self, result: dict):
        try:
            _ = self._list   # guard: panel may have been destroyed
        except RuntimeError:
            return
        self._btn_refresh.setEnabled(True)
        if not result.get("ok"):
            err = result.get("error", "Unknown error")
            self._status_lbl.setText(f"Error: {err}")
            log.warning("list_shared failed: %s", err)
            return

        self._files = result.get("files", [])
        self._list.clear()
        for info in self._files:
            self._list.addItem(_FileItem(info))

        count = len(self._files)
        total = sum(f.get("size", 0) for f in self._files)
        human_total = _human(total)
        self._status_lbl.setText(
            f"{count} file{'s' if count != 1 else ''}  ·  {human_total} total"
            if count else "Empty — drop or upload files here"
        )
        # Update path label if server returned the actual path
        if result.get("path"):
            self._path_lbl.setText(result["path"])

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def _upload_files(self):
        if not self._conn or not getattr(self._conn, "connected", False):
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add files to Shared Drive", str(Path.home()))
        if paths:
            self._start_upload(paths)

    def _start_upload(self, paths: list[str]):
        self._set_controls_enabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._status_lbl.setText(f"Uploading {len(paths)} file(s)…")
        self._upload_thread = _UploadThread(self._conn, paths)
        self._upload_thread.file_progress.connect(self._on_upload_progress)
        self._upload_thread.file_done.connect(self._on_file_uploaded)
        self._upload_thread.all_done.connect(self._on_upload_all_done)
        self._upload_thread.start()

    @pyqtSlot(str, int, int)
    def _on_upload_progress(self, filename: str, done: int, total: int):
        if total > 0:
            self._progress.setValue(int(done * 100 / total))
        self._status_lbl.setText(f"Uploading {filename}…  {_human(done)} / {_human(total)}")

    @pyqtSlot(str, bool, str)
    def _on_file_uploaded(self, filename: str, ok: bool, error: str):
        if not ok:
            log.warning("Upload failed: %s — %s", filename, error)

    @pyqtSlot()
    def _on_upload_all_done(self):
        self._progress.setVisible(False)
        self._set_controls_enabled(True)
        self.refresh()

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _download_selected(self):
        items = self._list.selectedItems()
        if not items or not self._conn or not getattr(self._conn, "connected", False):
            return
        item: _FileItem = items[0]
        filename = item.info.get("name", "")
        if not filename:
            return

        # Ask where to save
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save file", str(_DOWNLOADS_DIR / filename))
        if not dest:
            return

        self._set_controls_enabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._status_lbl.setText(f"Downloading {filename}…")
        self._download_thread = _DownloadThread(self._conn, filename, dest)
        self._download_thread.progress.connect(self._on_download_progress)
        self._download_thread.done.connect(self._on_download_done)
        self._download_thread.start()

    @pyqtSlot(int, int)
    def _on_download_progress(self, done: int, total: int):
        if total > 0:
            self._progress.setValue(int(done * 100 / total))
        self._status_lbl.setText(f"Downloading…  {_human(done)} / {_human(total)}")

    @pyqtSlot(dict)
    def _on_download_done(self, result: dict):
        self._progress.setVisible(False)
        self._set_controls_enabled(True)
        if result.get("ok"):
            self._status_lbl.setText("Download complete ✓")
        else:
            err = result.get("error", "Unknown error")
            self._status_lbl.setText(f"Download failed: {err}")
            log.warning("Download failed: %s", err)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _delete_selected(self):
        items = self._list.selectedItems()
        if not items:
            return
        names = [i.info.get("name", "?") for i in items if hasattr(i, "info")]
        if not names:
            return

        msg = (f"Delete {names[0]!r} from the Shared Drive?"
               if len(names) == 1
               else f"Delete {len(names)} files from the Shared Drive?")
        if QMessageBox.question(
            self, "Delete from Shared Drive", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return

        for name in names:
            try:
                result = self._conn.rpc(
                    {"type": "delete_shared", "filename": name})
                if not result.get("ok"):
                    log.warning("delete_shared failed: %s", result.get("error"))
            except Exception as exc:
                log.warning("delete_shared exception: %s", exc)
        self.refresh()

    # ------------------------------------------------------------------
    # Open on DGX
    # ------------------------------------------------------------------

    def _open_on_dgx(self):
        if not self._conn:
            return
        try:
            self._conn.rpc({"type": "open_shared_drive"})
        except Exception as exc:
            log.warning("open_shared_drive RPC failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
