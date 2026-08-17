"""
startup.py
==========
Shortcuts: one in the Startup folder so the board comes back when the PC does,
and one on the Desktop so it can be launched or pinned like any other app.

The Startup *folder* is used rather than a registry Run key or a scheduled
task. All three work without admin, but only this one is somewhere the user can
find, see, and delete without being told how - which is the right property for
something that silently runs at every boot.

Shortcuts are created through ``WScript.Shell`` via PowerShell. That avoids a
pywin32 dependency for what is a handful of lines, and it is the same COM
interface Explorer itself uses, so the .lnk is a completely ordinary shortcut.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

APP_NAME = "PC Widgets"
LNK = f"{APP_NAME}.lnk"


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pythonw() -> str:
    """The windowed interpreter, so no console flashes up at boot."""
    exe = sys.executable
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe


def startup_dir() -> str:
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                        "Start Menu", "Programs", "Startup")


def desktop_dir() -> str:
    return os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")


def startup_link() -> str:
    return os.path.join(startup_dir(), LNK)


def desktop_link() -> str:
    return os.path.join(desktop_dir(), LNK)


def app_link() -> str:
    """A launcher inside the project folder itself.

    ``run.bat`` already worked, but a .bat shows a generic script icon and
    flashes a console on the way through. This is the same shortcut as the
    desktop one, sitting next to the code, so the folder has an obvious thing
    to double-click.
    """
    return os.path.join(_project_root(), LNK)


def icon_path() -> Optional[str]:
    p = os.path.join(_project_root(), "assets", "widgets.ico")
    return p if os.path.exists(p) else None


# --------------------------------------------------------------------------- #
def _make_shortcut(path: str) -> bool:
    root = _project_root()
    target = pythonw()
    icon = icon_path() or target
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK_PATH); "
        "$s.TargetPath = $env:LNK_TARGET; "
        "$s.Arguments = 'main.py'; "
        "$s.WorkingDirectory = $env:LNK_CWD; "
        "$s.IconLocation = $env:LNK_ICON; "
        "$s.Description = 'Desktop widgets'; "
        "$s.WindowStyle = 7; "          # minimised: nothing to see at launch
        "$s.Save()"
    )
    env = dict(os.environ, LNK_PATH=path, LNK_TARGET=target,
               LNK_CWD=root, LNK_ICON=icon)
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env, capture_output=True, text=True, timeout=30,
            creationflags=0x08000000)      # CREATE_NO_WINDOW
        return res.returncode == 0 and os.path.exists(path)
    except Exception:
        return False


def _remove(path: str) -> bool:
    try:
        if os.path.exists(path):
            os.remove(path)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
def is_enabled() -> bool:
    return os.path.exists(startup_link())


def enable() -> bool:
    os.makedirs(startup_dir(), exist_ok=True)
    return _make_shortcut(startup_link())


def disable() -> bool:
    return _remove(startup_link())


def set_enabled(on: bool) -> bool:
    return enable() if on else disable()


def create_desktop_shortcut() -> Optional[str]:
    if not os.path.isdir(desktop_dir()):
        return None
    return desktop_link() if _make_shortcut(desktop_link()) else None


def create_app_shortcut() -> Optional[str]:
    return app_link() if _make_shortcut(app_link()) else None
