"""
pc-application/src/widgets.py
Reusable custom widgets for the DGX Desktop Remote GUI.
"""

from PyQt6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QVBoxLayout, QFrame,
    QPushButton, QSizePolicy, QGraphicsOpacityEffect
)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QSize, pyqtProperty
from PyQt6.QtGui import QFont, QColor, QPainter, QPen, QBrush

from theme import (
    ACCENT, SUCCESS, ERROR, WARNING, TEXT_DIM, TEXT_MAIN,
    BG_RAISED, BG_SURFACE, BORDER, BG_HOVER
)


# ──────────────────────────────────────────────────────────────────────
# StatusPill — animated connection status indicator
# ──────────────────────────────────────────────────────────────────────

class StatusPill(QLabel):
    """Small pill label showing connection state with a colored dot."""

    _STATES = {
        "disconnected": (ERROR,   "● Disconnected"),
        "connecting":   (WARNING, "● Connecting…"),
        "connected":    (SUCCESS, "● Connected"),
        "error":        (ERROR,   "● Error"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._state = "disconnected"
        self._apply("disconnected")

    def set_state(self, state: str):
        self._state = state
        self._apply(state)

    def _apply(self, state: str):
        color, text = self._STATES.get(state, (TEXT_DIM, "● Unknown"))
        self.setText(text)
        self.setStyleSheet(
            f"color: {color};"
            f"background: {color}18;"
            f"border: 1px solid {color}44;"
            f"border-radius: 10px;"
            f"padding: 2px 12px;"
            f"font-size: 11px;"
            f"font-weight: 600;"
        )


# ──────────────────────────────────────────────────────────────────────
# StatBadge — small metric pill for fps, ping, etc.
# ──────────────────────────────────────────────────────────────────────

class StatBadge(QLabel):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label = label
        self._set_value("—")

    def set_value(self, val: str):
        self._set_value(val)

    def _set_value(self, v: str):
        self.setText(f"{self._label}  {v}")
        self.setStyleSheet(
            f"color: {TEXT_DIM};"
            f"background: {BG_SURFACE};"
            f"border: 1px solid {BORDER};"
            f"border-radius: 8px;"
            f"padding: 2px 10px;"
            f"font-size: 11px;"
            f"font-family: 'Cascadia Code', 'Consolas', monospace;"
        )


# ──────────────────────────────────────────────────────────────────────
# Divider
# ──────────────────────────────────────────────────────────────────────

class HDivider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.HLine)
        self.setStyleSheet(f"background: {BORDER}; max-height: 1px; border: none;")
        self.setFixedHeight(1)


class VDivider(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.VLine)
        self.setStyleSheet(f"background: {BORDER}; max-width: 1px; border: none;")
        self.setFixedWidth(1)


# ──────────────────────────────────────────────────────────────────────
# SectionTitle
# ──────────────────────────────────────────────────────────────────────

class SectionTitle(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setStyleSheet(
            f"color: {TEXT_DIM};"
            f"font-size: 10px;"
            f"font-weight: 700;"
            f"letter-spacing: 2px;"
            f"padding: 0 0 4px 0;"
        )


# ──────────────────────────────────────────────────────────────────────
# InfoCard — key/value display card
# ──────────────────────────────────────────────────────────────────────

class InfoCard(QWidget):
    """A small card showing a key and a value."""

    def __init__(self, key: str, value: str = "—", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        self._key_lbl = QLabel(key.upper())
        self._key_lbl.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 9px; font-weight: 700; letter-spacing: 1px;"
        )
        self._val_lbl = QLabel(value)
        self._val_lbl.setStyleSheet(
            f"color: {TEXT_MAIN}; font-size: 13px; font-weight: 600;"
        )
        layout.addWidget(self._key_lbl)
        layout.addWidget(self._val_lbl)

        self.setStyleSheet(
            f"background: {BG_SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;"
        )

    def set_value(self, v: str):
        self._val_lbl.setText(v)


# ──────────────────────────────────────────────────────────────────────
# ToolButton — icon toolbar button
# ──────────────────────────────────────────────────────────────────────

class ToolButton(QPushButton):
    def __init__(self, icon_text: str, tooltip: str = "", checkable: bool = False,
                 parent=None):
        super().__init__(icon_text, parent)
        self.setToolTip(tooltip)
        self.setCheckable(checkable)
        self.setProperty("class", "toolbar")
        self.setFixedSize(QSize(36, 32))
        self.setFont(QFont("Segoe UI Emoji", 14))

    def setProperty(self, name, value):
        super().setProperty(name, value)
        self.style().unpolish(self)
        self.style().polish(self)
