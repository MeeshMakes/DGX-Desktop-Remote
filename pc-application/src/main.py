"""
pc-application/src/main.py
Entry point for DGX Desktop Remote PC application.
"""

import sys
import os
import logging

# Ensure src is on the path (handles both direct run and packaged)
_SRC = os.path.dirname(os.path.abspath(__file__))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Root logger — DEBUG level so the console window catches everything
logging.basicConfig(
    level=logging.DEBUG,
    format="%(name)-22s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore    import Qt, QSettings
from PyQt6.QtGui     import QFont

from config        import Config
from theme         import APP_STYLESHEET
from setup_wizard  import SetupWizard


def main():
    # Hi-DPI
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("DGX Desktop Remote")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("FathomPC")
    app.setStyleSheet(APP_STYLESHEET)

    # Default font
    font = QFont("Segoe UI", 9)
    app.setFont(font)

    # Load or create config
    config = Config.load()

    # Run setup wizard on first launch
    if not config.is_configured():
        wizard = SetupWizard(config)
        if wizard.exec() != SetupWizard.DialogCode.Accepted:
            # User exited wizard without completing
            sys.exit(0)
        # Reload post-wizard
        config = Config.load()

    # Lazy import main window to speed initial startup
    from main_window  import MainWindow
    from system_tray  import AppSystemTray

    win  = MainWindow(config)
    tray = AppSystemTray(win)
    win.tray = tray          # back-reference so window can notify tray
    tray.show()

    if config.start_minimized:
        win.hide()
    else:
        win.show()

    if config.auto_connect:
        # Delay slightly so window paints first
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(500, win._connect)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
