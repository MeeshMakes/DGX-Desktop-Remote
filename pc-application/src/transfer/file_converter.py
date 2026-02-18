"""
pc-application/src/transfer/file_converter.py

Pre-transfer file preparation: format conversion, CRLF stripping.

Conversions supported
  .bat / .cmd  → .sh   Windows batch  → bash  (structural translation)
  .ps1         → .sh   PowerShell     → bash  (structural translation)
  all text     → LF    CRLF stripped to Unix line endings

Script translation is intentionally conservative: complex constructs
(IF/FOR/functions) are preserved as commented-out lines so nothing is
silently lost.  The result is runnable for simple scripts and
reviewable for complex ones.
"""

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from .file_analyzer import FileInfo

# ──────────────────────────────────────────────────────────────────────
# .bat / .cmd  →  .sh
# ──────────────────────────────────────────────────────────────────────

def _bat_to_sh(text: str) -> str:
    """Translate a Windows batch script to a bash script."""
    out  = ["#!/bin/bash", "# Auto-converted from Windows batch by DGX Bridge", ""]
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    for raw in text.splitlines():
        stripped = raw.strip()
        upper    = stripped.upper()

        if not stripped:
            out.append(""); continue

        # Comments
        if upper.startswith("REM ") or upper == "REM":
            out.append("# " + stripped[4:]); continue
        if stripped.startswith("::"):
            out.append("# " + stripped[2:]); continue

        # echo
        if upper in ("@ECHO OFF", "@ECHO ON", "ECHO OFF", "ECHO ON"):
            out.append("# " + stripped); continue
        if upper.startswith("ECHO "):
            msg = re.sub(r"%(\w+)%", r"${\1}", stripped[5:])
            out.append(f'echo {_quote(msg)}'); continue
        if upper == "ECHO.":
            out.append("echo"); continue

        # Pause
        if upper == "PAUSE":
            out.append('read -rp "Press enter to continue..." _'); continue

        # SET VAR=VALUE
        if upper.startswith("SET "):
            rest = stripped[4:].strip()
            if "=" in rest:
                k, v = rest.split("=", 1)
                v = re.sub(r"%(\w+)%", r"${\1}", v)
                out.append(f"{k.strip()}={_quote(v.strip())}"); continue
            out.append("# set " + rest); continue

        # Labels and GOTO
        if stripped.startswith(":") and not stripped.startswith("::"):
            out.append(stripped[1:] + "():"); continue
        if upper.startswith("GOTO "):
            out.append("# GOTO " + stripped[5:] + "  # unsupported"); continue
        if upper.startswith("CALL "):
            out.append("source " + stripped[5:].replace("\\", "/")); continue

        # Navigation
        if upper.startswith("CD ") or upper == "CD":
            path = (stripped[3:] if len(stripped) > 3 else "~").replace("\\", "/").strip('"')
            out.append(f"cd {_quote(path)}"); continue
        if upper.startswith("MKDIR ") or upper.startswith("MD "):
            path = _rest(stripped).replace("\\", "/").strip('"')
            out.append(f"mkdir -p {_quote(path)}"); continue
        if upper.startswith("RMDIR ") or upper.startswith("RD "):
            path = _rest(stripped).replace("\\", "/").strip('"')
            out.append(f"rm -rf {_quote(path)}"); continue

        # File ops
        if upper.startswith("DEL ") or upper.startswith("ERASE "):
            out.append("rm -f " + _rest(stripped).replace("\\", "/")); continue
        if upper.startswith("COPY "):
            out.append("cp " + stripped[5:].replace("\\", "/")); continue
        if upper.startswith("XCOPY "):
            out.append("cp -r " + stripped[6:].replace("\\", "/") + "  # xcopy"); continue
        if upper.startswith("MOVE ") or upper.startswith("REN "):
            out.append("mv " + _rest(stripped).replace("\\", "/")); continue
        if upper.startswith("TYPE "):
            out.append("cat " + _rest(stripped).replace("\\", "/")); continue
        if upper.startswith("DIR"):
            out.append("ls " + stripped[3:].replace("\\", "/").strip()); continue
        if upper.startswith("START "):
            out.append("( " + stripped[6:] + " & )"); continue

        # Complex constructs — comment + preserve
        if upper.startswith(("IF ", "FOR ", "SETLOCAL", "ENDLOCAL", "SHIFT")):
            out.append("# " + re.sub(r"%(\w+)%", r"${\1}", stripped)); continue

        # Fallback: %VAR% → ${VAR}, backslash → /
        conv = re.sub(r"%(\w+)%", r"${\1}", stripped).replace("\\", "/")
        out.append(conv)

    return "\n".join(out) + "\n"


