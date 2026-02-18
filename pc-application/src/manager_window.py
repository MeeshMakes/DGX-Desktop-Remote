"""
pc-application/src/manager_window.py
PC Manager dialog — settings, tools, DGX system info.
"""

import subprocess
import platform
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QSpinBox, QComboBox, QPushButton,
    QGroupBox, QCheckBox, QTabWidget, QWidget, QTextEdit,
    QDialogButtonBox, QMessageBox, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

from config import Config
from theme import (
    ACCENT, SUCCESS, ERROR, WARNING, TEXT_DIM, TEXT_MAIN,
    BG_RAISED, BG_SURFACE, BORDER, BG_BASE
)
from widgets import HDivider, SectionTitle, InfoCard


# ──────────────────────────────────────────────────────────────────────
# System info fetch thread
# ──────────────────────────────────────────────────────────────────────

class _SystemInfoThread(QThread):
    result = pyqtSignal(dict)

    def __init__(self, connection):
        super().__init__()
        self._conn = connection

    def run(self):
        if not self._conn or not self._conn.connected:
            self.result.emit({})
            return
        try:
            info = self._conn.rpc({"type": "get_system_info"}, timeout=5)
            self.result.emit(info)
        except Exception:
            self.result.emit({})


# ──────────────────────────────────────────────────────────────────────
# Manager Window
# ──────────────────────────────────────────────────────────────────────

