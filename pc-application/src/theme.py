"""
pc-application/src/theme.py
Global dark theme stylesheet + palette for the entire PC application.
"""

ACCENT      = "#6C63FF"   # Purple-indigo accent
ACCENT_DARK = "#534BD4"
SUCCESS     = "#22D47E"
WARNING     = "#F5A623"
ERROR       = "#FF4F5E"
DIM         = "#4A4A6A"
BG_DEEP     = "#0B0B12"   # Deepest background (window base)
BG_BASE     = "#111118"   # Normal widget background
BG_RAISED   = "#181824"   # Raised panels, sidebars
BG_SURFACE  = "#1E1E2E"   # Cards, input fields
BG_HOVER    = "#252538"
BORDER      = "#2A2A42"
TEXT_MAIN   = "#E4E4F4"
TEXT_DIM    = "#7070A0"
TEXT_MUTED  = "#404060"


APP_STYLESHEET = f"""
/* ===== Base ===================================================== */
QWidget {{
    background-color: {BG_BASE};
    color: {TEXT_MAIN};
    font-family: "Segoe UI", "Inter", "Ubuntu", sans-serif;
    font-size: 13px;
    border: none;
    outline: none;
}}
QMainWindow {{
    background-color: {BG_DEEP};
}}

/* ===== QLabel =================================================== */
QLabel {{
    background: transparent;
    color: {TEXT_MAIN};
}}
QLabel[class="dim"] {{
    color: {TEXT_DIM};
    font-size: 11px;
}}
QLabel[class="heading"] {{
    font-size: 16px;
    font-weight: 600;
    color: {TEXT_MAIN};
}}
QLabel[class="subheading"] {{
    font-size: 12px;
    color: {TEXT_DIM};
}}
QLabel[class="pill-connected"] {{
    background-color: {SUCCESS}22;
    color: {SUCCESS};
    border: 1px solid {SUCCESS}55;
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel[class="pill-disconnected"] {{
    background-color: {ERROR}22;
    color: {ERROR};
    border: 1px solid {ERROR}44;
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel[class="pill-connecting"] {{
    background-color: {WARNING}22;
    color: {WARNING};
    border: 1px solid {WARNING}44;
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
}}

/* ===== QPushButton ============================================== */
QPushButton {{
    background-color: {BG_SURFACE};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 16px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {BG_HOVER};
    border-color: {DIM};
}}
QPushButton:pressed {{
    background-color: {ACCENT_DARK};
    border-color: {ACCENT};
}}
QPushButton:disabled {{
    color: {TEXT_MUTED};
    border-color: {TEXT_MUTED};
}}
QPushButton[class="primary"] {{
    background-color: {ACCENT};
    color: white;
    border: none;
    font-weight: 600;
}}
QPushButton[class="primary"]:hover {{
    background-color: {ACCENT_DARK};
}}
QPushButton[class="primary"]:pressed {{
    background-color: #3E37A8;
}}
QPushButton[class="danger"] {{
    background-color: transparent;
    color: {ERROR};
    border: 1px solid {ERROR}55;
}}
QPushButton[class="danger"]:hover {{
    background-color: {ERROR}22;
}}
QPushButton[class="success"] {{
    background-color: {SUCCESS}22;
    color: {SUCCESS};
    border: 1px solid {SUCCESS}55;
}}
QPushButton[class="toolbar"] {{
    background-color: transparent;
    border: none;
    border-radius: 6px;
    padding: 6px 10px;
    color: {TEXT_DIM};
    font-size: 18px;
}}
QPushButton[class="toolbar"]:hover {{
    background-color: {BG_HOVER};
    color: {TEXT_MAIN};
}}
QPushButton[class="toolbar"]:checked {{
    background-color: {ACCENT}33;
    color: {ACCENT};
}}

/* ===== QLineEdit ================================================ */
QLineEdit {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 7px 10px;
    color: {TEXT_MAIN};
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}
QLineEdit:read-only {{
    color: {TEXT_DIM};
}}

/* ===== QSpinBox / QComboBox ===================================== */
QSpinBox, QDoubleSpinBox {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    color: {TEXT_MAIN};
    selection-background-color: {ACCENT};
}}
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    background: {BG_RAISED};
    border: none;
    width: 16px;
}}
QComboBox {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    color: {TEXT_MAIN};
}}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {BG_RAISED};
    border: 1px solid {BORDER};
    color: {TEXT_MAIN};
    selection-background-color: {ACCENT};
    outline: none;
}}

/* ===== QCheckBox ================================================ */
QCheckBox {{
    spacing: 8px;
    color: {TEXT_MAIN};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {BORDER};
    background: {BG_SURFACE};
}}
QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    image: url("data:image/png;base64,");
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}

/* ===== QGroupBox ================================================ */
QGroupBox {{
    background-color: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 20px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
    color: {TEXT_DIM};
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    background: {BG_RAISED};
    color: {TEXT_DIM};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1px;
}}

/* ===== QTabWidget =============================================== */
QTabWidget::pane {{
    background-color: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    border-top-left-radius: 0px;
}}
QTabBar::tab {{
    background: {BG_BASE};
    color: {TEXT_DIM};
    padding: 8px 18px;
    border: 1px solid {BORDER};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
    font-size: 12px;
}}
QTabBar::tab:selected {{
    background: {BG_RAISED};
    color: {TEXT_MAIN};
    border-bottom: 2px solid {ACCENT};
}}
QTabBar::tab:hover:!selected {{
    background: {BG_HOVER};
    color: {TEXT_MAIN};
}}

/* ===== QListWidget ============================================== */
QListWidget {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 6px 8px;
    border-radius: 4px;
    color: {TEXT_MAIN};
}}
QListWidget::item:selected {{
    background-color: {ACCENT}33;
    color: {ACCENT};
}}
QListWidget::item:hover {{ background-color: {BG_HOVER}; }}

/* ===== QScrollBar =============================================== */
QScrollBar:vertical {{
    background: {BG_BASE};
    width: 8px;
    margin: 0;
    border-radius: 4px;
}}
QScrollBar::handle:vertical {{
    background: {DIM};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {BG_BASE};
    height: 8px;
    border-radius: 4px;
}}
QScrollBar::handle:horizontal {{
    background: {DIM};
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{ background: {ACCENT}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ===== QProgressBar ============================================= */
QProgressBar {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    text-align: center;
    color: {TEXT_MAIN};
    font-size: 11px;
    height: 16px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {ACCENT}, stop:1 {SUCCESS});
    border-radius: 5px;
}}

/* ===== QStatusBar =============================================== */
QStatusBar {{
    background-color: {BG_DEEP};
    color: {TEXT_DIM};
    font-size: 11px;
    border-top: 1px solid {BORDER};
    padding: 2px 8px;
}}
QStatusBar QLabel {{
    color: {TEXT_DIM};
    font-size: 11px;
    padding: 0 6px;
}}

/* ===== QToolBar ================================================= */
QToolBar {{
    background-color: {BG_RAISED};
    border-bottom: 1px solid {BORDER};
    spacing: 4px;
    padding: 4px 8px;
}}
QToolBar::separator {{
    background: {BORDER};
    width: 1px;
    margin: 4px 6px;
}}

/* ===== QTextEdit ================================================ */
QTextEdit, QPlainTextEdit {{
    background-color: {BG_SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    color: {TEXT_MAIN};
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
    padding: 6px;
    selection-background-color: {ACCENT};
}}

/* ===== QDialog ================================================== */
QDialog {{
    background-color: {BG_BASE};
}}

/* ===== QWizard ================================================== */
QWizard {{
    background-color: {BG_BASE};
}}
QWizardPage {{
    background-color: {BG_BASE};
}}

/* ===== QMessageBox ============================================== */
QMessageBox {{
    background-color: {BG_BASE};
}}
QMessageBox QPushButton {{
    min-width: 80px;
}}

/* ===== QFrame separator ========================================= */
QFrame[frameShape="4"],   /* HLine */
QFrame[frameShape="5"] {{ /* VLine */
    background: {BORDER};
    border: none;
    max-height: 1px;
}}

/* ===== QToolTip ================================================= */
QToolTip {{
    background-color: {BG_RAISED};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 12px;
}}
"""
