"""
pc-application/src/transfer/transfer_worker.py
QThread worker for queued file upload and download operations.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable
import time

from PyQt6.QtCore import QThread, pyqtSignal

from .file_analyzer import analyze_file, FileInfo
from .file_converter import FileConverter


# ──────────────────────────────────────────────────────────────────────
# Transfer item descriptor
# ──────────────────────────────────────────────────────────────────────

@dataclass
class TransferItem:
    local_path:     Path
    remote_folder:  str   = "inbox"     # "inbox" | "staging" | "outbox"
    direction:      str   = "upload"    # "upload" | "download"
    convert_crlf:   bool  = True
    # Populated after analysis
    info:           Optional[FileInfo] = field(default=None, repr=False)
    # Runtime state
    status:         str   = "pending"   # pending | running | done | error
    error_msg:      str   = ""


# ──────────────────────────────────────────────────────────────────────
# Worker thread
# ──────────────────────────────────────────────────────────────────────

class TransferWorker(QThread):
    """
    Processes a list of TransferItems sequentially.
    Emits progress and completion signals on each item.
    """

    # item_id, bytes_done, bytes_total
    progress         = pyqtSignal(str, int, int)
    # item_id, success, message
    item_complete    = pyqtSignal(str, bool, str)
    # items_done, items_total
    overall_progress = pyqtSignal(int, int)
    # emitted when all items processed
    batch_complete   = pyqtSignal()
    # human-readable status message
    status_msg       = pyqtSignal(str)

    def __init__(self, items: list[TransferItem], connection, parent=None):
        super().__init__(parent)
        self._items = items
        self._conn  = connection
        self._abort = False

    def abort(self):
        self._abort = True

    def run(self):
        total = len(self._items)
        converter = FileConverter()

        for idx, item in enumerate(self._items):
            if self._abort:
                break

            item_id = str(item.local_path)
            self.overall_progress.emit(idx, total)
            self.status_msg.emit(f"Analyzing  {item.local_path.name} …")

            # Analyze
            info = analyze_file(item.local_path)
            item.info = info

            if not info.is_readable:
                item.status = "error"
                item.error_msg = info.error or "Unreadable"
                self.item_complete.emit(item_id, False, item.error_msg)
                continue

            # Prepare (CRLF strip if needed)
            send_path, is_tmp = converter.prepare(info, item.convert_crlf)
            metadata = converter.get_remote_metadata(info)

            self.status_msg.emit(f"Sending  {item.local_path.name} …")
            item.status = "running"

            def _progress(done: int, total_b: int):
                self.progress.emit(item_id, done, total_b)

            try:
                if item.direction == "upload":
                    result = self._conn.send_file(
                        local_path=send_path,
                        remote_folder=item.remote_folder,
                        progress_cb=_progress,
                        metadata=metadata,
                    )
                else:
                    result = self._conn.get_file(
                        filename=item.local_path.name,
                        folder=item.remote_folder,
                        local_dest=item.local_path.parent,
                        progress_cb=_progress,
                    )

                if result.get("ok"):
                    item.status = "done"
                    self.item_complete.emit(item_id, True, "")
                else:
                    item.status = "error"
                    item.error_msg = result.get("error", "Transfer failed")
                    self.item_complete.emit(item_id, False, item.error_msg)

            except Exception as e:
                item.status = "error"
                item.error_msg = str(e)
                self.item_complete.emit(item_id, False, item.error_msg)

            finally:
                if is_tmp:
                    converter.cleanup()

        self.overall_progress.emit(total, total)
        self.batch_complete.emit()
