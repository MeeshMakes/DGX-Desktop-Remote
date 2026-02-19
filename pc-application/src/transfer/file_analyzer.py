"""
pc-application/src/transfer/file_analyzer.py
Magic-byte file analysis, SHA-256 checksums, CRLF detection.
"""

import hashlib
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────────────────────────────
# Magic byte signatures → (mime_type, transfer_note)
# ──────────────────────────────────────────────────────────────────────
_MAGIC: list[tuple[bytes, str, str]] = [
    # offset 0
    (b"\x89PNG\r\n\x1a\n",   "image/png",       "binary"),
    (b"\xff\xd8\xff",        "image/jpeg",      "binary"),
    (b"GIF87a",              "image/gif",       "binary"),
    (b"GIF89a",              "image/gif",       "binary"),
    (b"BM",                  "image/bmp",       "binary"),
    (b"RIFF",                "audio/video",     "binary"),
    (b"ID3",                 "audio/mpeg",      "binary"),
    (b"\xff\xfb",            "audio/mpeg",      "binary"),
    (b"fLaC",                "audio/flac",      "binary"),
    (b"OggS",                "audio/ogg",       "binary"),
    (b"\x1f\x8b",            "application/gzip","binary"),
    (b"BZh",                 "application/bzip2","binary"),
    (b"PK\x03\x04",          "application/zip", "binary"),
    (b"7z\xbc\xaf\x27\x1c",  "application/7z",  "binary"),
    (b"\xfd7zXZ\x00",        "application/xz",  "binary"),
    (b"%PDF",                "application/pdf", "binary"),
    (b"\xcaFEBABE",          "application/java","binary"),
    (b"\x7fELF",             "application/elf", "binary"),
    (b"MZ",                  "application/exe", "binary"),
]

_TEXT_EXTENSIONS = {
    ".txt", ".py", ".sh", ".bash", ".zsh", ".md", ".rst",
    ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".conf", ".env", ".log", ".xml", ".html", ".htm",
    ".css", ".js", ".ts", ".jsx", ".tsx", ".vue", ".svelte",
    ".c", ".h", ".cpp", ".hpp", ".cxx", ".cc", ".cs", ".java",
    ".go", ".rs", ".rb", ".php", ".pl", ".r", ".m", ".tex",
    ".sql", ".Makefile", ".dockerfile", ".gitignore",
    # Windows scripts — treated as text, converted to .sh on DGX
    ".bat", ".cmd", ".ps1", ".ps1xml", ".psm1", ".psd1",
    # Registry / INF / config formats
    ".reg", ".inf", ".nfo",
}

_BINARY_EXTENSIONS = {
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin",
    ".onnx", ".gguf", ".ggml",
}


@dataclass
class FileInfo:
    path:         Path
    name:         str
    size:         int
    mime_type:    str    = "application/octet-stream"
    transfer_hint: str   = "binary"    # "binary" | "text"
    has_crlf:     bool   = False
    sha256:       str    = ""
    is_readable:  bool   = True
    error:        Optional[str] = None

    # Computed in analyze()
    size_human:   str    = field(init=False, default="")

    def __post_init__(self):
        self.size_human = _human_size(self.size)


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:,.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def analyze_file(path: Path, compute_sha256: bool = True) -> FileInfo:
    """
    Detect MIME type, text/binary hint, CRLF presence, and SHA-256.
    Fast for large files: reads header once, then streams for sha256.
    """
    path = Path(path)

    if not path.exists():
        return FileInfo(path=path, name=path.name, size=0,
                        is_readable=False, error="File not found")
    if not path.is_file():
        return FileInfo(path=path, name=path.name, size=0,
                        is_readable=False, error="Not a regular file")

    stat = path.stat()
    size = stat.st_size

    # Read header for magic detection
    try:
        with open(path, "rb") as fh:
            header = fh.read(16)
    except PermissionError:
        return FileInfo(path=path, name=path.name, size=size,
                        is_readable=False, error="Permission denied")
    except OSError as e:
        return FileInfo(path=path, name=path.name, size=size,
                        is_readable=False, error=str(e))

    # Magic detection
    mime  = "application/octet-stream"
    hint  = "binary"
    for magic, m, h in _MAGIC:
        if header.startswith(magic):
            mime, hint = m, h
            break
    else:
        # Extension-based text detection
        ext = path.suffix.lower()
        if ext in _BINARY_EXTENSIONS:
            mime = "application/octet-stream"
            hint = "binary"
        elif ext in _TEXT_EXTENSIONS:
            mime = "text/plain"
            hint = "text"
        elif size > 0 and _looks_like_text(header):
            mime = "text/plain"
            hint = "text"

    # CRLF detection (text files only, cap at 256 KB scan)
    has_crlf = False
    if hint == "text":
        try:
            with open(path, "rb") as fh:
                chunk = fh.read(262144)
                has_crlf = b"\r\n" in chunk
        except OSError:
            pass

    # SHA-256 (stream full file)
    digest = ""
    if compute_sha256:
        sha = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    sha.update(chunk)
            digest = sha.hexdigest()
        except OSError:
            digest = ""

    return FileInfo(
        path=path, name=path.name, size=size,
        mime_type=mime, transfer_hint=hint,
        has_crlf=has_crlf, sha256=digest,
        is_readable=True,
    )


def _looks_like_text(data: bytes, sample: int = 512) -> bool:
    """Heuristic: <10 % non-printable bytes implies text."""
    data = data[:sample]
    if not data:
        return False
    non_print = sum(1 for b in data if b < 0x09 or (0x0E <= b < 0x20))
    return non_print / len(data) < 0.10
