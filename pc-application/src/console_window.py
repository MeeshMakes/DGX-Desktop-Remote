"""
pc-application/src/console_window.py
─────────────────────────────────────────────────────────────────────────────
Live log/error console popup.

• A QPlainTextEdit that intercepts every log record emitted by Python's
  logging framework (via a custom Handler) and writes it in real time.
• WARNING and above entries are coloured amber; CRITICAL/ERROR are red.
• Auto-opens itself if an ERROR or CRITICAL is emitted.
• Toolbar: Clear · Copy All · Auto-scroll toggle · Level filter.
• Thread-safe: records are marshalled to the GUI thread through a signal.
─────────────────────────────────────────────────────────────────────────────
"""

import logging
import time
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QPushButton, QComboBox, QLabel, QCheckBox, QSizePolicy,
    QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, pyqtSlot
from PyQt6.QtGui  import QTextCharFormat, QColor, QFont, QTextCursor


# ── Colours ───────────────────────────────────────────────────────────────
_COL = {
    "DEBUG":    "#6A6A9A",
    "INFO":     "#B0B8E0",
    "WARNING":  "#F5A623",
    "ERROR":    "#FF4F5E",
    "CRITICAL": "#FF1F3E",
}
_LEVEL_ORDER = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_PANEL_BG  = "#0B0B12"
_BORDER    = "#2A2A42"
_BTN_BG    = "#181824"
_BTN_HVR   = "#252538"
_TEXT_MAIN = "#E4E4F4"
_TEXT_DIM  = "#7070A0"
_ACCENT    = "#6C63FF"


# ─────────────────────────────────────────────────────────────────────────
# Signal bridge (enables thread-safe emit from logging handler → GUI)
# ─────────────────────────────────────────────────────────────────────────

class _LogBridge(QObject):
    record_emitted = pyqtSignal(str, str, str)  # level_name, timestamp, message


# ─────────────────────────────────────────────────────────────────────────
# Custom logging.Handler
# ─────────────────────────────────────────────────────────────────────────

class _QtLogHandler(logging.Handler):
    """Sends every log record through the Qt signal system."""

    def __init__(self, bridge: _LogBridge):
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord):
        try:
            msg       = self.format(record)
            level     = record.levelname
            ts        = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
            self._bridge.record_emitted.emit(level, ts, msg)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────
# Console Window
# ─────────────────────────────────────────────────────────────────────────

