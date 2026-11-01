"""
paths.py
========
Where the app is, how to start another copy of it, and where it may write -
the same answers from a checkout and from the installed program.

From a checkout the app is ``main.py`` run by Python, and everything sits in
the project folder. Installed, it is ``PC Widgets.exe`` built by PyInstaller:
there is no Python to hand a script to, and the program folder is replaced on
every update. Three things relied on the checkout layout and broke there:

- the taskbar guard and the media helper start ``python some_module.py``. The
  exe can't run a script, and handed one as an argument it would start a second
  board instead; so the exe takes ``--guard`` and ``--winrt-read`` itself;
- shortcuts pointed at ``pythonw.exe main.py``;
- the layout's second copy and two logs were written beside the code, where an
  update or an uninstall would delete them.

Read-only files (fonts, assets, vendor, agents) still resolve from each
module's own ``__file__``: PyInstaller puts the bundled copies at the same
paths relative to the modules, so that code needs no change.
"""

from __future__ import annotations

import os
import sys
from typing import List

FROZEN = bool(getattr(sys, "frozen", False))


def project_root() -> str:
    """The folder that holds ``main.py`` and its data, or their bundled copies."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def install_dir() -> str:
    """The folder a person would open: the checkout, or the program's own folder."""
    return os.path.dirname(sys.executable) if FROZEN else project_root()


def write_dir() -> str:
    """Somewhere beside the app to write, and not in the profile's PCWidgets.

    The layout keeps a second copy away from %APPDATA% because that directory
    has repeatedly been unreadable or unwritable to the board. From a checkout
    that is the project folder, as it always was; installed, it is
    %LOCALAPPDATA%\\PCWidgets, which survives updates and is still a different
    directory from the first copy.
    """
    if not FROZEN:
        return project_root()
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "PCWidgets")
    os.makedirs(path, exist_ok=True)
    return path


def _python(windowed: bool) -> str:
    exe = sys.executable or "python"
    want = "pythonw.exe" if windowed else "python.exe"
    cand = os.path.join(os.path.dirname(exe), want)
    return cand if os.path.exists(cand) else exe


def launcher(windowed: bool = True) -> str:
    """The program a shortcut targets. ``windowed`` picks pythonw from a
    checkout, so no console flashes up; the exe never has one."""
    return sys.executable if FROZEN else _python(windowed)


def launch_args(args: str = "") -> str:
    """A shortcut's arguments: the script first from a checkout, nothing extra
    for the exe."""
    return args if FROZEN else f"main.py {args}".strip()


def helper(mode: str, script: str, *args: str) -> List[str]:
    """The command line for a short-lived helper process.

    Either way ``mode`` is the first argument, so the helper reads the same
    ``sys.argv`` both times. From a checkout ``script`` runs under the console
    interpreter, which with CREATE_NO_WINDOW shows nothing and has real pipes.
    Installed it is the exe again; ``main.py`` sends the mode straight to the
    helper before importing Qt or touching the board.
    """
    if FROZEN:
        return [sys.executable, mode, *args]
    return [_python(windowed=False), script, mode, *args]
