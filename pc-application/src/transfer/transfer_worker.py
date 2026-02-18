"""
pc-application/src/transfer/transfer_worker.py

Transfer pipeline worker (QThread).

Pipeline per file:
  0. Local prep  — copy + convert to bridge-prep/<session>/ folder
  1. Validate    — exists, readable, size check
  2. SHA-256     — checksum source (post-conversion)
  3. Send        — upload converted file to DGX BridgeStaging/<session>/
  4. Verify      — compare sha256 echoed back by DGX
     → emit job_bridge_ready when all files are staged on DGX
     (user then drags from DGX bridge folder to desired location)

  Optional step 5 (if auto_place=True):
  5. Place       — RPC place_staged moves file to final DGX destination

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
    item_progress    = pyqtSignal(str, int, int)   # id, bytes_done, bytes_total
    item_status      = pyqtSignal(str, str, str)   # id, status, message
    job_progress     = pyqtSignal(int, int)         # items_done, items_total
    job_complete     = pyqtSignal(str, int, int)    # job_id, ok_count, fail_count
    # Fired once all items are sitting in DGX BridgeStaging (ready to drag)
    job_bridge_ready = pyqtSignal(str, str)          # job_id, session_id

    def __init__(self, job: TransferJob, connection,
                 session: TransferSession,
                 auto_place: bool = False,
                 parent=None):
        super().__init__(parent)
        self._job        = job
        self._conn       = connection
        self._session    = session
        self._auto_place = auto_place   # if True, call place_staged automatically
        self._abort      = False

    def abort(self):
        self._abort = True

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self):
        total      = len(self._job.items)
        ok_count   = 0
        fail_count = 0
        converter  = FileConverter()
        prep_dir   = self._session.local_prep_path   # bridge-prep/<session_id>/

        for idx, item in enumerate(self._job.items):
            if self._abort:
                item.status    = "failed"
                item.error_msg = "Cancelled"
                self.item_status.emit(item.item_id, "failed", "Cancelled")
                fail_count += 1
                continue

            self.job_progress.emit(idx, total)
            self._process_item(item, converter, prep_dir)

            if item.status in ("done", "bridge"):
                ok_count += 1
            else:
                fail_count += 1

        converter.cleanup()
        self.job_progress.emit(total, total)
        self.job_complete.emit(self._job.job_id, ok_count, fail_count)

        # If all items reached bridge state, announce readiness
        if all(i.status in ("done", "bridge") for i in self._job.items):
            self.job_bridge_ready.emit(self._job.job_id, self._job.session_id)

        log.info("Job %s complete: %d ok, %d failed",
                 self._job.job_id, ok_count, fail_count)

    # ── Per-item pipeline ─────────────────────────────────────────────

    def _process_item(self, item: TransferItem, converter: FileConverter,
                      prep_dir: Path):
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

        # ── 0. Local prep (copy + convert to bridge-prep folder) ──────
        self.item_status.emit(item.item_id, "running", "Preparing…")
        info = analyze_file(item.local_path)
        entry.size_bytes = info.size
        item.bytes_total = info.size

        if not info.is_readable:
            return self._fail(item, entry, info.error or "Unreadable")

        # Convert to bridge-prep dir so the user can inspect the result
        prep_path, dgx_name = converter.prepare_to_dir(info, prep_dir)
        item.dgx_name = dgx_name        # store for panel display

        # Update dgx_dest to use the (possibly changed) filename
        base_dir = item.dgx_dest.rsplit("/", 1)[0] if "/" in item.dgx_dest else item.dgx_dest
        item.dgx_dest = f"{base_dir}/{dgx_name}"

        converted = converter.needs_conversion(info)
        conv_label = f"→ {dgx_name}" if converted else ""
        self.item_status.emit(item.item_id, "running",
                              f"Converted {conv_label}".strip() if converted else "Checksumming…")

        # ── 1. Validate ───────────────────────────────────────────────
        # (already done via analyze_file above)

        # ── 2. SHA-256 source (post-conversion file) ──────────────────
        sha_src = sha256_file(prep_path)
        item.sha256_src  = sha_src
        entry.sha256_src = sha_src

        # ── 3. Send to DGX BridgeStaging ─────────────────────────────
        entry.ts_started = time.time()
        item.status      = "running"
        self.item_status.emit(item.item_id, "running",
                              f"Sending {info.size_human}…")

        # Rebuild metadata with the (possibly new) dgx_name
        re_info   = analyze_file(prep_path)
        metadata  = converter.get_remote_metadata(re_info, dgx_name)

        dgx_stage_folder = self._session.dgx_stage_path
        result = self._conn.send_file(
            local_path    = str(prep_path),
            remote_folder = dgx_stage_folder,
            progress_cb   = lambda done, total_b, _id=item.item_id: (
                self.item_progress.emit(_id, done, total_b)
            ),
            metadata = metadata,
        )

        if not result.get("ok"):
            return self._fail(item, entry, result.get("error", "Send failed"))

        # ── 4. Verify integrity ───────────────────────────────────────
        self.item_status.emit(item.item_id, "verifying", "Verifying…")
        sha_dst = result.get("sha256", "")
        item.sha256_dst  = sha_dst
        entry.sha256_dst = sha_dst

        if sha_src and sha_dst:
            ok = sha_src == sha_dst
        else:
            ok = result.get("ok", False)

        item.integrity_ok  = ok
        entry.integrity_ok = ok

        if not ok:
            return self._fail(item, entry,
                              f"Integrity mismatch "
                              f"(src={sha_src[:8]} dst={sha_dst[:8]})")

        # ── 5. Auto-place (optional) ──────────────────────────────────
        if self._auto_place:
            self.item_status.emit(item.item_id, "running", "Placing file…")
            place_result = self._conn.rpc({
                "type":        "place_staged",
                "session_id":  self._job.session_id,
                "filename":    dgx_name,
                "destination": item.dgx_dest,
            })
            if not place_result.get("ok"):
                return self._fail(item, entry,
                                  place_result.get("error", "Place failed"))

            entry.ts_finished = time.time()
            entry.status      = "done"
            item.status       = "done"
            self._session.log_entry(entry)
            self.item_status.emit(item.item_id, "done", "")

        else:
            # Bridge mode: file is in DGX staging — user drags to destination
            entry.ts_finished = time.time()
            entry.status      = "bridge"
            item.status       = "bridge"
            self._session.log_entry(entry)
            self.item_status.emit(item.item_id, "bridge",
                                  f"In DGX bridge — {dgx_name}")

    def _fail(self, item: TransferItem, entry: LogEntry, msg: str):
        item.status       = "failed"
        item.error_msg    = msg
        entry.status      = "failed"
        entry.error       = msg
        entry.ts_finished = time.time()
        self._session.log_entry(entry)
        self.item_status.emit(item.item_id, "failed", msg)
        log.warning("Transfer failed [%s]: %s", item.local_path.name, msg)

