"""
pc-application/src/transfer/file_converter.py
Pre-transfer file preparation: CRLF stripping, temp file wrangling.
"""

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from .file_analyzer import FileInfo


class FileConverter:
    """
    Optionally converts a file before upload.
    Returns the path to send (may be a temp file) and cleanup instructions.
    """

    def __init__(self):
        self._tmp_files: list[Path] = []

    def prepare(
        self,
        info: FileInfo,
        convert_crlf: bool = True,
    ) -> tuple[Path, bool]:
        """
        Return (path_to_send, is_temp).
        If is_temp is True the caller must call cleanup() after transfer.
        """
        if not info.is_readable:
            return info.path, False

        # Only convert text files with CRLF
        if convert_crlf and info.transfer_hint == "text" and info.has_crlf:
            return self._strip_crlf(info.path)

        return info.path, False

    def _strip_crlf(self, path: Path) -> tuple[Path, bool]:
        """Create a temp LF-only copy. Returns (tmp_path, True)."""
        try:
            fd, tmp = tempfile.mkstemp(suffix=path.suffix, prefix="dgx_tx_")
            tmp_path = Path(tmp)
            self._tmp_files.append(tmp_path)
            with open(path, "rb") as src, open(fd, "wb") as dst:
                for chunk in iter(lambda: src.read(65536), b""):
                    dst.write(chunk.replace(b"\r\n", b"\n"))
            return tmp_path, True
        except OSError:
            return path, False

    def cleanup(self):
        """Remove all temporary files created by this converter."""
        for p in self._tmp_files:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        self._tmp_files.clear()

    def get_remote_metadata(self, info: FileInfo) -> dict:
        """
        Build the metadata dict sent to DGX alongside each file.
        DGX uses this to set permissions and placement.
        """
        return {
            "name":       info.name,
            "size":       info.size,
            "sha256":     info.sha256,
            "mime_type":  info.mime_type,
            "is_text":    info.transfer_hint == "text",
            "had_crlf":   info.has_crlf,
            "permissions": _suggest_permissions(info),
        }


def _suggest_permissions(info: FileInfo) -> str:
    """Return chmod octal string based on file type."""
    if info.mime_type in ("application/elf", "application/exe") \
            or info.path.suffix in (".sh", ".bash", ".zsh", ".py", ".pl", ".rb"):
        return "0755"
    if info.transfer_hint == "text":
        return "0644"
    return "0644"
