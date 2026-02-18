"""
make_test_files.py — generate a structured set of test files for transfer testing.

Run from the repo root:
    python tests/make_test_files.py

Creates:  tests/test_files/
    images/         PNG, JPG, BMP, SVG
    documents/      TXT, MD, CSV, JSON, XML, HTML
    code/           PY, SH, C, JS
    windows_scripts/  BAT, PS1  (CRLF line endings — need conversion)
    data/           YAML, TOML, TSV
    crlf/           Text files with explicit CRLF endings
    binary/         A small binary blob
"""

from __future__ import annotations

import json
import os
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).parent / "test_files"


def _w(path: Path, text: str, *, crlf: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nl = "\r\n" if crlf else "\n"
    data = nl.join(text.splitlines()) + nl
    path.write_bytes(data.encode())


def _wb(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def _make_png(w: int = 8, h: int = 8) -> bytes:
    """Return a minimal valid 8×8 RGBA PNG."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit RGB
    raw = b""
    for row in range(h):
        raw += b"\x00"  # filter type None
        for col in range(w):
            r = (row * 32) & 0xFF
            g = (col * 32) & 0xFF
            b_ = 128
            raw += bytes([r, g, b_])
    compressed = zlib.compress(raw)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def _make_bmp(w: int = 4, h: int = 4) -> bytes:
    """Return a minimal 4×4 24-bit BMP."""
    row_size = (w * 3 + 3) & ~3  # pad to 4 bytes
    pixel_data = b""
    for row in range(h):
        row_bytes = b""
        for col in range(w):
            row_bytes += bytes([col * 64, row * 64, 128])  # BGR
        row_bytes += b"\x00" * (row_size - len(row_bytes))
        pixel_data += row_bytes
    file_size = 54 + len(pixel_data)
    header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, 54)
    dib = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(pixel_data), 2835, 2835, 0, 0)
    return header + dib + pixel_data


def make_images() -> None:
    d = ROOT / "images"
    _wb(d / "test.png", _make_png())
    # JPG: minimal valid JFIF
    jpg = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1eP"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
        b"\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04"
        b"\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa"
        b"\x07\"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n"
        b"\x16\x17\x18\x19\x1a%&'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz"
        b"\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99"
        b"\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7"
        b"\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5"
        b"\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1"
        b"\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa"
        b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd4P\x00\x00\x00\x1f\xff\xd9"
    )
    _wb(d / "test.jpg", jpg)
    _wb(d / "test.bmp", _make_bmp())
    _w(d / "test.svg", """\
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">
  <rect width="64" height="64" fill="#4a90d9"/>
  <circle cx="32" cy="32" r="20" fill="#ffffff" opacity="0.8"/>
  <text x="32" y="37" text-anchor="middle" font-size="14" fill="#333">DGX</text>
</svg>
""")


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def make_documents() -> None:
    d = ROOT / "documents"
    _w(d / "test.txt", "Hello from the DGX Bridge transfer test.\nLine two.\nLine three.\n")
    _w(d / "test.md", """\
# DGX Bridge Test Document

This file is used to test **Markdown** transfer.

- Item one
- Item two
- Item three

> Quote block for good measure.
""")
    _w(d / "test.csv", "name,value,description\nalpha,1,First item\nbeta,2,Second item\ngamma,3,Third item\n")
    _w(d / "test.json", json.dumps({"version": 1, "test": True, "items": ["a", "b", "c"]}, indent=2) + "\n")
    _w(d / "test.xml", """\
<?xml version="1.0" encoding="UTF-8"?>
<root>
  <item id="1">Alpha</item>
  <item id="2">Beta</item>
</root>
""")
    _w(d / "test.html", """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>DGX Test</title></head>
<body>
  <h1>Transfer Test</h1>
  <p>This file exercises HTML transfer via the bridge.</p>
</body>
</html>
""")


# ---------------------------------------------------------------------------
# Code
# ---------------------------------------------------------------------------

def make_code() -> None:
    d = ROOT / "code"
    _w(d / "test.py", """\
#!/usr/bin/env python3
\"\"\"Minimal Python test file for transfer testing.\"\"\"


def greet(name: str) -> str:
    return f"Hello, {name}!"


if __name__ == "__main__":
    print(greet("DGX"))
""")
    _w(d / "test.sh", """\
#!/bin/bash
# Minimal shell script for transfer testing
set -euo pipefail

NAME="${1:-DGX}"
echo "Hello from bash: $NAME"
""")
    _w(d / "test.c", """\
#include <stdio.h>

int main(void) {
    printf("Hello from C transfer test\\n");
    return 0;
}
""")
    _w(d / "test.js", """\
'use strict';
// Minimal JS file for transfer testing
const greet = (name = 'DGX') => `Hello, ${name}!`;
console.log(greet());
""")


# ---------------------------------------------------------------------------
# Windows Scripts (CRLF, need conversion)
# ---------------------------------------------------------------------------

def make_windows_scripts() -> None:
    d = ROOT / "windows_scripts"
    _w(d / "build.bat", """\
@ECHO OFF
REM Simple build script — should be converted to build.sh
SET PROJECT=DGX-Remote
SET BUILD_DIR=build\\output

ECHO Building %PROJECT%...
MKDIR %BUILD_DIR%
COPY src\\*.c %BUILD_DIR%\\
ECHO Done.
""", crlf=True)

    _w(d / "setup.ps1", """\
# PowerShell setup script — should be converted to setup.sh
param(
    [string]$InstallPath = "/opt/dgx-remote"
)

Write-Host "Installing to $InstallPath"
$env:DGX_HOME = $InstallPath
Set-Location $PSScriptRoot

New-Item -ItemType Directory -Force -Path $InstallPath | Out-Null
Write-Host "Setup complete."
""", crlf=True)

    _w(d / "deploy.cmd", """\
@ECHO OFF
REM Deployment script (.cmd variant)
SET SERVER=10.0.0.1
ECHO Deploying to %SERVER%...
XCOPY /E /Y dist\\ \\\\%SERVER%\\share\\
ECHO Deployment done.
""", crlf=True)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def make_data() -> None:
    d = ROOT / "data"
    _w(d / "test.yaml", """\
version: 1
name: dgx-bridge-test
settings:
  verbose: true
  max_connections: 4
items:
  - id: 1
    label: alpha
  - id: 2
    label: beta
""")
    _w(d / "test.toml", """\
[project]
name = "dgx-bridge-test"
version = "1.0.0"

[settings]
verbose = true
max_connections = 4

[[items]]
id = 1
label = "alpha"

[[items]]
id = 2
label = "beta"
""")
    _w(d / "test.tsv", "id\tname\tvalue\n1\talpha\t100\n2\tbeta\t200\n3\tgamma\t300\n")


# ---------------------------------------------------------------------------
# CRLF edge-case files
# ---------------------------------------------------------------------------

def make_crlf() -> None:
    d = ROOT / "crlf"
    _w(d / "crlf_text.txt", "Line one\nLine two\nLine three\n", crlf=True)
    _w(d / "crlf_script.sh", "#!/bin/bash\necho hello\n", crlf=True)
    _w(d / "mixed.txt", "Unix line\n")
    # manually write a mixed file
    (d / "mixed.txt").write_bytes(b"Unix line\nWindows line\r\nUnix again\n")


# ---------------------------------------------------------------------------
# Binary blob
# ---------------------------------------------------------------------------

def make_binary() -> None:
    d = ROOT / "binary"
    data = bytes(range(256)) * 4  # 1 KiB of bytes 0-255 repeated
    _wb(d / "random.bin", data)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    make_images()
    make_documents()
    make_code()
    make_windows_scripts()
    make_data()
    make_crlf()
    make_binary()

    files = list(ROOT.rglob("*"))
    file_count = sum(1 for f in files if f.is_file())
    print(f"Created {file_count} test files in {ROOT}")
    for f in sorted(files):
        if f.is_file():
            rel = f.relative_to(ROOT)
            print(f"  {rel}  ({f.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
