"""
test_transfer.py — unit tests for file_converter, file_analyzer, and transfer_session.

Run from the repo root:
    python -m pytest tests/test_transfer.py -v
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Make sure the pc-application source is importable
_SRC = Path(__file__).parents[1] / "pc-application" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from transfer.file_analyzer import FileInfo, analyze_file
from transfer.file_converter import FileConverter
from transfer.transfer_session import TransferItem, TransferSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(tmp: Path, name: str, content: str, *, crlf: bool = False) -> Path:
    path = tmp / name
    nl = "\r\n" if crlf else "\n"
    path.write_bytes((nl.join(content.splitlines()) + nl).encode())
    return path


# ---------------------------------------------------------------------------
# FileAnalyzer
# ---------------------------------------------------------------------------

class TestFileAnalyzer:
    def test_text_file(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "hello.txt", "Hello world\n")
        info = analyze_file(p)
        assert info.transfer_hint == "text"

    def test_bat_is_text(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "build.bat", "@ECHO OFF\nECHO hi\n", crlf=True)
        info = analyze_file(p)
        assert info.transfer_hint == "text"

    def test_ps1_is_text(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "setup.ps1", "Write-Host 'hi'\n")
        info = analyze_file(p)
        assert info.transfer_hint == "text"

    def test_python_is_text(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "script.py", "print('hi')\n")
        info = analyze_file(p)
        assert info.transfer_hint == "text"

    def test_binary_detected(self, tmp_path: Path) -> None:
        p = tmp_path / "data.bin"
        p.write_bytes(bytes(range(256)))
        info = analyze_file(p)
        assert info.transfer_hint == "binary"

    def test_svg_is_text(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "icon.svg", "<svg></svg>\n")
        info = analyze_file(p)
        assert info.transfer_hint == "text"

    def test_json_is_text(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "data.json", '{"key": "value"}\n')
        info = analyze_file(p)
        assert info.transfer_hint == "text"


# ---------------------------------------------------------------------------
# FileConverter — name and needs_conversion
# ---------------------------------------------------------------------------

class TestFileConverterNames:
    def test_bat_converts_to_sh(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "build.bat", "@ECHO OFF\n")
        info = analyze_file(p)
        conv = FileConverter()
        assert conv.needs_conversion(info)
        assert conv.convert_name(info) == "build.sh"

    def test_ps1_converts_to_sh(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "setup.ps1", "Write-Host hi\n")
        info = analyze_file(p)
        conv = FileConverter()
        assert conv.needs_conversion(info)
        assert conv.convert_name(info) == "setup.sh"

    def test_cmd_converts_to_sh(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "run.cmd", "@ECHO OFF\n")
        info = analyze_file(p)
        assert FileConverter().needs_conversion(info)
        assert FileConverter().convert_name(info) == "run.sh"

    def test_py_no_conversion(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "app.py", "print(1)\n")
        info = analyze_file(p)
        assert not FileConverter().needs_conversion(info)
        assert FileConverter().convert_name(info) == "app.py"

    def test_txt_no_conversion(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "notes.txt", "hello\n")
        info = analyze_file(p)
        assert not FileConverter().needs_conversion(info)


# ---------------------------------------------------------------------------
# FileConverter — bat → sh translation
# ---------------------------------------------------------------------------

class TestBatToSh:
    def _convert(self, tmp_path: Path, content: str, *, crlf: bool = False) -> str:
        p = _write(tmp_path, "t.bat", content, crlf=crlf)
        info = analyze_file(p)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        prepared, dgx_name = FileConverter().prepare_to_dir(info, out_dir)
        return prepared.read_text()

    def test_shebang_added(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "@ECHO OFF\nECHO hi\n")
        assert result.startswith("#!/bin/bash")

    def test_set_becomes_var(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "SET FOO=bar\n")
        assert "FOO=bar" in result
        # Should not contain raw SET
        assert "SET FOO" not in result

    def test_percent_vars_expanded(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "ECHO %NAME%\n")
        assert "${NAME}" in result

    def test_mkdir_translated(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "MKDIR build\\output\n")
        assert "mkdir -p" in result

    def test_del_translated(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "DEL old.txt\n")
        assert "rm -f" in result

    def test_copy_translated(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "COPY src.c dst.c\n")
        assert "cp src.c dst.c" in result

    def test_rem_comment_preserved(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "REM This is a comment\n")
        assert "# This is a comment" in result

    def test_crlf_stripped(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "ECHO hi\n", crlf=True)
        assert "\r" not in result

    def test_output_file_is_sh(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "build.bat", "@ECHO OFF\n")
        info = analyze_file(p)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        prepared, dgx_name = FileConverter().prepare_to_dir(info, out_dir)
        assert dgx_name == "build.sh"
        assert prepared.suffix == ".sh"


# ---------------------------------------------------------------------------
# FileConverter — ps1 → sh translation
# ---------------------------------------------------------------------------

class TestPs1ToSh:
    def _convert(self, tmp_path: Path, content: str) -> str:
        p = _write(tmp_path, "t.ps1", content)
        info = analyze_file(p)
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        prepared, dgx_name = FileConverter().prepare_to_dir(info, out_dir)
        return prepared.read_text()

    def test_shebang_added(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "Write-Host 'hi'\n")
        assert result.startswith("#!/bin/bash")

    def test_write_host_translated(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "Write-Host 'Hello'\n")
        assert "echo" in result

    def test_env_var_translated(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "$env:HOME\n")
        assert "$HOME" in result

    def test_set_location_translated(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "Set-Location /tmp\n")
        assert "cd /tmp" in result

    def test_psscriptroot_translated(self, tmp_path: Path) -> None:
        result = self._convert(tmp_path, "$PSScriptRoot\n")
        assert '$(dirname "$0")' in result


# ---------------------------------------------------------------------------
# FileConverter — plain text CRLF stripping (non-script)
# ---------------------------------------------------------------------------

class TestCrlfStripping:
    def test_txt_crlf_stripped(self, tmp_path: Path) -> None:
        p = _write(tmp_path, "notes.txt", "line one\nline two\n", crlf=True)
        info = analyze_file(p)
        prepared_path, dgx_name, is_temp = FileConverter().prepare(info, convert_crlf=True)
        try:
            content = prepared_path.read_bytes()
            assert b"\r" not in content
        finally:
            if is_temp:
                prepared_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# TransferSession
# ---------------------------------------------------------------------------

class TestTransferSession:
    def test_session_id_unique(self) -> None:
        s1 = TransferSession()
        s2 = TransferSession()
        assert s1.session_id != s2.session_id

    def test_local_prep_path_created(self) -> None:
        s = TransferSession()
        p = s.local_prep_path
        assert p.exists()
        assert p.is_dir()

    def test_transfer_item_dgx_name_default(self) -> None:
        p = Path("/tmp/test.bat")
        item = TransferItem(local_path=p, dgx_dest="/home/user/scripts/test.bat")
        assert item.dgx_name == ""


# ---------------------------------------------------------------------------
# Integration: prepare_to_dir writes correct file
# ---------------------------------------------------------------------------

class TestPrepareToDir:
    def test_bat_written_as_sh(self, tmp_path: Path) -> None:
        src = _write(tmp_path, "build.bat", "@ECHO OFF\nECHO building\n")
        info = analyze_file(src)
        out = tmp_path / "bridge"
        out.mkdir()
        prepared, dgx_name = FileConverter().prepare_to_dir(info, out)
        assert prepared.exists()
        assert dgx_name == "build.sh"
        text = prepared.read_text()
        assert "#!/bin/bash" in text
        assert "building" in text.lower() or "echo" in text.lower()

    def test_txt_written_unchanged(self, tmp_path: Path) -> None:
        src = _write(tmp_path, "readme.txt", "Hello!\n")
        info = analyze_file(src)
        out = tmp_path / "bridge"
        out.mkdir()
        prepared, dgx_name = FileConverter().prepare_to_dir(info, out)
        assert dgx_name == "readme.txt"
        assert prepared.read_text() == "Hello!\n"

    def test_binary_copied_intact(self, tmp_path: Path) -> None:
        raw = bytes(range(256))
        src = tmp_path / "blob.bin"
        src.write_bytes(raw)
        info = analyze_file(src)
        out = tmp_path / "bridge"
        out.mkdir()
        prepared, dgx_name = FileConverter().prepare_to_dir(info, out)
        assert prepared.read_bytes() == raw
