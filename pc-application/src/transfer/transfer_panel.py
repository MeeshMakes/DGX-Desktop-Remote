"""
pc-application/src/transfer/transfer_panel.py

Transfer Queue Panel — the only transfer UI.

No settings dialogs.  Drag-and-drop onto the DGX canvas view triggers
the automatic pipeline:
  drop → capture selection + destination → stage → send → verify → place → log

Layout
  ┌─────────────────────────────────┐
  │  File Transfer              [✕] │  ← header
  ├─────────────────────────────────┤
  │  [job row]  filename  ████░░ 60%│
  │    ↳ file1.py  ✅               │
  │    ↳ folder/   ⏳ sending…      │
  ├─────────────────────────────────┤
  │  [Open Staging] [Open Log] [✕]  │  ← footer actions
  └─────────────────────────────────┘
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QProgressBar, QSizePolicy
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor

from theme import (
    ACCENT, SUCCESS, ERROR, WARNING, TEXT_DIM, TEXT_MAIN,
    BG_RAISED, BG_SURFACE, BG_BASE, BORDER, BG_DEEP
)
from .transfer_session import TransferSession, TransferJob, TransferItem
from .transfer_worker  import TransferWorker

log = logging.getLogger("pc.transfer.panel")


# ──────────────────────────────────────────────────────────────────────
# Single file row inside a job
# ──────────────────────────────────────────────────────────────────────

class _FileRow(QWidget):
    retry_requested = pyqtSignal(str)  # item_id

    _STATUS_STYLE = {
        "queued":     f"color: {TEXT_DIM};",
        "running":    f"color: {ACCENT};",
        "verifying":  f"color: {WARNING};",
        "done":       f"color: {SUCCESS};",
        "bridge":     f"color: {ACCENT};",
        "failed":     f"color: {ERROR};",
    }
    _STATUS_ICON = {
        "queued":    "⏳",
        "running":   "⬆",
        "verifying": "🔍",
        "done":      "✅",
        "bridge":    "🎯",
        "failed":    "❌",
    }

    def __init__(self, item: TransferItem, parent=None):
        super().__init__(parent)
        self._item = item
        self.setFixedHeight(26)

        l = QHBoxLayout(self)
        l.setContentsMargins(20, 0, 8, 0)
        l.setSpacing(6)

        self._icon = QLabel("⏳")
        self._icon.setFixedWidth(16)
        self._icon.setStyleSheet("font-size: 11px;")
        l.addWidget(self._icon)

        # Show converted name when .bat→.sh etc.
        display_name = (item.dgx_name
                        if item.dgx_name and item.dgx_name != item.local_path.name
                        else item.local_path.name)
        self._name = QLabel(display_name)
        self._name.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 11px;")
        self._name.setMaximumWidth(140)
        l.addWidget(self._name)

        self._bar = QProgressBar()
        self._bar.setFixedHeight(4)
        self._bar.setTextVisible(False)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setStyleSheet(
            f"QProgressBar {{ background: {BG_BASE}; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}"
        )
        l.addWidget(self._bar)

        self._msg = QLabel("")
        self._msg.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
        self._msg.setFixedWidth(80)
        l.addWidget(self._msg)

        self._btn_retry = QPushButton("↺")
        self._btn_retry.setFixedSize(20, 20)
        self._btn_retry.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {ACCENT};"
            f"font-size: 12px; border: none; }}"
        )
        self._btn_retry.hide()
        self._btn_retry.clicked.connect(
            lambda: self.retry_requested.emit(item.item_id)
        )
        l.addWidget(self._btn_retry)

    def update_progress(self, done: int, total: int):
        if total > 0:
            self._bar.setValue(int(done / total * 100))

    def update_status(self, status: str, msg: str = ""):
        self._icon.setText(self._STATUS_ICON.get(status, "⏳"))
        self._icon.setStyleSheet(
            "font-size: 11px; " + self._STATUS_STYLE.get(status, "")
        )
        if status == "running":
            pct = self._bar.value()
            self._msg.setText(f"{pct}%")
        elif status == "bridge":
            self._bar.setValue(100)
            self._msg.setText("in bridge")
            self._msg.setStyleSheet(f"color: {ACCENT}; font-size: 10px;")
        elif status in ("done", "failed"):
            self._bar.setValue(100 if status == "done" else self._bar.value())
            self._msg.setText("✓" if status == "done" else msg[:20] if msg else "error")
            if status == "failed":
                self._btn_retry.show()
                self._msg.setStyleSheet(f"color: {ERROR}; font-size: 10px;")
                self._msg.setToolTip(msg)
        elif status == "verifying":
            self._msg.setText("checking…")


# ──────────────────────────────────────────────────────────────────────
# Bridge-ready banner (shown inside _JobRow once files land in DGX)
# ──────────────────────────────────────────────────────────────────────

class _BridgeBanner(QWidget):
    open_dgx_requested  = pyqtSignal(str)   # session_id
    open_prep_requested = pyqtSignal()

    def __init__(self, job: TransferJob, parent=None):
        super().__init__(parent)
        self._session_id = job.session_id

        self.setStyleSheet(
            f"background: {ACCENT}18; border: 1px solid {ACCENT}44; border-radius: 6px;"
        )
        bl = QVBoxLayout(self)
        bl.setContentsMargins(10, 6, 10, 6)
        bl.setSpacing(4)

        lbl = QLabel("🎯  Files are ready in the DGX Bridge folder")
        lbl.setStyleSheet(
            f"color: {ACCENT}; font-size: 11px; font-weight: 600;"
            "border: none;"
        )
        bl.addWidget(lbl)

        hint = QLabel(
            "Move your mouse into the DGX view — the bridge folder is open.\n"
            "Drag files from there to wherever you want on the DGX."
        )
        hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px; border: none;")
        hint.setWordWrap(True)
        bl.addWidget(hint)

        btn_row = QWidget()
        btn_row.setStyleSheet("background: transparent; border: none;")
        brl = QHBoxLayout(btn_row)
        brl.setContentsMargins(0, 2, 0, 0)
        brl.setSpacing(4)

        def _btn(text: str, tip: str = "") -> QPushButton:
            b = QPushButton(text)
            b.setFixedHeight(22)
            b.setStyleSheet(
                f"QPushButton {{ background: {BG_SURFACE}; color: {ACCENT};"
                f"border: 1px solid {ACCENT}44; border-radius: 4px;"
                f"font-size: 10px; padding: 0 6px; }}"
                f"QPushButton:hover {{ background: {ACCENT}22; }}"
            )
            if tip:
                b.setToolTip(tip)
            return b

        btn_dgx = _btn("📂 Open on DGX",
                       "Opens ~/BridgeStaging in the DGX file manager")
        btn_dgx.clicked.connect(
            lambda: self.open_dgx_requested.emit(self._session_id)
        )
        brl.addWidget(btn_dgx)

        btn_local = _btn("📁 Open local prep",
                         "Open the converted files in Windows Explorer")
        btn_local.clicked.connect(self.open_prep_requested)
        brl.addWidget(btn_local)
        brl.addStretch()

        bl.addWidget(btn_row)


# ──────────────────────────────────────────────────────────────────────
# Job row (one per drop)
# ──────────────────────────────────────────────────────────────────────

class _JobRow(QWidget):
    def __init__(self, job: TransferJob, parent=None):
        super().__init__(parent)
        self._job = job
        self._file_rows: dict[str, _FileRow] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 4)
        root.setSpacing(2)

        # ── Header row ────────────────────────────────────────────────
        hdr = QWidget()
        hdr.setFixedHeight(30)
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.setSpacing(6)

        icon = QLabel("📦")
        icon.setFixedWidth(18)
        hl.addWidget(icon)

        count = len(job.items)
        noun  = "file" if count == 1 else "files"
        dest  = job.dgx_dest_dir.replace("~", "").rstrip("/").split("/")[-1] or "Desktop"
        lbl   = QLabel(f"{count} {noun} → {dest}")
        lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 12px; font-weight: 600;")
        hl.addWidget(lbl)
        hl.addStretch()

        self._job_bar = QProgressBar()
        self._job_bar.setFixedSize(80, 6)
        self._job_bar.setTextVisible(False)
        self._job_bar.setRange(0, count)
        self._job_bar.setValue(0)
        self._job_bar.setStyleSheet(
            f"QProgressBar {{ background: {BG_BASE}; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}"
        )
        hl.addWidget(self._job_bar)

        self._job_lbl = QLabel("queued")
        self._job_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
        self._job_lbl.setFixedWidth(60)
        hl.addWidget(self._job_lbl)

        root.addWidget(hdr)

        # ── File sub-rows ─────────────────────────────────────────────
        for item in job.items:
            row = _FileRow(item)
            root.addWidget(row)
            self._file_rows[item.item_id] = row

        # ── Bridge banner (shown when all files land in DGX staging) ──
        self._bridge_banner = _BridgeBanner(job)
        root.addWidget(self._bridge_banner)
        self._bridge_banner.hide()

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        root.addWidget(sep)

    # ── Slot helpers (called from panel) ──────────────────────────────

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
        self._job_bar.setValue(done)
        if done < total:
            self._job_lbl.setText(f"{done}/{total}")
        else:
            self._job_lbl.setText("done")

    def _recalc_job_state(self):
        items    = self._job.items
        statuses = [i.status for i in items]
        if all(s == "done" for s in statuses):
            self._job_lbl.setText("✅ done")
            self._job_lbl.setStyleSheet(f"color: {SUCCESS}; font-size: 10px;")
            self._job_bar.setValue(len(items))
            self._bridge_banner.hide()
        elif all(s in ("bridge", "done") for s in statuses) \
                and any(s == "bridge" for s in statuses):
            self._job_lbl.setText("🎯 in bridge")
            self._job_lbl.setStyleSheet(f"color: {ACCENT}; font-size: 10px;")
            self._job_bar.setValue(len(items))
            self._bridge_banner.show()
        elif any(s == "failed" for s in statuses):
            fc = sum(1 for s in statuses if s == "failed")
            self._job_lbl.setText(f"⚠ {fc} failed")
            self._job_lbl.setStyleSheet(f"color: {ERROR}; font-size: 10px;")
        elif any(s == "running" for s in statuses):
            rc = sum(1 for s in statuses if s == "running")
            self._job_lbl.setText(f"sending {rc}")


# ──────────────────────────────────────────────────────────────────────
# Transfer Queue Panel (the public widget)
# ──────────────────────────────────────────────────────────────────────

class TransferPanel(QWidget):
    """
    Sidebar panel.  Wire up:
      panel.set_connection(conn)
      panel.enqueue_drop(paths, dgx_dest_dir)   ← called from MainWindow on drop
    """

    def __init__(self, connection=None, parent=None):
        super().__init__(parent)
        self._conn:    Optional[object] = connection
        self._session: TransferSession  = TransferSession()
        self._workers: list[TransferWorker] = []
        self._job_rows: dict[str, _JobRow]  = {}

        self.setMinimumWidth(280)
        self.setMaximumWidth(360)
        self._build_ui()

    def set_connection(self, conn):
        self._conn = conn

    # ── UI ────────────────────────────────────────────────────────────

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
        btn_close = QPushButton("✕")
        btn_close.setFlat(True)
        btn_close.setFixedSize(28, 28)
        btn_close.setStyleSheet(f"color: {TEXT_DIM}; font-size: 14px;")
        btn_close.clicked.connect(self.hide)
        hl.addWidget(btn_close)
        root.addWidget(hdr)

        # Scroll area for job rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background: {BG_DEEP}; }}"
        )
        self._jobs_widget = QWidget()
        self._jobs_widget.setStyleSheet(f"background: {BG_DEEP};")
        self._jobs_layout = QVBoxLayout(self._jobs_widget)
        self._jobs_layout.setContentsMargins(0, 0, 0, 0)
        self._jobs_layout.setSpacing(0)
        self._jobs_layout.addStretch()

        # Empty state label
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

        btn_stage = _ftr_btn("📁 Staging", "Open PC staging folder")
        btn_stage.clicked.connect(self._session.open_stage_dir)
        fl.addWidget(btn_stage)

        btn_log = _ftr_btn("🗒 Log", "Open session transfer log")
        btn_log.clicked.connect(self._session.open_log)
        fl.addWidget(btn_log)

        fl.addStretch()

        btn_clear = _ftr_btn("Clear done")
        btn_clear.clicked.connect(self._clear_done)
        fl.addWidget(btn_clear)

        root.addWidget(ftr)

    # ── Public API ────────────────────────────────────────────────────

    def enqueue_drop(self, paths: list[str], dgx_dest_dir: str = ""):
        """
        Called from MainWindow when files are dropped onto the DGX canvas.
        Builds a job and starts it immediately.
        """
        if not self._conn:
            log.warning("enqueue_drop: no connection")
            return

        job = self._session.make_job(paths, dgx_dest_dir)
        if not job.items:
            log.warning("enqueue_drop: no transferable items in drop")
            return

        self._empty_lbl.hide()
        row = _JobRow(job)
        # Insert before the stretch at the end
        count = self._jobs_layout.count()
        self._jobs_layout.insertWidget(count - 1, row)
        self._job_rows[job.job_id] = row

        worker = TransferWorker(job, self._conn, self._session)
        worker.item_progress.connect(
            lambda iid, done, total, r=row: r.on_item_progress(iid, done, total)
        )
        worker.item_status.connect(
            lambda iid, st, msg, r=row: r.on_item_status(iid, st, msg)
        )
        worker.job_progress.connect(
            lambda done, total, r=row: r.on_job_progress(done, total)
        )
        worker.job_bridge_ready.connect(self._on_job_bridge_ready)
        worker.job_complete.connect(self._on_job_complete)

        # Wire bridge banner buttons
        row._bridge_banner.open_dgx_requested.connect(self._open_bridge_folder_on_dgx)
        row._bridge_banner.open_prep_requested.connect(self._session.open_prep_dir)

        self._workers.append(worker)
        worker.start()

        log.info("Started job %s: %d items → %s",
                 job.job_id, len(job.items), dgx_dest_dir or "~/Desktop")

    def enqueue_paths(self, paths: list[str], dgx_dest: str = ""):
        """Convenience alias (called from sidebar drop zone)."""
        self.enqueue_drop(paths, dgx_dest)

    # ── Internal slots ────────────────────────────────────────────────

    @pyqtSlot(str, int, int)
    def _on_job_complete(self, job_id: str, ok: int, fail: int):
        log.info("Job %s finished: %d ok, %d failed", job_id, ok, fail)
        # Clean up completed workers
        self._workers = [w for w in self._workers if w.isRunning()]

    @pyqtSlot(str, str)
    def _on_job_bridge_ready(self, job_id: str, session_id: str):
        """Called when all items in a job reach 'bridge' status."""
        log.info("Job %s bridge-ready in session %s", job_id, session_id)
        # Automatically open the DGX staging folder so the user can drag files
        self._open_bridge_folder_on_dgx(session_id)

    def _open_bridge_folder_on_dgx(self, session_id: str):
        """Send RPC to DGX to open the bridge staging folder in its file manager."""
        if not self._conn:
            log.warning("_open_bridge_folder_on_dgx: no connection")
            return
        try:
            self._conn.rpc({"type": "open_bridge_folder", "session_id": session_id})
        except Exception as exc:  # noqa: BLE001
            log.warning("open_bridge_folder RPC failed: %s", exc)

    def _clear_done(self):
        for job_id, row in list(self._job_rows.items()):
            if all(i.status in ("done", "failed")
                   for i in self._job_rows[job_id]._job.items):
                row.setParent(None)
                row.deleteLater()
                del self._job_rows[job_id]
        if not self._job_rows:
            self._empty_lbl.show()