class ConsoleWindow(QDialog):
    """
    Pop-out log console.  Create ONE instance per application and call
    .attach() after creation to start capturing logs.
    """

    # Emits the worst log level seen so far: "DEBUG"|"INFO"|"WARNING"|"ERROR"|"CRITICAL"
    severity_changed = pyqtSignal(str)

    def __init__(self, parent=None, title: str = "Console"):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(f"🖥  {title}")
        self.setMinimumSize(860, 480)
        self.resize(1000, 540)
        self._min_level = logging.DEBUG
        self._auto_scroll = True
        self._records: list[tuple[str, str, str]] = []   # for filtering
        self._worst_level = "INFO"  # track worst level for dot signal

        self._bridge = _LogBridge()
        self._handler = _QtLogHandler(self._bridge)
        self._handler.setFormatter(
            logging.Formatter("%(name)-22s  %(levelname)-8s  %(message)s")
        )
        # Wire signal to slot
        self._bridge.record_emitted.connect(self._on_record)

        self._build_ui()
        self.setStyleSheet(self._stylesheet())

    # ── Public API ───────────────────────────────────────────────────

    def attach(self, logger_name: str = None, level: int = logging.DEBUG):
        """
        Attach handler to root logger (or a named logger).
        Call once after creating the window.
        """
        self._handler.setLevel(level)
        target = logging.getLogger(logger_name)
        target.addHandler(self._handler)
        # Ensure root level allows all records through
        if target.level == logging.NOTSET or target.level > level:
            target.setLevel(level)

    def detach(self):
        root = logging.getLogger()
        root.removeHandler(self._handler)

    # ── Build UI ─────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Toolbar ──────────────────────────────────────────────────
        bar = QHBoxLayout()
        bar.setSpacing(6)

        lbl = QLabel("Console")
        lbl.setStyleSheet(f"color: {_ACCENT}; font-weight: 700; font-size: 13px;")
        bar.addWidget(lbl)

        bar.addSpacing(8)

        # Level filter
        bar.addWidget(QLabel("Level:"))
        self._level_cb = QComboBox()
        self._level_cb.addItems(_LEVEL_ORDER)
        self._level_cb.setCurrentText("DEBUG")
        self._level_cb.setFixedWidth(90)
        self._level_cb.currentTextChanged.connect(self._on_level_change)
        bar.addWidget(self._level_cb)

        bar.addStretch()

        # Auto-scroll toggle
        self._chk_scroll = QCheckBox("Auto-scroll")
        self._chk_scroll.setChecked(True)
        self._chk_scroll.toggled.connect(self._on_autoscroll_toggle)
        bar.addWidget(self._chk_scroll)

        # Copy All
        btn_copy = QPushButton("Copy All")
        btn_copy.setFixedWidth(80)
        btn_copy.clicked.connect(self._copy_all)
        bar.addWidget(btn_copy)

        # Clear
        btn_clear = QPushButton("Clear")
        btn_clear.setFixedWidth(60)
        btn_clear.clicked.connect(self._clear)
        bar.addWidget(btn_clear)

        root.addLayout(bar)

        # ── Log view ─────────────────────────────────────────────────
        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._view.setFont(QFont("Cascadia Code, Consolas, Courier New", 10))
        self._view.setMaximumBlockCount(5000)
        self._view.setStyleSheet(
            f"QPlainTextEdit {{"
            f"  background: {_PANEL_BG};"
            f"  color: {_TEXT_MAIN};"
            f"  border: 1px solid {_BORDER};"
            f"  border-radius: 6px;"
            f"  padding: 6px;"
            f"}}"
        )
        root.addWidget(self._view)

        # ── Status bar ───────────────────────────────────────────────
        self._lbl_status = QLabel("Ready")
        self._lbl_status.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 11px;")
        root.addWidget(self._lbl_status)

    # ── Slots ─────────────────────────────────────────────────────────

    @pyqtSlot(str, str, str)
    def _on_record(self, level: str, ts: str, msg: str):
        self._records.append((level, ts, msg))
        level_no = getattr(logging, level, logging.DEBUG)
        if level_no < self._min_level:
            return
        self._append(level, ts, msg)

        # Auto-open on errors
        if level_no >= logging.ERROR and not self.isVisible():
            self.show()
            self.raise_()

    def _append(self, level: str, ts: str, msg: str):
        colour = _COL.get(level, _TEXT_MAIN)
        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # Timestamp dim
        fmt_ts = QTextCharFormat()
        fmt_ts.setForeground(QColor(_TEXT_DIM))
        cursor.insertText(f"[{ts}] ", fmt_ts)

        # Level badge
        fmt_lvl = QTextCharFormat()
        fmt_lvl.setForeground(QColor(colour))
        if level in ("ERROR", "CRITICAL"):
            fmt_lvl.setFontWeight(700)
        cursor.insertText(f"{level:<8} ", fmt_lvl)

        # Message
        fmt_msg = QTextCharFormat()
        fmt_msg.setForeground(QColor(colour if level in ("WARNING", "ERROR", "CRITICAL") else _TEXT_MAIN))
        cursor.insertText(msg + "\n", fmt_msg)

        if self._auto_scroll:
            self._view.setTextCursor(cursor)
            self._view.ensureCursorVisible()

        # Status bar counts
        errors = sum(1 for r in self._records if r[0] in ("ERROR", "CRITICAL"))
        warns  = sum(1 for r in self._records if r[0] == "WARNING")
        self._lbl_status.setText(
            f"{len(self._records)} records  |"
            f"  ⚠ {warns} warnings  |  ✖ {errors} errors"
        )
        if errors:
            self._lbl_status.setStyleSheet(f"color: {_COL['ERROR']}; font-size: 11px;")
            if self._worst_level not in ("ERROR", "CRITICAL"):
                self._worst_level = "ERROR"
                self.severity_changed.emit("ERROR")
        elif warns:
            self._lbl_status.setStyleSheet(f"color: {_COL['WARNING']}; font-size: 11px;")
            if self._worst_level not in ("ERROR", "CRITICAL", "WARNING"):
                self._worst_level = "WARNING"
                self.severity_changed.emit("WARNING")

    def _on_level_change(self, level_name: str):
        self._min_level = getattr(logging, level_name, logging.DEBUG)
        # Re-render with new filter
        self._view.clear()
        for level, ts, msg in self._records:
            if getattr(logging, level, 0) >= self._min_level:
                self._append(level, ts, msg)

    def _on_autoscroll_toggle(self, checked: bool):
        self._auto_scroll = checked

    def _copy_all(self):
        QApplication.clipboard().setText(self._view.toPlainText())
        self._lbl_status.setText("Copied to clipboard ✓")

    def _clear(self):
        self._view.clear()
        self._records.clear()
        self._worst_level = "INFO"
        self._lbl_status.setText("Cleared")

    # ── Stylesheet ────────────────────────────────────────────────────

    def _stylesheet(self) -> str:
        return f"""
        QDialog {{
            background: {_PANEL_BG};
            color: {_TEXT_MAIN};
        }}
        QLabel {{
            background: transparent;
            color: {_TEXT_MAIN};
            font-size: 12px;
        }}
        QPushButton {{
            background: {_BTN_BG};
            color: {_TEXT_MAIN};
            border: 1px solid {_BORDER};
            border-radius: 4px;
            padding: 4px 10px;
            font-size: 12px;
        }}
        QPushButton:hover {{
            background: {_BTN_HVR};
            border-color: {_ACCENT};
        }}
        QComboBox {{
            background: {_BTN_BG};
            color: {_TEXT_MAIN};
            border: 1px solid {_BORDER};
            border-radius: 4px;
            padding: 3px 8px;
            font-size: 12px;
        }}
        QComboBox QAbstractItemView {{
            background: {_BTN_BG};
            color: {_TEXT_MAIN};
            selection-background-color: {_ACCENT};
        }}
        QCheckBox {{
            color: {_TEXT_MAIN};
            font-size: 12px;
            spacing: 6px;
        }}
        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid {_BORDER};
            border-radius: 3px;
            background: {_BTN_BG};
        }}
        QCheckBox::indicator:checked {{
            background: {_ACCENT};
            border-color: {_ACCENT};
        }}
        """

    def closeEvent(self, event):
        # Hide instead of destroy so it can be re-shown
        event.ignore()
        self.hide()