# ──────────────────────────────────────────────────────────────────────
# .ps1  →  .sh
# ──────────────────────────────────────────────────────────────────────

def _ps1_to_sh(text: str) -> str:
    """Translate a PowerShell script to a bash script."""
    out  = ["#!/bin/bash", "# Auto-converted from PowerShell by DGX Bridge", ""]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    in_block = False

    for raw in text.splitlines():
        stripped = raw.strip()
        if "<#" in stripped:
            in_block = True
        if in_block:
            out.append("# " + stripped)
            if "#>" in stripped:
                in_block = False
            continue

        if not stripped:
            out.append(""); continue
        if stripped.startswith("#"):
            out.append(stripped); continue

        s  = stripped
        su = s.upper()

        # Write-* cmdlets
        for ps, bash in (
            ("WRITE-HOST ", "echo "), ("WRITE-OUTPUT ", "echo "),
            ("WRITE-VERBOSE ", "echo "), ("WRITE-INFORMATION ", "echo "),
        ):
            if su.startswith(ps):
                msg = s[len(ps):].strip().strip("\"'")
                s = f'echo "{msg}"'; break
        else:
            if su.startswith("WRITE-ERROR "):
                s = f'echo "ERROR: {s[12:].strip().strip(chr(34) + chr(39))}" >&2'
            elif su.startswith("EXIT"):
                s = s.lower()
            elif su.startswith("SET-LOCATION ") or su.startswith("PUSH-LOCATION "):
                path = s.split(None, 1)[1].strip().strip("\"'").replace("\\", "/")
                s = f"cd {_quote(path)}"
            elif su.startswith("GET-LOCATION"):
                s = "pwd"
            elif su.startswith(("GET-CHILDITEM", "GCI ", "DIR ")):
                rest = s.split(None, 1)[1].strip() if " " in s else ""
                s = "ls " + rest
            elif su.startswith("REMOVE-ITEM "):
                path = s[12:].strip().strip("\"'").replace("\\", "/")
                s = f"rm -rf {_quote(path)}"
            elif su.startswith(("COPY-ITEM ", "MOVE-ITEM ", "NEW-ITEM ")):
                out.append("# PS: " + stripped + "  # manual conversion needed"); continue
            elif su.startswith(("INVOKE-EXPRESSION ", "IEX ")):
                s = "eval " + s.split(None, 1)[1].strip()
            elif su.startswith(("PARAM(", "PARAM (")):
                out.append("# " + stripped + "  # params — convert to $1 $2 manually"); continue
            elif su.startswith("FUNCTION "):
                name = s.split()[1].rstrip("{").strip()
                out.append(f"{name}() {{"); continue
            elif stripped == "}":
                out.append("}"); continue

        s = re.sub(r"\$env:(\w+)", r"$\1", s)
        s = s.replace("$PSScriptRoot", '$(dirname "$0")')
        s = s.replace("$PSCommandPath", '"$0"')
        s = s.replace("\\", "/")
        out.append(s)

    return "\n".join(out) + "\n"


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _quote(s: str) -> str:
    """Wrap in double-quotes if the string contains spaces."""
    if " " in s or not s:
        return f'"{s}"'
    return s

def _rest(line: str) -> str:
    """Return everything after the first whitespace-delimited word."""
    parts = line.split(None, 1)
    return parts[1] if len(parts) > 1 else ""


# Which extensions get converted and to what
_SCRIPT_CONVERTERS: dict[str, tuple] = {
    ".bat":  (_bat_to_sh, ".sh"),
    ".cmd":  (_bat_to_sh, ".sh"),
    ".ps1":  (_ps1_to_sh, ".sh"),
}


# ──────────────────────────────────────────────────────────────────────
# Public converter class
# ──────────────────────────────────────────────────────────────────────

