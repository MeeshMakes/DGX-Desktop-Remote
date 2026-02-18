"""
pc-application/src/transfer/transfer_session.py

Session-scoped transfer coordinator.

Responsibilities:
  - Generate a unique SessionId per app launch (reset = new session).
  - Own the PC-side staging directory.
  - Accumulate a JSONL transfer log for the session lifetime.
  - Provide a factory for TransferJob objects.

Staging layout
  PC  : %LOCALAPPDATA%/DGX-Desktop-Remote/staging/<session_id>/<file>
  DGX : ~/BridgeStaging/<session_id>/<file>
"""

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("pc.transfer")

# ── PC-side paths ─────────────────────────────────────────────────────
_APP_DATA   = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
_BASE_DIR   = _APP_DATA / "DGX-Desktop-Remote"
_STAGE_ROOT = _BASE_DIR / "staging"
_LOG_DIR    = _BASE_DIR / "logs"
for _d in (_STAGE_ROOT, _LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Repo-local bridge-prep folder (visible next to the source code)
_REPO_ROOT   = Path(__file__).parents[3]   # DGX-Desktop-Remote/
_BRIDGE_PREP = _REPO_ROOT / "bridge-prep"
_BRIDGE_PREP.mkdir(parents=True, exist_ok=True)

# ── DGX-side paths ────────────────────────────────────────────────────
DGX_STAGE_ROOT = "BridgeStaging"   # relative to DGX home (~/)
DGX_DEFAULT_DEST = "Desktop"        # default drop destination on DGX


# ──────────────────────────────────────────────────────────────────────
# Transfer log entry
# ──────────────────────────────────────────────────────────────────────

@dataclass
class LogEntry:
    session_id:    str
    item_id:       str          # UUID per file
    src_path:      str          # absolute Windows path
    dst_path:      str          # absolute DGX path
    file_ext:      str
    size_bytes:    int
    ts_queued:     float = 0.0
    ts_started:    float = 0.0
    ts_finished:   float = 0.0
    method:        str   = "file"   # "file" | "folder_recursive"
    recursive_count: int = 1
    sha256_src:    str   = ""
    sha256_dst:    str   = ""
    integrity_ok:  Optional[bool] = None
    status:        str   = "queued"  # queued|running|verifying|done|failed
    error:         str   = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ──────────────────────────────────────────────────────────────────────
# Single file to transfer
# ──────────────────────────────────────────────────────────────────────

@dataclass
class TransferItem:
    local_path:  Path
    dgx_dest:    str          # absolute DGX destination path (e.g. ~/Desktop/file.txt)
    item_id:     str = field(default_factory=lambda: str(uuid.uuid4()))
    dgx_name:    str = ""     # filename on DGX (may differ after conversion, e.g. .bat→.sh)
    # Runtime
    status:      str   = "queued"
    error_msg:   str   = ""
    bytes_done:  int   = 0
    bytes_total: int   = 0
    sha256_src:  str   = ""
    sha256_dst:  str   = ""
    integrity_ok: Optional[bool] = None


# ──────────────────────────────────────────────────────────────────────
# One user drop = one job (may contain many files)
# ──────────────────────────────────────────────────────────────────────

@dataclass
class TransferJob:
    job_id:       str
    session_id:   str
    items:        list[TransferItem]
    dgx_dest_dir: str          # directory portion of destination on DGX
    # Runtime
    status:       str   = "queued"
    created_at:   float = field(default_factory=time.monotonic)


# ──────────────────────────────────────────────────────────────────────
# Session
# ──────────────────────────────────────────────────────────────────────

class TransferSession:
    """
    Singleton-per-app-launch.  Created in main_window, passed to the panel.
    Call reset() to start a fresh session (new staging dir + log).
    """

    def __init__(self):
        self._session_id: str = ""
        self._stage_dir:  Optional[Path] = None
        self._log_path:   Optional[Path] = None
        self.reset()

    # ── Lifecycle ─────────────────────────────────────────────────────

    def reset(self):
        """Start a new session: new ID, new staging directory, new log."""
        self._session_id = uuid.uuid4().hex[:12]
        self._stage_dir  = _STAGE_ROOT / self._session_id
        self._stage_dir.mkdir(parents=True, exist_ok=True)
        self._log_path   = _LOG_DIR / f"transfer-{self._session_id}.jsonl"
        log.info("Transfer session started: %s", self._session_id)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def stage_dir(self) -> Path:
        return self._stage_dir

    @property
    def dgx_stage_path(self) -> str:
        """Path on DGX where staged files land (relative to home)."""
        return f"{DGX_STAGE_ROOT}/{self._session_id}"

    @property
    def local_prep_path(self) -> Path:
        """Repo-local bridge-prep directory for this session.

        Files are copied and converted here so you can inspect the
        exact bytes that will land on the DGX before transfer starts.
        """
        path = _BRIDGE_PREP / self._session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def open_prep_dir(self):
        """Open the local bridge-prep directory in Explorer."""
        import subprocess
        subprocess.Popen(["explorer", str(self.local_prep_path)])

    # ── Job factory ───────────────────────────────────────────────────

    def make_job(self, paths: list[str], dgx_dest_dir: str = "") -> TransferJob:
        """
        Expand paths (files + folders recursively) into TransferItems.
        dgx_dest_dir: absolute DGX path for drop destination.
                      Falls back to ~/Desktop.
        """
        if not dgx_dest_dir:
            dgx_dest_dir = f"~/{DGX_DEFAULT_DEST}"

        items: list[TransferItem] = []
        for raw in paths:
            p = Path(raw)
            if p.is_file():
                dst = _dgx_dest_for(p, dgx_dest_dir)
                items.append(TransferItem(local_path=p, dgx_dest=dst))
            elif p.is_dir():
                for child in _walk_dir(p):
                    rel  = child.relative_to(p.parent)
                    dst  = f"{dgx_dest_dir}/{_safe_linux_path(str(rel))}"
                    items.append(TransferItem(local_path=child, dgx_dest=dst))

        job = TransferJob(
            job_id=uuid.uuid4().hex[:8],
            session_id=self._session_id,
            items=items,
            dgx_dest_dir=dgx_dest_dir,
        )
        return job

    # ── Logging ───────────────────────────────────────────────────────

    def log_entry(self, entry: LogEntry):
        """Append a log entry to the session JSONL file."""
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")
        except OSError as e:
            log.warning("Could not write transfer log: %s", e)

    def open_stage_dir(self):
        """Open the PC staging dir in Explorer."""
        import subprocess
        subprocess.Popen(["explorer", str(self._stage_dir)])

    def open_log(self):
        """Open the session log in Notepad."""
        import subprocess
        if self._log_path and self._log_path.exists():
            subprocess.Popen(["notepad", str(self._log_path)])


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _walk_dir(root: Path) -> list[Path]:
    """Recursively collect all files under root."""
    files = []
    for item in root.rglob("*"):
        if item.is_file():
            files.append(item)
    return sorted(files)


def _safe_linux_path(windows_rel: str) -> str:
    """Convert a Windows relative path to a safe Linux path segment."""
    # Replace backslashes, strip illegal chars
    p = windows_rel.replace("\\", "/")
    # Remove chars illegal on Linux filenames (keep alphanumeric, ., -, _, /, space)
    import re
    p = re.sub(r'[<>:"|?*\x00-\x1f]', "_", p)
    return p


def _dgx_dest_for(local: Path, dgx_dir: str) -> str:
    """Build the full DGX destination path for a single file."""
    return f"{dgx_dir}/{_safe_linux_path(local.name)}"


def sha256_file(path: Path) -> str:
    """Compute SHA-256 of a local file."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""
