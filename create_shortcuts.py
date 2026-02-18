"""
create_shortcuts.py
Create a polished Windows desktop shortcut (.lnk) for DGX Desktop Remote.

Uses the project's own .venv pythonw.exe so:
  • No console window when launched from the icon
  • Fully self-contained — no system Python dependency after install
  • Custom icon baked in from icons/app.ico

Can be called:
  • By INSTALL.bat after setup
  • Automatically by main.py on first run
  • Manually:  python create_shortcuts.py
"""

import os
import sys
from pathlib import Path


ROOT = Path(__file__).parent.resolve()
ICON = ROOT / "icons" / "app.ico"
SCRIPT = ROOT / "pc-application" / "src" / "main.py"


def _venv_pythonw() -> Path:
    """Return the pythonw.exe inside the project .venv (no console window)."""
    venv_pw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pw.exists():
        return venv_pw
    # Fallback: same dir as current interpreter but pythonw
    cur = Path(sys.executable)
    pw = cur.parent / "pythonw.exe"
    if pw.exists():
        return pw
    return cur   # last resort — regular python


def create_desktop_shortcut(force: bool = False) -> bool:
    """
    Create / update the .lnk on the Windows desktop.
    Returns True on success, False on failure.
    """
    desktop = Path(os.path.expanduser("~")) / "Desktop"
    lnk_path = desktop / "DGX Desktop Remote.lnk"

    if lnk_path.exists() and not force:
        return True   # already there

    try:
        import win32com.client
    except ImportError:
        print("[shortcut] pywin32 not available — run: pip install pywin32")
        return False

    pythonw = _venv_pythonw()

    shell = win32com.client.Dispatch("WScript.Shell")
    lnk   = shell.CreateShortCut(str(lnk_path))

    lnk.TargetPath       = str(pythonw)
    lnk.Arguments        = f'"{SCRIPT}"'
    lnk.WorkingDirectory = str(SCRIPT.parent)
    lnk.Description      = "DGX Desktop Remote — Remote window into your DGX"
    lnk.WindowStyle      = 1   # 1=Normal, 7=Minimised

    if ICON.exists():
        lnk.IconLocation = f"{ICON}, 0"

    lnk.save()
    print(f"[shortcut] Created: {lnk_path}")
    return True


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Desktop shortcut creation is Windows-only.")
        sys.exit(0)

    force = "--force" in sys.argv or "-f" in sys.argv
    ok = create_desktop_shortcut(force=force)
    sys.exit(0 if ok else 1)