class ManagerWindow(QDialog):

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._conn  = parent.conn if parent and hasattr(parent, "conn") else None
        self.setWindowTitle("DGX Desktop Remote — Manager")
        self.setMinimumSize(560, 560)
        self.setModal(True)
        self._build_ui()
        self._populate()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header bar
        header = QWidget()
        header.setFixedHeight(52)
        header.setStyleSheet(
            f"background: {BG_RAISED}; border-bottom: 1px solid {BORDER};"
        )
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 16, 0)
        title = QLabel("Manager")
        title.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {TEXT_MAIN};"
        )
        hl.addWidget(title)
        hl.addStretch()
        conn_badge = QLabel(
            "● Connected" if (self._conn and self._conn.connected) else "● Offline"
        )
        conn_badge.setStyleSheet(
            f"color: {SUCCESS if (self._conn and self._conn.connected) else TEXT_DIM};"
            f"font-size: 12px; font-weight: 600;"
        )
        hl.addWidget(conn_badge)
        root.addWidget(header)

        # Tabs
        tabs = QTabWidget()
        tabs.setStyleSheet(f"QTabWidget::pane {{ border: none; }}")
        tabs.addTab(self._tab_network(),   "🌐  Network")
        tabs.addTab(self._tab_display(),   "🖥  Display")
        tabs.addTab(self._tab_behavior(),  "⚙  Behavior")
        tabs.addTab(self._tab_dgxinfo(),   "📊  DGX Info")
        tabs.addTab(self._tab_tools(),     "🔧  Tools")
        root.addWidget(tabs)

        # Save / Cancel
        root.addWidget(HDivider())
        btn_row = QWidget()
        btn_row.setStyleSheet(f"background: {BG_RAISED};")
        bl = QHBoxLayout(btn_row)
        bl.setContentsMargins(16, 10, 16, 10)
        bl.addStretch()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setFixedWidth(90)
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("Save")
        btn_save.setProperty("class", "primary")
        btn_save.setFixedWidth(90)
        btn_save.clicked.connect(self._save)
        bl.addWidget(btn_cancel)
        bl.addWidget(btn_save)
        root.addWidget(btn_row)

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _tab_network(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(16)

        l.addWidget(SectionTitle("IP Addresses"))
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._f_pc  = QLineEdit()
        self._f_dgx = QLineEdit()
        form.addRow("PC IP Address:", self._f_pc)
        form.addRow("DGX IP Address:", self._f_dgx)
        l.addLayout(form)

        l.addWidget(SectionTitle("Ports"))
        pform = QFormLayout()
        pform.setSpacing(10)
        pform.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._f_rpc   = QSpinBox(); self._f_rpc.setRange(1024, 65535)
        self._f_video = QSpinBox(); self._f_video.setRange(1024, 65535)
        self._f_input = QSpinBox(); self._f_input.setRange(1024, 65535)
        pform.addRow("RPC / Control Port:", self._f_rpc)
        pform.addRow("Video Stream Port:",  self._f_video)
        pform.addRow("Input Events Port:",  self._f_input)
        l.addLayout(pform)

        note = QLabel(
            "Default ports: 22010 (RPC), 22011 (Video), 22012 (Input)\n"
            "Changes take effect on next connection."
        )
        note.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        l.addWidget(note)
        l.addStretch()
        return w

    def _tab_display(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(16)

        l.addWidget(SectionTitle("Display Mode"))
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._f_mode = QComboBox()
        self._f_mode.addItems(["Window Mode", "Virtual Display Mode"])
        form.addRow("Mode:", self._f_mode)

        self._f_fps = QSpinBox()
        self._f_fps.setRange(5, 60)
        self._f_fps.setSuffix("  fps")
        form.addRow("Target FPS:", self._f_fps)

        self._f_quality = QSpinBox()
        self._f_quality.setRange(40, 100)
        self._f_quality.setSuffix(" %")
        form.addRow("JPEG Quality:", self._f_quality)
        l.addLayout(form)

        mode_note = QLabel(
            "Window Mode: DGX desktop in a floating window.\n"
            "Virtual Display Mode: DGX appears as an additional monitor.\n"
            "Restart required after mode change."
        )
        mode_note.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        mode_note.setWordWrap(True)
        l.addWidget(mode_note)

        l.addWidget(SectionTitle("Virtual Display Side"))
        self._f_virt_side = QComboBox()
        self._f_virt_side.addItems([
            "Right of all monitors",
            "Left of all monitors",
            "Above all monitors",
            "Below all monitors"
        ])
        l.addWidget(self._f_virt_side)

        l.addStretch()
        return w

    def _tab_behavior(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(12)

        l.addWidget(SectionTitle("Startup"))
        self._cb_autoconnect   = QCheckBox("Auto-connect when app starts")
        self._cb_startmin      = QCheckBox("Start minimized to system tray")
        l.addWidget(self._cb_autoconnect)
        l.addWidget(self._cb_startmin)

        l.addWidget(SectionTitle("Display"))
        self._cb_fps   = QCheckBox("Show FPS overlay on canvas")
        self._cb_ping  = QCheckBox("Show ping in status bar")
        l.addWidget(self._cb_fps)
        l.addWidget(self._cb_ping)

        l.addWidget(SectionTitle("File Transfer"))
        self._cb_confirm_del = QCheckBox("Confirm before deleting remote files")
        l.addWidget(self._cb_confirm_del)

        l.addStretch()
        return w

    def _tab_dgxinfo(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(12)

        # Cards row
        self._cards_row = QHBoxLayout()
        self._card_hostname = InfoCard("Hostname")
        self._card_os       = InfoCard("OS")
        self._card_disk     = InfoCard("Free Disk")
        self._cards_row.addWidget(self._card_hostname)
        self._cards_row.addWidget(self._card_os)
        self._cards_row.addWidget(self._card_disk)
        l.addLayout(self._cards_row)

        # GPU info
        l.addWidget(SectionTitle("GPUs"))
        self._lbl_gpu = QLabel("—")
        self._lbl_gpu.setWordWrap(True)
        self._lbl_gpu.setStyleSheet(
            f"background: {BG_SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;"
            f"padding: 8px; color: {TEXT_MAIN}; font-size: 12px;"
            f"font-family: 'Cascadia Code', 'Consolas', monospace;"
        )
        l.addWidget(self._lbl_gpu)

        btn_refresh = QPushButton("  Refresh")
        btn_refresh.setFixedWidth(100)
        btn_refresh.clicked.connect(self._fetch_dgx_info)
        l.addWidget(btn_refresh)

        l.addStretch()

        if self._conn and self._conn.connected:
            QTimer.singleShot(300, self._fetch_dgx_info)

        return w

    def _tab_tools(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(16, 16, 16, 16)
        l.setSpacing(12)

        l.addWidget(SectionTitle("Shortcuts"))
        btn_shortcut = QPushButton("  Create Desktop Shortcut (PC)")
        btn_shortcut.clicked.connect(self._create_shortcut)
        l.addWidget(btn_shortcut)

        l.addWidget(SectionTitle("Windows Firewall"))
        fw_label = QLabel(
            "Run in PowerShell as Administrator:"
        )
        fw_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        l.addWidget(fw_label)
        fw_cmd = QTextEdit()
        fw_cmd.setReadOnly(True)
        fw_cmd.setFixedHeight(80)
        fw_cmd.setPlainText(
            "New-NetFirewallRule `\n"
            "  -DisplayName 'DGX-Desktop-Remote' `\n"
            "  -Direction Inbound -Protocol TCP `\n"
            "  -LocalPort 12010 -RemoteAddress 10.0.0.1 `\n"
            "  -Action Allow"
        )
        l.addWidget(fw_cmd)
        btn_copy = QPushButton("  Copy Command")
        btn_copy.setFixedWidth(140)
        btn_copy.clicked.connect(
            lambda: QApplication_clipboard(fw_cmd.toPlainText())
        )
        l.addWidget(btn_copy)

        l.addWidget(SectionTitle("Reset"))
        btn_reset = QPushButton("  Reset All Settings to Default")
        btn_reset.setProperty("class", "danger")
        btn_reset.clicked.connect(self._reset_config)
        l.addWidget(btn_reset)

        l.addStretch()
        return w

    # ------------------------------------------------------------------
    # Populate / Save
    # ------------------------------------------------------------------

    def _populate(self):
        c = self.config
        self._f_pc.setText(c.pc_ip)
        self._f_dgx.setText(c.dgx_ip)
        self._f_rpc.setValue(c.rpc_port)
        self._f_video.setValue(c.video_port)
        self._f_input.setValue(c.input_port)
        self._f_mode.setCurrentIndex(0 if c.display_mode == "window" else 1)
        self._f_fps.setValue(c.target_fps)
        self._f_quality.setValue(c.jpeg_quality)
        side_map = {"right": 0, "left": 1, "top": 2, "bottom": 3}
        self._f_virt_side.setCurrentIndex(side_map.get(c.virt_side, 0))
        self._cb_autoconnect.setChecked(c.auto_connect)
        self._cb_startmin.setChecked(c.start_minimized)
        self._cb_fps.setChecked(c.show_fps)
        self._cb_ping.setChecked(c.show_ping)
        self._cb_confirm_del.setChecked(c.confirm_file_del)

    def _save(self):
        c = self.config
        old_mode = c.display_mode

        c.pc_ip        = self._f_pc.text().strip()
        c.dgx_ip       = self._f_dgx.text().strip()
        c.rpc_port     = self._f_rpc.value()
        c.video_port   = self._f_video.value()
        c.input_port   = self._f_input.value()
        c.display_mode = "window" if self._f_mode.currentIndex() == 0 else "virtual_display"
        c.target_fps   = self._f_fps.value()
        c.jpeg_quality = self._f_quality.value()
        sides = ["right", "left", "top", "bottom"]
        c.virt_side    = sides[self._f_virt_side.currentIndex()]
        c.auto_connect    = self._cb_autoconnect.isChecked()
        c.start_minimized = self._cb_startmin.isChecked()
        c.show_fps        = self._cb_fps.isChecked()
        c.show_ping       = self._cb_ping.isChecked()
        c.confirm_file_del= self._cb_confirm_del.isChecked()
        c.save()

        if old_mode != c.display_mode:
            QMessageBox.information(
                self, "Restart Required",
                f"Display mode changed to {c.display_mode.replace('_',' ').title()}.\n"
                "Please restart the application."
            )
        self.accept()

    # ------------------------------------------------------------------
    # DGX Info fetch
    # ------------------------------------------------------------------

    def _fetch_dgx_info(self):
        t = _SystemInfoThread(self._conn)
        t.result.connect(self._on_dgx_info)
        t.start()

    def _on_dgx_info(self, info: dict):
        if not info.get("ok"):
            return
        self._card_hostname.set_value(info.get("hostname", "—"))
        os_str = info.get("os", "—")
        # Shorten long os string
        if len(os_str) > 30:
            os_str = os_str[:28] + "…"
        self._card_os.set_value(os_str)
        self._card_disk.set_value(f"{info.get('disk_free_gb', '—')} GB")
        gpus = info.get("gpus", [])
        if gpus:
            lines = []
            for g in gpus:
                lines.append(
                    f"{g['name']}  "
                    f"{g['memory_free_mb']}/{g['memory_total_mb']} MB free"
                )
            self._lbl_gpu.setText("\n".join(lines))
        else:
            self._lbl_gpu.setText("No GPU info available")

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _create_shortcut(self):
        try:
            import sys
            from pathlib import Path as P
            sys.path.insert(0, str(P(__file__).parents[2]))
            from create_shortcuts import create_windows_shortcut
            create_windows_shortcut()
            QMessageBox.information(self, "Shortcut", "Desktop shortcut created.")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _reset_config(self):
        if QMessageBox.question(
            self, "Reset Settings",
            "Reset all settings to defaults?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            from config import CONFIG_FILE
            if CONFIG_FILE.exists():
                CONFIG_FILE.unlink()
            new_cfg = Config()
            new_cfg.save()
            self.config.__dict__.update(new_cfg.__dict__)
            self._populate()


def QApplication_clipboard(text: str):
    from PyQt6.QtWidgets import QApplication
    QApplication.clipboard().setText(text)
