"""
build_exe.py
============
Build ``PC Widgets.exe`` from a commit, then check the build works.

    python tools/build_exe.py [--out build] [--ref HEAD] [--no-test]

Run it with a Python that has the packages in packaging/requirements.txt.

The exe is built from ``git archive`` of the commit, never the working folder.
A working folder holds things that must not ship: a local SF Pro copy in
fonts/, logs, a layout mirror, uncommitted edits. A clean export is also what
the release workflow builds, so a local build and a released one match.

Then three checks, each on the built exe rather than the source:

- ``--selftest``, the whole board offscreen, with %LOCALAPPDATA% pointed into
  the build folder so nothing lands in the profile;
- the taskbar guard: a hidden window, parked off every monitor, comes back
  when the guard's pipe closes;
- the media helper answers with JSON, the WinRT projection importable.

Output: ``<out>/dist/PC Widgets/``, the folder the installer packs.
"""

from __future__ import annotations

import argparse
import ctypes as C
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from ctypes import wintypes as W

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NO_WINDOW = 0x08000000


def export(ref: str, dest: str) -> None:
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    tar = subprocess.run(["git", "archive", "--format=tar", ref], cwd=ROOT,
                         capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(dest, filter="data")


def forbidden(folder: str) -> list:
    """Files that must never be in a build: Apple's fonts are licensed to one
    machine."""
    return [os.path.join(base, n) for base, _, names in os.walk(folder)
            for n in names if n.lower().endswith(".otf")]


def size_mb(folder: str) -> float:
    return sum(os.path.getsize(os.path.join(b, n))
               for b, _, names in os.walk(folder) for n in names) / 1e6


# --------------------------------------------------------------------------- #
def check_selftest(exe: str, out: str) -> None:
    local = os.path.join(out, "test-localappdata")
    shutil.rmtree(local, ignore_errors=True)
    os.makedirs(local)
    env = dict(os.environ, LOCALAPPDATA=local)
    started = time.perf_counter()
    code = subprocess.run([exe, "--selftest"], env=env, timeout=600).returncode
    log = os.path.join(local, "PCWidgets", "_selftest", "selftest.log")
    text = open(log, encoding="utf-8").read() if os.path.exists(log) else ""
    took = time.perf_counter() - started
    assert code == 0 and "all good." in text, (
        f"the exe's self test failed (exit {code}):\n{text[-3000:]}")
    print(f"  ok    --selftest passes in {took:.0f} s ({log})")


def check_guard(exe: str) -> None:
    u = C.WinDLL("user32", use_last_error=True)
    u.CreateWindowExW.restype = W.HWND
    u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
                                  C.c_int, C.c_int, C.c_int, C.c_int,
                                  W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
    u.IsWindowVisible.argtypes = [W.HWND]
    u.DestroyWindow.argtypes = [W.HWND]
    u.PeekMessageW.argtypes = [C.c_void_p, W.HWND, W.UINT, W.UINT, W.UINT]
    WS_POPUP, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000000, 0x80, 0x08000000
    # Created hidden, so there is nothing to see before or after.
    hwnd = u.CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, "STATIC",
                             "guard check", WS_POPUP, -32000, -32000, 40, 40,
                             None, None, None, None)
    assert hwnd, f"could not make a test window ({C.get_last_error()})"
    try:
        guard = subprocess.Popen([exe, "--guard", str(int(hwnd))],
                                 stdin=subprocess.PIPE, creationflags=NO_WINDOW)
        time.sleep(1.0)
        assert guard.poll() is None, "the guard exited before its pipe closed"
        assert not u.IsWindowVisible(hwnd)
        guard.stdin.close()
        # The guard's ShowWindow is answered by this thread, so keep pumping.
        msg = W.MSG()
        deadline = time.monotonic() + 10
        while guard.poll() is None and time.monotonic() < deadline:
            while u.PeekMessageW(C.byref(msg), None, 0, 0, 1):
                u.TranslateMessage(C.byref(msg))
                u.DispatchMessageW(C.byref(msg))
            time.sleep(0.02)
        assert guard.poll() == 0, "the guard did not finish"
        assert u.IsWindowVisible(hwnd), "the guard did not restore the window"
    finally:
        u.DestroyWindow(hwnd)
    print("  ok    the guard restores a hidden window when its pipe closes")


def check_media_helper(exe: str) -> None:
    res = subprocess.run([exe, "--winrt-read", "auto", ""], capture_output=True,
                         text=True, timeout=30, creationflags=NO_WINDOW)
    data = json.loads(res.stdout.strip() or "{}")
    assert data.get("ok"), (
        f"the media helper could not read the session: {res.stdout[:300]}"
        f"{res.stderr[-1500:]}")
    playing = "a track is playing" if data.get("available") else "nothing playing"
    print(f"  ok    the media helper answers ({playing})")


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "build"))
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--no-test", action="store_true")
    args = ap.parse_args()
    out = os.path.abspath(args.out)

    src = os.path.join(out, "src")
    commit = subprocess.run(["git", "rev-parse", "--short", args.ref], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    print(f"exporting {args.ref} ({commit})")
    export(args.ref, src)
    bad = forbidden(src)
    assert not bad, f"refusing to build with {bad}"

    dist = os.path.join(out, "dist")
    print("building")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                    "--log-level", "WARN", "--distpath", dist,
                    "--workpath", os.path.join(out, "work"),
                    os.path.join(src, "packaging", "pc-widgets.spec")], check=True)
    app = os.path.join(dist, "PC Widgets")
    exe = os.path.join(app, "PC Widgets.exe")
    bad = forbidden(app)
    assert not bad, f"the build contains {bad}"
    print(f"  built {exe}  ({size_mb(app):.0f} MB)")

    if not args.no_test:
        check_media_helper(exe)
        check_guard(exe)
        check_selftest(exe, out)
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