class FileConverter:
    """
    Converts files for DGX / Linux compatibility.

    Usage::
        conv = FileConverter()
        path, dgx_name, is_tmp = conv.prepare(info)
        # transfer `path` as `dgx_name`
        if is_tmp:
            conv.cleanup()
    """

    def __init__(self):
        self._tmp_files: list[Path] = []

    def convert_name(self, info: FileInfo) -> str:
        """Return the filename to use on the DGX (may change extension)."""
        ext = info.path.suffix.lower()
        if ext in _SCRIPT_CONVERTERS:
            _, new_ext = _SCRIPT_CONVERTERS[ext]
            return info.path.stem + new_ext
        return info.name

    def needs_conversion(self, info: FileInfo) -> bool:
        """True if this file will be structurally converted (not just CRLF-stripped)."""
        return info.path.suffix.lower() in _SCRIPT_CONVERTERS

    def prepare(
        self,
        info: FileInfo,
        convert_crlf: bool = True,
    ) -> tuple[Path, str, bool]:
        """
        Return ``(path_to_send, dgx_filename, is_temp)``.

        *is_temp* — if True, call :meth:`cleanup` after the transfer.
        """
        if not info.is_readable:
            return info.path, info.name, False

        ext = info.path.suffix.lower()

        # ── Script conversion (.bat/.cmd/.ps1 → .sh) ─────────────────
        if ext in _SCRIPT_CONVERTERS:
            converter_fn, new_ext = _SCRIPT_CONVERTERS[ext]
            new_name = info.path.stem + new_ext
            try:
                src_text  = info.path.read_text(encoding="utf-8", errors="replace")
                converted = converter_fn(src_text)
                fd, tmp   = tempfile.mkstemp(suffix=new_ext, prefix="dgx_tx_")
                tmp_path  = Path(tmp)
                self._tmp_files.append(tmp_path)
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(converted)
                return tmp_path, new_name, True
            except Exception:
                pass  # fall through

        # ── CRLF strip for plain text ─────────────────────────────────
        if convert_crlf and info.transfer_hint == "text" and info.has_crlf:
            tmp_path, ok = self._strip_crlf(info.path)
            if ok:
                return tmp_path, info.name, True

        return info.path, info.name, False

    def prepare_to_dir(
        self,
        info: FileInfo,
        out_dir: Path,
        convert_crlf: bool = True,
    ) -> tuple[Path, str]:
        """
        Like :meth:`prepare` but writes to *out_dir* (bridge-prep folder).
        Caller owns the output file — no need for :meth:`cleanup`.
        Returns ``(prepared_path, dgx_filename)``.
        """
        if not info.is_readable:
            return info.path, info.name

        out_dir.mkdir(parents=True, exist_ok=True)
        ext = info.path.suffix.lower()

        if ext in _SCRIPT_CONVERTERS:
            converter_fn, new_ext = _SCRIPT_CONVERTERS[ext]
            new_name  = info.path.stem + new_ext
            out_path  = out_dir / new_name
            try:
                src_text  = info.path.read_text(encoding="utf-8", errors="replace")
                converted = converter_fn(src_text)
                out_path.write_text(converted, encoding="utf-8", newline="\n")
                return out_path, new_name
            except Exception:
                pass

        if convert_crlf and info.transfer_hint == "text" and info.has_crlf:
            out_path = out_dir / info.name
            try:
                with open(info.path, "rb") as src, open(out_path, "wb") as dst:
                    for chunk in iter(lambda: src.read(65536), b""):
                        dst.write(chunk.replace(b"\r\n", b"\n"))
                return out_path, info.name
            except OSError:
                pass

        # No conversion — copy as-is
        out_path = out_dir / info.name
        shutil.copy2(info.path, out_path)
        return out_path, info.name

    # ── Private ───────────────────────────────────────────────────────

    def _strip_crlf(self, path: Path) -> tuple[Path, bool]:
        try:
            fd, tmp  = tempfile.mkstemp(suffix=path.suffix, prefix="dgx_tx_")
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

    def get_remote_metadata(self, info: FileInfo, dgx_name: str = "") -> dict:
        """Build the metadata dict sent alongside each file to the DGX."""
        name = dgx_name or info.name
        return {
            "name":        name,
            "size":        info.size,
            "sha256":      info.sha256,
            "mime_type":   info.mime_type,
            "is_text":     info.transfer_hint == "text",
            "had_crlf":    info.has_crlf,
            "converted":   info.path.suffix.lower() in _SCRIPT_CONVERTERS,
            "permissions": _suggest_permissions(info, name),
        }


def _suggest_permissions(info: FileInfo, dgx_name: str = "") -> str:
    """Return chmod octal string based on file type."""
    name   = dgx_name or info.name
    suffix = Path(name).suffix.lower()
    if info.mime_type in ("application/elf", "application/exe") \
            or suffix in (".sh", ".bash", ".zsh", ".py", ".pl", ".rb"):
        return "0755"
    return "0644"
