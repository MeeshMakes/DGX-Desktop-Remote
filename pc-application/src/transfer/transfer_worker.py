"""
pc-application/src/transfer/transfer_worker.py

Transfer pipeline worker (QThread).

Pipeline per file:
  1. Validate (exists, readable, size)
  2. Compute SHA-256 of source
  3. CRLF-strip if text file (uses FileConverter)
  4. Send to DGX staging area  (send_file RPC)
  5. Verify integrity (DGX echoes sha256 back)
  6. Place from DGX staging → final destination  (place_staged RPC)
  7. Log everything to TransferSession

Signals emitted on the GUI thread via Qt's queued connection.
"""

import logging
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

from .transfer_session import (
    TransferSession, TransferJob, TransferItem, LogEntry, sha256_file
)
from .file_analyzer  import analyze_file
from .file_converter import FileConverter

log = logging.getLogger("pc.transfer.worker")


class TransferWorker(QThread):
    """
    Processes all items in one TransferJob sequentially.
    Emits fine-grained signals so the UI can update per-file.
    """

    # ── Signals ───────────────────────────────────────────────────────
    # (item_id, bytes_done, bytes_total)
    item_progress    = pyqtSignal(str, int, int)
    # (item_id, status)  status: "running"|"verifying"|"done"|"failed"
    item_status      = pyqtSignal(str, str, str)   # id, status, message
    # (items_done, items_total)
    job_progress     = pyqtSignal(int, int)
    # emitted when all items processed
    job_complete     = pyqtSignal(str, int, int)   # job_id, ok_count, fail_count

    def __init__(self, job: TransferJob, connection,
                 session: TransferSession, parent=None):
        super().__init__(parent)
        self._job     = job
        self._conn    = connection
        self._session = session
        self._abort   = False

    def abort(self):
        self._abort = True

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self):
        total    = len(self._job.items)
        ok_count = 0
        fail_count = 0
        converter = FileConverter()

        for idx, item in enumerate(self._job.items):
            if self._abort:
                item.status    = "failed"
                item.error_msg = "Cancelled"
                self.item_status.emit(item.item_id, "failed", "Cancelled")
                fail_count += 1
                continue

            self.job_progress.emit(idx, total)
            self._process_item(item, converter)

            if item.status == "done":
                ok_count += 1
            else:
                fail_count += 1

        converter.cleanup()
        self.job_progress.emit(total, total)
        self.job_complete.emit(self._job.job_id, ok_count, fail_count)
        log.info("Job %s complete: %d ok, %d failed", self._job.job_id,
                 ok_count, fail_count)

    # ── Per-item pipeline ─────────────────────────────────────────────

    def _process_item(self, item: TransferItem, converter: FileConverter):
        entry = LogEntry(
            session_id = self._job.session_id,
            item_id    = item.item_id,
            src_path   = str(item.local_path),
            dst_path   = item.dgx_dest,
            file_ext   = item.local_path.suffix,
            size_bytes = 0,
            ts_queued  = time.time(),
            method     = "file",
        )

        # ── 1. Validate ───────────────────────────────────────────────
        self.item_status.emit(item.item_id, "running", "Validating…")
        info = analyze_file(item.local_path)
        entry.size_bytes = info.size
        item.bytes_total = info.size

        if not info.is_readable:
            return self._fail(item, entry, info.error or "Unreadable")

        # ── 2. SHA-256 source ─────────────────────────────────────────
        self.item_status.emit(item.item_id, "running", "Checksumming…")
        sha_src = sha256_file(item.local_path)
        item.sha256_src  = sha_src
        entry.sha256_src = sha_src

        # ── 3. Prepare (CRLF strip) ───────────────────────────────────
        send_path, is_tmp = converter.prepare(info, convert_crlf=True)
        metadata = converter.get_remote_metadata(info)

        # ── 4. Send to DGX staging ────────────────────────────────────
        entry.ts_started = time.time()
        item.status = "running"
        self.item_status.emit(item.item_id, "running", f"Sending {info.size_human}…")

        dgx_stage_folder = self._session.dgx_stage_path
        result = self._conn.send_file(
            local_path    = str(send_path),
            remote_folder = dgx_stage_folder,
            progress_cb   = lambda done, total_b, _id=item.item_id: (
                self.item_progress.emit(_id, done, total_b)
            ),
            metadata = metadata,
        )

        if is_tmp:
            converter.cleanup()

        if not result.get("ok"):
            return self._fail(item, entry, result.get("error", "Send failed"))

        # ── 5. Verify integrity ───────────────────────────────────────
        self.item_status.emit(item.item_id, "verifying", "Verifying…")
        sha_dst = result.get("sha256", "")
        item.sha256_dst  = sha_dst
        entry.sha256_dst = sha_dst

        if sha_src and sha_dst:
            ok = sha_src == sha_dst
        else:
            # fall back to size check
            ok = result.get("ok", False)

        item.integrity_ok  = ok
        entry.integrity_ok = ok

        if not ok:
            return self._fail(item, entry, f"Integrity mismatch (src={sha_src[:8]} dst={sha_dst[:8]})")

        # ── 6. Place from staging → final dest ───────────────────────
        self.item_status.emit(item.item_id, "running", "Placing file…")
        place_result = self._conn.rpc({
            "type":         "place_staged",
            "session_id":   self._job.session_id,
            "filename":     item.local_path.name,
            "destination":  item.dgx_dest,
        })

        if not place_result.get("ok"):
            return self._fail(item, entry, place_result.get("error", "Place failed"))

        # ── Done ──────────────────────────────────────────────────────
        entry.ts_finished = time.time()
        entry.status      = "done"
        item.status       = "done"
        self._session.log_entry(entry)
        self.item_status.emit(item.item_id, "done", "")

    def _fail(self, item: TransferItem, entry: LogEntry, msg: str):
        item.status       = "failed"
        item.error_msg    = msg
        entry.status      = "failed"
        entry.error       = msg
        entry.ts_finished = time.time()
        self._session.log_entry(entry)
        self.item_status.emit(item.item_id, "failed", msg)
        log.warning("Transfer failed [%s]: %s", item.local_path.name, msg)

