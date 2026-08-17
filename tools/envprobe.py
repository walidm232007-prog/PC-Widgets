"""Dump the environment a launch actually gets, and whether the media API loads.

Run it two ways - from a shell, and from a shortcut the way Explorer starts the
board at login - and diff the two. The board behaves differently depending on how
it was started, and this is the cheapest way to find out what differs.

    python tools\\envprobe.py out.json
"""
from __future__ import annotations

import json
import os
import site
import sys
import time


def probe() -> dict:
    info = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "argv": sys.argv,
        "sys_path": list(sys.path),
        "flags_no_user_site": bool(sys.flags.no_user_site),
        "flags_isolated": bool(sys.flags.isolated),
        "user_site_enabled": site.ENABLE_USER_SITE,
        "user_site_dir": None,
        "user_site_on_path": None,
        "env": {k: os.environ.get(k) for k in (
            "APPDATA", "LOCALAPPDATA", "USERPROFILE", "USERNAME", "HOMEPATH",
            "PYTHONPATH", "PYTHONHOME", "PYTHONNOUSERSITE", "PYTHONSTARTUP",
            "PATH", "TEMP", "SESSIONNAME",
        )},
        "imports": {},
    }
    try:
        user = site.getusersitepackages()
        info["user_site_dir"] = user
        info["user_site_on_path"] = user in sys.path
        info["user_site_exists"] = os.path.isdir(user)
    except Exception as exc:
        info["user_site_dir"] = f"<{exc!r}>"

    for name in ("winrt.windows.media.control", "winrt.windows.storage.streams",
                 "PySide6", "numpy", "pyaudiowpatch"):
        t0 = time.perf_counter()
        try:
            __import__(name)
            info["imports"][name] = {
                "ok": True, "ms": round((time.perf_counter() - t0) * 1000, 1)}
        except BaseException as exc:                   # noqa: BLE001
            info["imports"][name] = {
                "ok": False, "error": repr(exc),
                "ms": round((time.perf_counter() - t0) * 1000, 1)}

    # And what the media layer concludes from all of the above.
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from widgets.core import media
        src = media.MediaSource()
        np = src.poll()
        info["media"] = {
            "winrt_ok": src.winrt_ok, "backend": src.backend,
            "title": np.title, "source": np.source, "available": np.available,
        }
    except BaseException as exc:                       # noqa: BLE001
        info["media"] = {"error": repr(exc)}
    return info


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "envprobe.json"
    data = probe()
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print(json.dumps(data["media"], indent=2))
