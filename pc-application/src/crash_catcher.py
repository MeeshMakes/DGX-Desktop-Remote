"""
pc-application/src/crash_catcher.py
────────────────────────────────────────────────────────────────────────
Global crash / unhandled-exception catcher.

Install with:
    from crash_catcher import install_crash_handler
    install_crash_handler(app)

Any uncaught exception — in any thread — will:
 1. Be logged via Python's logging module (auto-appears in ConsoleWindow).
 2. Pop up a CrashDialog that:
      • Shows the full formatted traceback.
      • Has a Copy button (copies to clipboard).
      • Has a Close button (lets the user close the dialog manually).
      • Does NOT close or exit the application.
 3. Keep the main window alive so the user can inspect state.
"""

import sys
import traceback
import logging
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QPlainTextEdit, QApplication, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QMetaObject, Q_ARG
from PyQt6.QtGui  import QFont, QColor, QPalette

log = logging.getLogger("pc.crash")

# ── Colours ──────────────────────────────────────────────────────────────
_BG     = "#0B0B12"
_RAISED = "#181824"
_BORDER = "#2A2A42"
_RED    = "#FF4F5E"
_AMBER  = "#F5A623"
_TEXT   = "#E4E4F4"
_DIM    = "#7070A0"
_ACCENT = "#6C63FF"


# ─────────────────────────────────────────────────────────────────────────
# Signal bridge so worker-thread crashes can marshal to the GUI thread
# ─────────────────────────────────────────────────────────────────────────

class _CrashBridge(QObject):
    crash_occurred = pyqtSignal(str, str)   # title, formatted_traceback

_bridge: "_CrashBridge | None" = None


def _get_bridge() -> "_CrashBridge":
    global _bridge
    if _bridge is None:
        _bridge = _CrashBridge()
    return _bridge


# ─────────────────────────────────────────────────────────────────────────
# Crash Dialog
# ─────────────────────────────────────────────────────────────────────────

class CrashDialog(QDialog):
    """
    Modal-ish window shown on unhandled exception.
    Not truly modal so the main window stays responsive.
    Cannot be closed via the window ✕ button (only via the Close button).
    """

    def __init__(self, title: str, body: str, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("⚠  Crash Report — DGX Desktop Remote")
        self.setMinimumSize(780, 460)
        self.resize(900, 520)
        self._body = body
        self._build_ui(title, body)
        self._apply_style()

    def _build_ui(self, title: str, body: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        # ── Red header strip ──────────────────────────────────────────
        hdr = QWidget()
        hdr.setFixedHeight(48)
        hdr.setStyleSheet(
            f"background: #330009; border: 1px solid {_RED}; border-radius: 6px;"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(14, 0, 14, 0)
        icon = QLabel("💥")
        icon.setStyleSheet("font-size: 22px; background: transparent;")
        hl.addWidget(icon)
        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: {_RED}; font-size: 14px; font-weight: 700;"
            f"background: transparent; padding-left: 6px;"
        )
        lbl.setWordWrap(True)
        hl.addWidget(lbl, 1)
        root.addWidget(hdr)

        # ── Timestamp + note ──────────────────────────────────────────
        ts = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        note = QLabel(
            f"Crash recorded at  {ts}\n"
            "The application has been kept open so you can copy this report.\n"
            "Click  Copy Report  then  Close  when you are done."
        )
        note.setStyleSheet(f"color: {_DIM}; font-size: 11px;")
        note.setWordWrap(True)
        root.addWidget(note)

        # ── Traceback view ────────────────────────────────────────────
        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setPlainText(body)
        self._view.setFont(QFont("Cascadia Code, Consolas, Courier New", 9))
        self._view.setStyleSheet(
            f"background: {_BG}; color: {_RED};"
            f"border: 1px solid {_BORDER}; border-radius: 6px; padding: 8px;"
        )
        root.addWidget(self._view, 1)

        # ── Button row ────────────────────────────────────────────────
        br = QHBoxLayout()
        br.setSpacing(8)
        br.addStretch()

        btn_copy = QPushButton("  Copy Report")
        btn_copy.setFixedHeight(30)
        btn_copy.setMinimumWidth(120)
        btn_copy.clicked.connect(self._copy)
        btn_copy.setStyleSheet(
            f"QPushButton {{ background: #1a0a2e; color: {_ACCENT};"
            f"border: 1px solid {_ACCENT}; border-radius: 4px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: #2a1a4e; }}"
        )
        br.addWidget(btn_copy)

        btn_close = QPushButton("  Close")
        btn_close.setFixedHeight(30)
        btn_close.setMinimumWidth(80)
        btn_close.clicked.connect(self.accept)
        btn_close.setStyleSheet(
            f"QPushButton {{ background: #1a0005; color: {_RED};"
            f"border: 1px solid {_RED}; border-radius: 4px; font-size: 12px; }}"
            f"QPushButton:hover {{ background: #2a000a; }}"
        )
        br.addWidget(btn_close)

        root.addLayout(br)

    def _copy(self):
        QApplication.clipboard().setText(self._body)
        # Brief feedback
        sender = self.sender()
        if sender:
            sender.setText("  Copied ✓")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(2000, lambda: sender.setText("  Copy Report"))

    def _apply_style(self):
        self.setStyleSheet(
            f"QDialog {{ background: {_RAISED}; color: {_TEXT}; }}"
            f"QLabel  {{ background: transparent; }}"
        )

    def closeEvent(self, event):
        # Swallow window ✕ — must use the Close button
        event.ignore()


# Import here to avoid circular at module level
from PyQt6.QtWidgets import QWidget


# ─────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────

def _show_crash(title: str, tb_text: str):
    """Show CrashDialog. Must be called on the GUI thread."""
    try:
        app = QApplication.instance()
        parent = None
        if app:
            # Try to find the main window
            for w in app.topLevelWidgets():
                if w.isVisible() and w.__class__.__name__ == "MainWindow":
                    parent = w
                    break
        dlg = CrashDialog(title, tb_text, parent)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
    except Exception as e:
        # Last resort: print to stderr
        print(f"[crash_catcher] Could not show dialog: {e}", file=sys.stderr)
        print(tb_text, file=sys.stderr)


def install_crash_handler(app=None):
    """
    Install global exception hooks.
    Call once from main() after creating QApplication.
    """
    bridge = _get_bridge()
    bridge.crash_occurred.connect(lambda title, tb: _show_crash(title, tb))

    # ── Python main-thread uncaught exception ─────────────────────────
    _orig_excepthook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            _orig_excepthook(exc_type, exc_value, exc_tb)
            return
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        title   = f"{exc_type.__name__}: {exc_value}"
        log.critical("UNHANDLED EXCEPTION\n%s", tb_text)
        bridge.crash_occurred.emit(title, tb_text)

    sys.excepthook = _excepthook

    # ── Threading — catch crashes in worker threads ───────────────────
    import threading
    _orig_threading_hook = threading.excepthook

    def _thread_excepthook(args):
        if args.exc_type is SystemExit:
            return
        tb_text = "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        title = f"Thread crash — {threading.current_thread().name}: {args.exc_type.__name__}: {args.exc_value}"
        log.critical("THREAD EXCEPTION\n%s", tb_text)
        bridge.crash_occurred.emit(title, tb_text)

    threading.excepthook = _thread_excepthook

    log.debug("Crash handler installed.")
