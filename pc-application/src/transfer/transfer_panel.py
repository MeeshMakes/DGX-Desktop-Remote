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
  |  | +------------------------+ |  |
  |  | | DGX Staging | Local    | |  |  <- tabs on bridge-ready
  |  | | [icon] build.sh        | |  |
  |  | +------------------------+ |  |
  |  +----------------------------+  |
  +----------------------------------+
  |  Staging   Log   [Clear done]    |  <- footer
  +----------------------------------+
"""

import logging
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QProgressBar, QSizePolicy, QListWidget,
    QListWidgetItem, QTabWidget,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot

from theme import (
    ACCENT, SUCCESS, ERROR, WARNING, TEXT_DIM, TEXT_MAIN,
    BG_RAISED, BG_SURFACE, BG_BASE, BORDER, BG_DEEP
)
from .transfer_session import TransferSession, TransferJob, TransferItem
from .transfer_worker  import TransferWorker

log = logging.getLogger("pc.transfer.panel")


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _file_icon(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {
        ".py": "D", ".sh": "E", ".bash": "E",
        ".c": "T", ".cpp": "T", ".h": "T",
        ".js": "J", ".ts": "J", ".json": "C",
        ".html": "W", ".xml": "C", ".yaml": "C", ".toml": "C",
        ".jpg": "I", ".jpeg": "I", ".png": "I",
        ".gif": "I", ".bmp": "I", ".svg": "I",
        ".pdf": "P", ".md": "N", ".txt": "N",
        ".zip": "Z", ".tar": "Z", ".gz": "Z",
        ".mp4": "V", ".mp3": "M",
    }.get(ext, "F")


_ICON_EMOJI = {
    "D": "d", "E": "e", "T": "t", "J": "j", "C": "c",
    "W": "w", "I": "i", "P": "p", "N": "n", "Z": "z",
    "V": "v", "M": "m", "F": "f",
}


def _file_icon_char(name: str) -> str:
    ext = Path(name).suffix.lower()
    icons = {
        ".py": "\U0001f40d", ".sh": "\u26a1", ".bash": "\u26a1",
        ".c": "\U0001f527", ".cpp": "\U0001f527", ".h": "\U0001f527",
        ".js": "\U0001f7e8", ".ts": "\U0001f7e6", ".json": "\U0001f4cb",
        ".html": "\U0001f310", ".xml": "\U0001f4cb", ".yaml": "\U0001f4cb", ".toml": "\U0001f4cb",
        ".jpg": "\U0001f5bc", ".jpeg": "\U0001f5bc", ".png": "\U0001f5bc",
        ".gif": "\U0001f5bc", ".bmp": "\U0001f5bc", ".svg": "\U0001f5bc",
        ".pdf": "\U0001f4d5", ".md": "\U0001f4dd", ".txt": "\U0001f4dd",
        ".zip": "\U0001f4e6", ".tar": "\U0001f4e6", ".gz": "\U0001f4e6",
        ".mp4": "\U0001f3ac", ".mp3": "\U0001f3b5",
    }
    return icons.get(ext, "\U0001f4c4")


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# -----------------------------------------------------------------------
# Background thread: list files in DGX BridgeStaging/<session_id>/
# -----------------------------------------------------------------------

class _ListBridgeStagingThread(QThread):
    result = pyqtSignal(list)   # list[dict]

    def __init__(self, conn, session_id: str, parent=None):
        super().__init__(parent)
        self._conn       = conn
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


# -----------------------------------------------------------------------
# Single file row inside a job (no per-file progress bar)
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

        display_name = (item.dgx_name
                        if item.dgx_name and item.dgx_name != item.local_path.name
                        else item.local_path.name)
        self._name = QLabel(display_name)
        self._name.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 11px;")
        self._name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        l.addWidget(self._name, 1)

        self._msg = QLabel("")
        self._msg.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
        self._msg.setFixedWidth(70)
        self._msg.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        l.addWidget(self._msg)

        self._btn_retry = QPushButton("\u21ba")
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
            pct = int(done / total * 100)
            self._msg.setText(f"{pct}%")

    def update_status(self, status: str, msg: str = ""):
        self._icon.setText(self._STATUS_ICON.get(status, self._STATUS_ICON["queued"]))
        self._icon.setStyleSheet("font-size: 11px; " + self._STATUS_STYLE.get(status, ""))

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
# Bridge folder tabs - DGX Staging / Local Prep file browsers
# Shown inside a job row once files reach bridge state.
# -----------------------------------------------------------------------

def _list_style() -> str:
    return (
        f"QListWidget {{ background: {BG_BASE}; border: none; outline: none;"
        f"  color: {TEXT_MAIN}; font-size: 11px; }}"
        f"QListWidget::item {{ padding: 2px 0; }}"
        f"QListWidget::item:selected {{ background: {ACCENT}22; color: {TEXT_MAIN}; }}"
        f"QListWidget::item:hover {{ background: {BG_SURFACE}; }}"
    )


class _BridgeTabs(QWidget):
    """
    Two-tab widget embedded in the job row when files reach bridge state.

    DGX tab  - live file list from ~/BridgeStaging/<session_id>/ on the DGX.
    Local tab - file list from bridge-prep/<session_id>/ on the PC.
    """

    def __init__(self, session_id: str, local_path: Path, parent=None):
        super().__init__(parent)
        self._session_id = session_id
        self._local_path = local_path
        self._conn       = None
        self._list_thread: Optional[_ListBridgeStagingThread] = None

        self._build_ui()
        self.setVisible(False)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(0)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 4px;"
            f"  background: {BG_BASE}; }}"
            f"QTabBar::tab {{ padding: 4px 10px; font-size: 10px;"
            f"  color: {TEXT_DIM}; background: {BG_RAISED}; border: 1px solid {BORDER};"
            f"  border-bottom: none; border-radius: 3px 3px 0 0; margin-right: 2px; }}"
            f"QTabBar::tab:selected {{ color: {TEXT_MAIN}; background: {ACCENT}22;"
            f"  border-bottom: 1px solid {ACCENT}; }}"
        )

        # DGX Staging tab
        dgx_page = QWidget()
        dv = QVBoxLayout(dgx_page)
        dv.setContentsMargins(0, 0, 0, 0)
        dv.setSpacing(0)

        dgx_tb = QWidget()
        dgx_tb.setFixedHeight(22)
        dt = QHBoxLayout(dgx_tb)
        dt.setContentsMargins(4, 0, 4, 0)
        dt.setSpacing(4)
        dgx_hint = QLabel("~/BridgeStaging/")
        dgx_hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9px;")
        dt.addWidget(dgx_hint)
        dt.addStretch()
        btn_rdgx = QPushButton("\u27f3")
        btn_rdgx.setFixedSize(18, 18)
        btn_rdgx.setStyleSheet(
            f"background: transparent; color: {TEXT_DIM}; font-size: 11px; border: none;"
        )
        btn_rdgx.clicked.connect(self._refresh_dgx)
        dt.addWidget(btn_rdgx)
        dv.addWidget(dgx_tb)

        self._dgx_list = QListWidget()
        self._dgx_list.setFixedHeight(110)
        self._dgx_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._dgx_list.setStyleSheet(_list_style())
        dv.addWidget(self._dgx_list)

        tabs.addTab(dgx_page, "DGX")

        # Local Prep tab
        local_page = QWidget()
        lv = QVBoxLayout(local_page)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)

        local_tb = QWidget()
        local_tb.setFixedHeight(22)
        lt = QHBoxLayout(local_tb)
        lt.setContentsMargins(4, 0, 4, 0)
        lt.setSpacing(4)
        local_hint = QLabel(str(self._local_path))
        local_hint.setStyleSheet(f"color: {TEXT_DIM}; font-size: 9px;")
        local_hint.setToolTip(str(self._local_path))
        lt.addWidget(local_hint)
        lt.addStretch()
        btn_rloc = QPushButton("\u27f3")
        btn_rloc.setFixedSize(18, 18)
        btn_rloc.setStyleSheet(
            f"background: transparent; color: {TEXT_DIM}; font-size: 11px; border: none;"
        )
        btn_rloc.clicked.connect(self._refresh_local)
        lt.addWidget(btn_rloc)
        lv.addWidget(local_tb)

        self._local_list = QListWidget()
        self._local_list.setFixedHeight(110)
        self._local_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._local_list.setStyleSheet(_list_style())
        lv.addWidget(self._local_list)

        tabs.addTab(local_page, "Local")

        root.addWidget(tabs)

    def activate(self, conn):
        """Show the widget and populate both file lists."""
        self._conn = conn
        self.setVisible(True)
        self._refresh_local()
        self._refresh_dgx()

    def _refresh_local(self):
        self._local_list.clear()
        if not self._local_path.exists():
            self._local_list.addItem(QListWidgetItem("  (folder not found)"))
            return
        try:
            files = sorted(self._local_path.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            self._local_list.addItem(QListWidgetItem("  (cannot read folder)"))
            return
        for f in files:
            if f.is_file():
                icon = _file_icon_char(f.name)
                sz   = _human_size(f.stat().st_size)
                item = QListWidgetItem(f"  {icon}  {f.name}  {sz}")
                item.setToolTip(str(f))
                self._local_list.addItem(item)
        if self._local_list.count() == 0:
            self._local_list.addItem(QListWidgetItem("  (empty)"))

    def _refresh_dgx(self):
        if not self._conn:
            return
        self._dgx_list.clear()
        self._dgx_list.addItem(QListWidgetItem("  loading..."))
        self._list_thread = _ListBridgeStagingThread(
            self._conn, self._session_id, parent=self
        )
        self._list_thread.result.connect(self._on_dgx_list)
        self._list_thread.start()

    @pyqtSlot(list)
    def _on_dgx_list(self, files: list):
        try:
            self._dgx_list.clear()
        except RuntimeError:
            return
        for info in files:
            name = info.get("name", "?")
            sz   = info.get("size_human", "")
            icon = _file_icon_char(name)
            self._dgx_list.addItem(QListWidgetItem(f"  {icon}  {name}  {sz}"))
        if self._dgx_list.count() == 0:
            self._dgx_list.addItem(QListWidgetItem("  (empty)"))


# -----------------------------------------------------------------------
# Job row (one per drop)
# -----------------------------------------------------------------------

class _JobRow(QWidget):
    def __init__(self, job: TransferJob, session: TransferSession, parent=None):
        super().__init__(parent)
        self._job     = job
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
        noun  = "file" if count == 1 else "files"
        dest  = job.dgx_dest_dir.replace("~", "").rstrip("/").split("/")[-1] or "Desktop"
        lbl   = QLabel(f"{count} {noun} \u2192 {dest}")
        lbl.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 12px; font-weight: 600;")
        hl.addWidget(lbl)
        hl.addStretch()

        self._job_lbl = QLabel("queued")
        self._job_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 10px;")
        hl.addWidget(self._job_lbl)

        root.addWidget(hdr)

        # File sub-rows
        for item in job.items:
            row = _FileRow(item)
            root.addWidget(row)
            self._file_rows[item.item_id] = row

        # Bridge folder tabs (hidden until bridge state)
        self._bridge_tabs = _BridgeTabs(
            session_id = job.session_id,
            local_path = session.local_prep_path,
        )
        root.addWidget(self._bridge_tabs)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        root.addWidget(sep)

    # Slot helpers called from panel

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
        """Activate the bridge tab view with live folder listings."""
        self._bridge_tabs.activate(conn)

    def _recalc_job_state(self):
        items    = self._job.items
        statuses = [i.status for i in items]
        if all(s == "done" for s in statuses):
            self._job_lbl.setText("\u2705 done")
            self._job_lbl.setStyleSheet(f"color: {SUCCESS}; font-size: 10px;")
        elif all(s in ("bridge", "done") for s in statuses) \
                and any(s == "bridge" for s in statuses):
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
# Transfer Queue Panel (the public widget)
# -----------------------------------------------------------------------

class TransferPanel(QWidget):
    """
    Sidebar panel.  Wire up:
      panel.set_connection(conn)
      panel.enqueue_drop(paths, dgx_dest_dir)  <- called from MainWindow on drop
    """

    def __init__(self, connection=None, parent=None):
        super().__init__(parent)
        self._conn:    Optional[object] = connection
        self._session: TransferSession  = TransferSession()
        self._workers: list[TransferWorker] = []
        self._job_rows: dict[str, _JobRow]  = {}

        # Global progress tracking: item_id -> (done_bytes, total_bytes)
        self._item_progress: dict[str, tuple[int, int]] = {}

        self.setMinimumWidth(280)
        self.setMaximumWidth(380)
        self._build_ui()

    def set_connection(self, conn):
        self._conn = conn

    # UI

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
        title.setStyleSheet(f"color: {TEXT_MAIN}; font-weight: 700; font-size: 13px;")
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

        # Global progress bar (3px, hidden when idle)
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
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {BG_DEEP}; }}")

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

    # Public API

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

        log.info("Started job %s: %d items -> %s",
                 job.job_id, len(job.items), dgx_dest_dir or "~/Desktop")

    def enqueue_paths(self, paths: list[str], dgx_dest: str = ""):
        self.enqueue_drop(paths, dgx_dest)

    # Global progress bar

    def _update_global_progress(self, item_id: str, done: int, total: int):
        self._item_progress[item_id] = (done, total)
        total_b = sum(t for _, t in self._item_progress.values())
        done_b  = sum(d for d, _ in self._item_progress.values())
        if total_b > 0:
            self._global_bar.setVisible(True)
            self._global_bar.setValue(int(done_b * 100 / total_b))

    def _on_item_status(self, item_id: str, status: str, msg: str, row: _JobRow):
        row.on_item_status(item_id, status, msg)
        if status in ("done", "failed", "bridge"):
            self._item_progress.pop(item_id, None)
            self._recalc_global_bar()

    def _recalc_global_bar(self):
        if not self._item_progress and not any(w.isRunning() for w in self._workers):
            self._global_bar.setVisible(False)
            self._global_bar.setValue(0)
            self._activity_lbl.setText("")

    # Internal slots

    @pyqtSlot(str, int, int)
    def _on_job_complete(self, job_id: str, ok: int, fail: int):
        log.info("Job %s finished: %d ok, %d failed", job_id, ok, fail)
        self._workers = [w for w in self._workers if w.isRunning()]
        self._recalc_global_bar()

    @pyqtSlot(str, str)
    def _on_job_bridge_ready(self, job_id: str, session_id: str):
        """Files are in DGX staging - activate the in-panel folder tabs."""
        log.info("Job %s bridge-ready (session %s)", job_id, session_id)
        row = self._job_rows.get(job_id)
        if row:
            row.show_bridge(self._conn)
        self._recalc_global_bar()

    def _clear_done(self):
        for job_id, row in list(self._job_rows.items()):
            if all(i.status in ("done", "failed")
                   for i in self._job_rows[job_id]._job.items):
                row.setParent(None)
                row.deleteLater()
                del self._job_rows[job_id]
        if not self._job_rows:
            self._empty_lbl.show()
