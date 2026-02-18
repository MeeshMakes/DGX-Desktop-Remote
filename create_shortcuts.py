"""
create_shortcuts.py
Create a Windows desktop shortcut (.lnk) for DGX Desktop Remote.
Also places a 'Run DGX Service' reminder on the DGX (SSH instructions).
"""

import os
import sys
from pathlib import Path


def create_windows_shortcut():
    """Create a .lnk shortcut on the Windows desktop using the pywin32 library."""
    try:
        import win32com.client
    except ImportError:
        _create_shortcut_fallback()
        return

    desktop = Path(os.path.expanduser("~")) / "Desktop"
    shortcut_path = str(desktop / "DGX Desktop Remote.lnk")

    shell  = win32com.client.Dispatch("WScript.Shell")
    lnk    = shell.CreateShortCut(shortcut_path)

    # Target: python main.py in pc-application/src/
    script  = Path(__file__).parent / "pc-application" / "src" / "main.py"
    python  = sys.executable

    lnk.Targetpath       = python
    lnk.Arguments        = f'"{script}"'
    lnk.WorkingDirectory = str(script.parent)
    lnk.Description      = "Launch DGX Desktop Remote PC Client"

    # Icon — use python.exe icon as fallback
    icon_dir = Path(__file__).parent / "icons"
    icon_ico = icon_dir / "app.ico"
    if icon_ico.exists():
        lnk.IconLocation = str(icon_ico)

    lnk.save()
    print(f"Shortcut created: {shortcut_path}")


def _create_shortcut_fallback():
    """Write a .bat launcher instead if pywin32 is unavailable."""
    desktop = Path(os.path.expanduser("~")) / "Desktop"
    bat     = desktop / "DGX Desktop Remote.bat"
    script  = Path(__file__).parent / "pc-application" / "src" / "main.py"
    content = (
        f'@echo off\n'
        f'cd /d "{script.parent}"\n'
        f'"{sys.executable}" main.py\n'
    )
    bat.write_text(content)
    print(f"Batch launcher created: {bat}")


if __name__ == "__main__":
    if sys.platform == "win32":
        create_windows_shortcut()
    else:
        print("Shortcut creation is only supported on Windows from this script.")
