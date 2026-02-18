"""
pc-application/src/setup_wizard.py
First-run setup wizard.
- Page 1: Welcome / hardware checklist
- Page 2: IP entry with Auto-Fill button + Live port negotiation / auto-connect
- Page 3: Preferences
"""

import re
import sys
import socket
import json
import time
from pathlib import Path

from PyQt6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QFrame, QProgressBar,
    QSizePolicy, QWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

from config import Config
from theme import ACCENT, SUCCESS, ERROR, WARNING, TEXT_DIM, BG_RAISED, BORDER, BG_SURFACE

IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _is_valid_ip(s: str) -> bool:
    if not IP_RE.match(s.strip()):
        return False
    return all(0 <= int(p) <= 255 for p in s.strip().split("."))


def _get_local_ip() -> str:
    """
    Detect the local IP by routing toward 10.0.0.1 (doesn't actually send
    any data — just asks the OS which interface it would use).
    Falls back to 10.0.0.2 if detection fails.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.0.0.1", 80))
            return s.getsockname()[0]
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "10.0.0.2"


# ──────────────────────────────────────────────────────────────────────
# Background worker: full negotiate + connect sequence
# ──────────────────────────────────────────────────────────────────────

class _NegotiateThread(QThread):
    """
    1. Scans local ports for free candidates.
    2. Connects to DGX discovery port 22000.
    3. Negotiates RPC/video/input ports.
    Emits step-by-step status updates so the UI can show progress.
    """
    step     = pyqtSignal(str, str)   # (message, level)  level=info/ok/error/warn
    finished = pyqtSignal(bool, dict) # (success, result_dict)

    def __init__(self, dgx_ip: str, pc_ip: str):
        super().__init__()
        self._dgx_ip = dgx_ip
        self._pc_ip  = pc_ip

    def run(self):
        from network.port_negotiator import scan_local_free_ports, DISCOVERY_PORT

        self.step.emit("Scanning local ports for free candidates…", "info")
        candidates = scan_local_free_ports(count=3)
        if len(candidates) < 3:
            self.step.emit("❌  Not enough free ports found (range 22010-22059)", "error")
            self.finished.emit(False, {})
            return

        self.step.emit(f"Free ports on PC: {candidates[:6]}", "info")
        self.step.emit(f"Connecting to DGX {self._dgx_ip}:{DISCOVERY_PORT}…", "info")

        try:
            sock = socket.create_connection((self._dgx_ip, DISCOVERY_PORT), timeout=6)
        except OSError as e:
            self.step.emit(f"❌  Cannot reach DGX: {e}", "error")
            self.finished.emit(False, {})
            return

        self.step.emit("Connected. Sending port candidates…", "info")
        try:
            msg = json.dumps({
                "type":       "negotiate",
                "candidates": candidates,
                "pc_ip":      self._pc_ip,
            }) + "\n"
            sock.sendall(msg.encode())
            sock.settimeout(8)
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionResetError("DGX closed connection during negotiation")
                buf += chunk
            resp = json.loads(buf.split(b"\n")[0].decode())
        except Exception as e:
            self.step.emit(f"❌  Negotiation error: {e}", "error")
            self.finished.emit(False, {})
            sock.close()
            return
        finally:
            try:
                sock.close()
            except Exception:
                pass

        if not resp.get("ok"):
            self.step.emit(f"❌  DGX rejected: {resp.get('error', 'unknown')}", "error")
            self.finished.emit(False, {})
            return

        rpc   = resp["rpc"]
        video = resp["video"]
        inp   = resp["input"]
        self.step.emit(
            f"✓  Agreed on ports:  RPC {rpc}  ·  Video {video}  ·  Input {inp}", "ok"
        )

        # Quick RPC ping to confirm service is fully up
        self.step.emit("Verifying connection…", "info")
        try:
            ping_sock = socket.create_connection((self._dgx_ip, rpc), timeout=5)
            ping_sock.sendall((json.dumps({"type": "ping"}) + "\n").encode())
            ping_sock.settimeout(4)
            reply_raw = b""
            while b"\n" not in reply_raw:
                c = ping_sock.recv(4096)
                if not c:
                    break
                reply_raw += c
            ping_sock.close()
            reply = json.loads(reply_raw.split(b"\n")[0]) if reply_raw else {}
            if reply.get("type") == "pong" or reply.get("ok"):
                self.step.emit("✓  DGX responding — ready to launch!", "ok")
            else:
                self.step.emit("⚠  Connected but ping response unexpected.", "warn")
        except Exception as e:
            self.step.emit(f"⚠  Ports agreed, RPC ping failed: {e}", "warn")

        self.finished.emit(True, {"rpc": rpc, "video": video, "input": inp})


# ──────────────────────────────────────────────────────────────────────
# Setup Wizard
# ──────────────────────────────────────────────────────────────────────

class SetupWizard(QWizard):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("DGX Desktop Remote — Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(640, 520)
        self.setButtonText(QWizard.WizardButton.NextButton,   "Next  →")
        self.setButtonText(QWizard.WizardButton.BackButton,   "←  Back")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Launch")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Exit")
        self.addPage(_WelcomePage())
        self.addPage(_NetworkPage(config))
        self.addPage(_PrefsPage(config))


# ──────────────────────────────────────────────────────────────────────
# Page 1 — Welcome
# ──────────────────────────────────────────────────────────────────────

class _WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Welcome to DGX Desktop Remote")
        l = QVBoxLayout(self)
        l.setSpacing(14)
        desc = QLabel(
            "This wizard sets up the direct 10 GbE connection between your\n"
            "Windows PC and the NVIDIA DGX.\n\n"
            "Before continuing, confirm the hardware checklist below:"
        )
        desc.setWordWrap(True)
        l.addWidget(desc)
        l.addWidget(_Divider())
        for icon, title, sub in [
            ("📦", "10 GbE NIC installed in PC",                     "Required hardware"),
            ("🔌", "Direct Cat6A or DAC cable connected",             "PC NIC ↔ DGX NIC"),
            ("⚙️",  "PC NIC set to a static IP (e.g. 10.0.0.2/24)",  "No DHCP, no gateway"),
            ("⚙️",  "DGX set to a static IP (e.g. 10.0.0.1/24)",     "Via netplan or nmcli"),
            ("🐧", "DGX service installed",                           "python3 dgx_service.py  (service will be auto-detected)"),
        ]:
            row = QHBoxLayout()
            row.setSpacing(10)
            ico = QLabel(icon)
            ico.setFixedWidth(28)
            ico.setAlignment(Qt.AlignmentFlag.AlignTop)
            row.addWidget(ico)
            col = QVBoxLayout()
            col.setSpacing(1)
            col.addWidget(_lbl(title, bold=True))
            col.addWidget(_lbl(sub, dim=True))
            row.addLayout(col)
            row.addStretch()
            l.addLayout(row)
        l.addStretch()


# ──────────────────────────────────────────────────────────────────────
# Page 2 — Network / Auto-connect
# ──────────────────────────────────────────────────────────────────────

class _NetworkPage(QWizardPage):
    def __init__(self, config: Config):
        super().__init__()
        self.config  = config
        self._thread: _NegotiateThread = None
        self._negotiated: dict = {}
        self.setTitle("Network Setup")
        self.setSubTitle(
            "Enter the static IPs for both machines, then click  ⚡ Auto-Connect."
        )

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # ── IP form ───────────────────────────────────────────────────
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        pc_row = QHBoxLayout()
        self._f_pc = QLineEdit()
        self._f_pc.setPlaceholderText("e.g.  10.0.0.2")
        pc_row.addWidget(self._f_pc)
        btn_fill_pc = QPushButton("Auto-Fill")
        btn_fill_pc.setFixedWidth(80)
        btn_fill_pc.setToolTip("Detect this PC's IP on the DGX link automatically")
        btn_fill_pc.clicked.connect(self._autofill_pc)
        pc_row.addWidget(btn_fill_pc)
        form.addRow("This PC's IP:", pc_row)

        dgx_row = QHBoxLayout()
        self._f_dgx = QLineEdit()
        self._f_dgx.setPlaceholderText("e.g.  10.0.0.1")
        dgx_row.addWidget(self._f_dgx)
        btn_fill_dgx = QPushButton("Auto-Fill")
        btn_fill_dgx.setFixedWidth(80)
        btn_fill_dgx.setToolTip("Guess DGX IP (same subnet, last octet = 1)")
        btn_fill_dgx.clicked.connect(self._autofill_dgx)
        dgx_row.addWidget(btn_fill_dgx)
        form.addRow("DGX IP Address:", dgx_row)

        root.addLayout(form)
        root.addWidget(_Divider())

        # ── Auto-connect button ───────────────────────────────────────
        btn_row = QHBoxLayout()
        self._btn_connect = QPushButton("⚡  Auto-Connect")
        self._btn_connect.setProperty("class", "primary")
        self._btn_connect.setFixedHeight(34)
        self._btn_connect.clicked.connect(self._start_negotiation)
        btn_row.addWidget(self._btn_connect)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── Progress bar ──────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.setTextVisible(False)
        self._progress.hide()
        root.addWidget(self._progress)

        # ── Step log ──────────────────────────────────────────────────
        self._log_frame = QFrame()
        self._log_frame.setStyleSheet(
            f"background: {BG_SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;"
        )
        self._log_frame.setMinimumHeight(120)
        ll = QVBoxLayout(self._log_frame)
        ll.setContentsMargins(10, 8, 10, 8)
        ll.setSpacing(3)
        self._log_layout = ll
        root.addWidget(self._log_frame)

        # ── Summary label ─────────────────────────────────────────────
        self._ports_lbl = QLabel("")
        self._ports_lbl.setStyleSheet(
            f"color: {SUCCESS}; font-size: 12px; font-weight: 600;"
        )
        root.addWidget(self._ports_lbl)
        root.addStretch()

        self.registerField("pc_ip*",  self._f_pc)
        self.registerField("dgx_ip*", self._f_dgx)

        # Pre-fill if reconnecting
        if config.pc_ip:
            self._f_pc.setText(config.pc_ip)
        if config.dgx_ip:
            self._f_dgx.setText(config.dgx_ip)

    # ------------------------------------------------------------------
    # Auto-fill
    # ------------------------------------------------------------------

    def _autofill_pc(self):
        ip = _get_local_ip()
        self._f_pc.setText(ip)
        self._log(f"Detected PC IP: {ip}", "ok")

    def _autofill_dgx(self):
        pc = self._f_pc.text().strip()
        if not _is_valid_ip(pc):
            pc = _get_local_ip()
            self._f_pc.setText(pc)
        parts = pc.split(".")
        parts[-1] = "1"
        dgx = ".".join(parts)
        self._f_dgx.setText(dgx)
        self._log(f"Auto-filled DGX IP: {dgx}", "ok")

    # ------------------------------------------------------------------
    # Negotiation
    # ------------------------------------------------------------------

    def _start_negotiation(self):
        pc  = self._f_pc.text().strip()
        dgx = self._f_dgx.text().strip()
        if not _is_valid_ip(pc):
            self._log("⚠  Enter a valid PC IP first (or click Auto-Fill)", "error")
            return
        if not _is_valid_ip(dgx):
            self._log("⚠  Enter a valid DGX IP first (or click Auto-Fill)", "error")
            return
        self._clear_log()
        self._negotiated = {}
        self._ports_lbl.setText("")
        self._btn_connect.setEnabled(False)
        self._progress.show()

        self._thread = _NegotiateThread(dgx_ip=dgx, pc_ip=pc)
        self._thread.step.connect(self._on_step)
        self._thread.finished.connect(self._on_finished)
        self._thread.start()

    def _on_step(self, msg: str, level: str):
        self._log(msg, level)

    def _on_finished(self, ok: bool, result: dict):
        self._progress.hide()
        self._btn_connect.setEnabled(True)
        if ok:
            self._negotiated = result
            self._ports_lbl.setText(
                f"✓  Ports locked:   RPC {result['rpc']}   "
                f"Video {result['video']}   Input {result['input']}"
            )
        else:
            self._log("Check that the DGX service is running, then retry.", "error")

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    def validatePage(self) -> bool:
        pc  = self._f_pc.text().strip()
        dgx = self._f_dgx.text().strip()
        if not _is_valid_ip(pc):
            self._f_pc.setStyleSheet(f"border: 1px solid {ERROR};")
            return False
        if not _is_valid_ip(dgx):
            self._f_dgx.setStyleSheet(f"border: 1px solid {ERROR};")
            return False
        self.config.pc_ip  = pc
        self.config.dgx_ip = dgx
        if self._negotiated:
            self.config.rpc_port        = self._negotiated["rpc"]
            self.config.video_port      = self._negotiated["video"]
            self.config.input_port      = self._negotiated["input"]
            self.config.last_rpc_port   = self._negotiated["rpc"]
            self.config.last_video_port = self._negotiated["video"]
            self.config.last_input_port = self._negotiated["input"]
        return True

    # ------------------------------------------------------------------
    # Log helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str, level: str = "info"):
        colors = {"info": TEXT_DIM, "ok": SUCCESS, "error": ERROR, "warn": WARNING}
        lbl = QLabel(msg)
        lbl.setStyleSheet(
            f"color: {colors.get(level, TEXT_DIM)}; font-size: 11px;"
        )
        lbl.setWordWrap(True)
        self._log_layout.addWidget(lbl)
        while self._log_layout.count() > 12:
            item = self._log_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _clear_log(self):
        while self._log_layout.count():
            item = self._log_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()


# ──────────────────────────────────────────────────────────────────────
# Page 3 — Preferences
# ──────────────────────────────────────────────────────────────────────

class _PrefsPage(QWizardPage):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.setTitle("Preferences")
        self.setSubTitle("All settings can be changed later via the Manager.")
        l = QVBoxLayout(self)
        l.setSpacing(10)

        self._auto          = QCheckBox("Auto-connect when application starts")
        self._autoreconnect = QCheckBox("Auto-reconnect if connection drops")
        self._min           = QCheckBox("Start minimized to system tray")
        self._shortcut      = QCheckBox("Create a desktop shortcut")
        self._auto.setChecked(True)
        self._autoreconnect.setChecked(True)
        self._shortcut.setChecked(True)

        for cb in (self._auto, self._autoreconnect, self._min, self._shortcut):
            l.addWidget(cb)

        l.addStretch()
        from config import CONFIG_FILE
        l.addWidget(_lbl("Configuration saved to:", dim=True))
        l.addWidget(_lbl(str(CONFIG_FILE), dim=True))

    def validatePage(self) -> bool:
        self.config.auto_connect    = self._auto.isChecked()
        self.config.auto_reconnect  = self._autoreconnect.isChecked()
        self.config.start_minimized = self._min.isChecked()
        self.config.save()
        if self._shortcut.isChecked():
            try:
                sys.path.insert(0, str(Path(__file__).parents[2]))
                from create_shortcuts import create_windows_shortcut
                create_windows_shortcut()
            except Exception:
                pass
        return True


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

class _Divider(QFrame):
    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.HLine)
        self.setStyleSheet(f"background: {BORDER}; max-height: 1px;")


def _lbl(text: str, bold: bool = False, dim: bool = False) -> QLabel:
    lbl = QLabel(text)
    s = []
    if bold: s.append("font-weight: 600;")
    if dim:  s.append(f"color: {TEXT_DIM}; font-size: 11px;")
    if s:    lbl.setStyleSheet(" ".join(s))
    return lbl
