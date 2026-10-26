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
# Starting with Windows brings the widgets back without opening the app window;
# every other launcher opens it.
BACKGROUND = "--background"


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
def _make_shortcut(path: str, args: str = "main.py", style: int = 1) -> bool:
    """``style`` is the shortcut's Run setting: 1 normal, 7 minimised.

    Launchers that open the app window must be normal. Windows applies a
    minimised Run setting to the first ordinary window the process shows, which
    is now the app window - it would open straight onto the taskbar.
    """
    root = _project_root()
    target = pythonw()
    icon = icon_path() or target
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK_PATH); "
        "$s.TargetPath = $env:LNK_TARGET; "
        "$s.Arguments = $env:LNK_ARGS; "
        "$s.WorkingDirectory = $env:LNK_CWD; "
        "$s.IconLocation = $env:LNK_ICON; "
        "$s.Description = 'Desktop widgets'; "
        "$s.WindowStyle = [int]$env:LNK_STYLE; "
        "$s.Save()"
    )
    env = dict(os.environ, LNK_PATH=path, LNK_TARGET=target, LNK_ARGS=args,
               LNK_CWD=root, LNK_ICON=icon, LNK_STYLE=str(style))
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
    # Minimised is harmless here: a background start shows no ordinary window.
    return _make_shortcut(startup_link(), f"main.py {BACKGROUND}", style=7)


def startup_link_is_current() -> bool:
    """Does the Startup shortcut, if there is one, start the board quietly?

    Shortcuts made before the app had a window launch plain ``main.py``, which
    now opens the window - at every boot. A .lnk keeps its arguments as UTF-16
    text, so the flag can be looked for in the file without a COM round trip.
    """
    try:
        with open(startup_link(), "rb") as fh:
            return BACKGROUND.encode("utf-16-le") in fh.read()
    except OSError:
        return True             # no shortcut, nothing to bring up to date


def refresh_startup_link() -> None:
    """Rewrite an out-of-date Startup shortcut. Cheap when there is nothing to do."""
    if is_enabled() and not startup_link_is_current():
        enable()


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
