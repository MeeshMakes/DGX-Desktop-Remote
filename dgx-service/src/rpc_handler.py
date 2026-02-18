"""
dgx-service/src/rpc_handler.py
Handles all JSON RPC requests from the PC client (control channel).
"""

import hashlib
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server import DGXService

log = logging.getLogger(__name__)

TRANSFER_ROOT = Path.home() / "Desktop" / "PC-Transfer"
_VALID_FOLDERS = {"inbox", "outbox", "staging", "archive"}


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class RPCHandler:
    """
    Dispatches incoming JSON RPC messages and returns response dicts.
    Each public handle_* method corresponds to a message type.
    """

    def __init__(self, service: "DGXService"):
        self._svc = service

    def dispatch(self, msg: dict) -> dict:
        t = msg.get("type", "")
        handler = getattr(self, f"handle_{t.replace('-', '_')}", None)
        if handler is None:
            return {"ok": False, "error": f"Unknown RPC type: {t}"}
        try:
            return handler(msg)
        except Exception as e:
            log.exception("RPC handler error for '%s'", t)
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    def handle_ping(self, msg: dict) -> dict:
        return {"ok": True, "type": "pong"}

    def handle_hello(self, msg: dict) -> dict:
        """
        Opening handshake from PC client.
        Returns system info so the PC can set up resolution, hostname etc.
        in one round-trip instead of a separate get_system_info call.
        """
        w, h = self._svc.resolution_monitor.current
        import shutil, socket as _sock, platform
        du = shutil.disk_usage(__import__('pathlib').Path.home())
        return {
            "ok":           True,
            "type":         "hello_ack",
            "hostname":     _sock.gethostname(),
            "os":           f"{platform.system()} {platform.release()}",
            "width":        w,
            "height":       h,
            "refresh_hz":   self._svc.capture._fps,
            "disk_free_gb": round(du.free / 1e9, 1),
            "gpus":         self._get_gpu_info(),
            "display":      {"width": w, "height": h, "refresh_hz": self._svc.capture._fps},
        }

    def handle_get_system_info(self, msg: dict) -> dict:
        w, h = self._svc.resolution_monitor.current
        du    = shutil.disk_usage(Path.home())
        disk_free_gb = round(du.free / 1e9, 1)

        hostname = socket.gethostname()
        try:
            os_name = f"{platform.system()} {platform.release()}"
        except Exception:
            os_name = platform.system()

        gpus = self._get_gpu_info()
        return {
            "ok":         True,
            "hostname":   hostname,
            "os":         os_name,
            "width":      w,
            "height":     h,
            "disk_free_gb": disk_free_gb,
            "gpus":       gpus,
        }

    def handle_get_resolution(self, msg: dict) -> dict:
        w, h = self._svc.resolution_monitor.current
        return {"ok": True, "width": w, "height": h}

    def handle_set_capture_params(self, msg: dict) -> dict:
        fps     = msg.get("fps")
        quality = msg.get("quality")
        self._svc.capture.set_params(fps=fps, quality=quality)
        return {"ok": True}

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def handle_list_files(self, msg: dict) -> dict:
        folder = msg.get("folder", "inbox")
        if folder not in _VALID_FOLDERS:
            return {"ok": False, "error": "Invalid folder"}
        path = TRANSFER_ROOT / folder
        path.mkdir(parents=True, exist_ok=True)
        files = []
        for f in sorted(path.iterdir()):
            if f.is_file():
                sz = f.stat().st_size
                files.append({
                    "name":       f.name,
                    "size":       sz,
                    "size_human": _human_size(sz),
                })
        return {"ok": True, "files": files}

    def handle_delete_file(self, msg: dict) -> dict:
        folder   = msg.get("folder", "inbox")
        filename = msg.get("filename", "")
        if folder not in _VALID_FOLDERS or not filename:
            return {"ok": False, "error": "Invalid folder or filename"}
        target = TRANSFER_ROOT / folder / Path(filename).name
        if not target.exists():
            return {"ok": False, "error": "File not found"}
        try:
            target.unlink()
            return {"ok": True}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def handle_verify_file(self, msg: dict) -> dict:
        folder   = msg.get("folder", "inbox")
        filename = msg.get("filename", "")
        expected = msg.get("sha256", "")
        if folder not in _VALID_FOLDERS or not filename:
            return {"ok": False, "error": "Bad params"}
        target = TRANSFER_ROOT / folder / Path(filename).name
        if not target.exists():
            return {"ok": False, "error": "File not found"}
        sha = hashlib.sha256()
        with open(target, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                sha.update(chunk)
        match = (sha.hexdigest() == expected)
        return {"ok": True, "match": match, "sha256": sha.hexdigest()}

    # ------------------------------------------------------------------
    # Input (dispatched from input channel, but RPC versions useful too)
    # ------------------------------------------------------------------

    def handle_type_text(self, msg: dict) -> dict:
        text = msg.get("text", "")
        if text:
            self._svc.input_handler.type_text(text)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Service management
    # ------------------------------------------------------------------

    def handle_get_service_status(self, msg: dict) -> dict:
        w, h = self._svc.resolution_monitor.current
        return {
            "ok":      True,
            "capture": self._svc.capture.running,
            "width":   w,
            "height":  h,
            "fps":     self._svc.capture._fps,
            "quality": self._svc.capture._quality,
        }

    def handle_shutdown(self, msg: dict) -> dict:
        """Graceful service shutdown (called from DGX manager GUI)."""
        import threading
        threading.Thread(
            target=lambda: (__import__("time").sleep(0.5), self._svc.stop()),
            daemon=True,
        ).start()
        return {"ok": True}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_gpu_info(self) -> list[dict]:
        try:
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=name,memory.total,memory.free,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL,
                timeout=4,
            ).decode()
            gpus = []
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 4:
                    gpus.append({
                        "name":             parts[0],
                        "memory_total_mb":  int(parts[1]),
                        "memory_free_mb":   int(parts[2]),
                        "utilization_pct":  int(parts[3]),
                    })
            return gpus
        except Exception:
            return []
