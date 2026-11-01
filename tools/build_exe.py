"""
build_exe.py
============
Build ``PC Widgets.exe`` from a commit, then check the build works.

    python tools/build_exe.py [--out build] [--ref HEAD] [--no-test] [--installer]

Run it with a Python that has the packages in packaging/requirements.txt.
``--installer`` also compiles packaging/pc-widgets.iss with Inno Setup 6.

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

With ``--installer``: ``<out>/PC-Widgets-Setup-<version>.exe``, then installed
silently into ``<out>/test-install``, its exe and Start menu shortcut checked,
and uninstalled again. The Start menu, Startup and Desktop shortcuts called
"PC Widgets" are put back as they were afterwards: on a developer's machine
they are the checkout's own, and the test install replaces the Start menu one.
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
def iscc() -> str:
    """Inno Setup's compiler: $ISCC, PATH, or where its installer puts it."""
    found = os.environ.get("ISCC") or shutil.which("ISCC")
    if found:
        return found
    folders = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
               os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", "")]
    for folder in folders:
        cand = os.path.join(folder, "Inno Setup 6", "ISCC.exe")
        if folder and os.path.exists(cand):
            return cand
    raise SystemExit("Inno Setup 6 not found: install it (winget install "
                     "JRSoftware.InnoSetup) or set ISCC to ISCC.exe")


def version(src: str) -> str:
    with open(os.path.join(src, "widgets", "version.py"), encoding="utf-8") as fh:
        return fh.read().split('VERSION = "')[1].split('"')[0]


def build_installer(src: str, app: str, out: str) -> str:
    ver = version(src)
    subprocess.run([iscc(), "/Q", f"/DAppVersion={ver}", f"/DSourceDir={app}",
                    f"/DOutputDir={out}",
                    os.path.join(src, "packaging", "pc-widgets.iss")], check=True)
    setup = os.path.join(out, f"PC-Widgets-Setup-{ver}.exe")
    assert os.path.exists(setup), f"Inno Setup made no {setup}"
    print(f"  built {setup}  ({os.path.getsize(setup) / 1e6:.0f} MB)")
    return setup


def shortcuts() -> list:
    programs = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                            "Start Menu", "Programs")
    return [os.path.join(programs, "PC Widgets.lnk"),
            os.path.join(programs, "Startup", "PC Widgets.lnk"),
            os.path.join(os.environ["USERPROFILE"], "Desktop", "PC Widgets.lnk")]


def link_target(path: str) -> str:
    script = "(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK).TargetPath"
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          env=dict(os.environ, LNK=path), capture_output=True, text=True,
                          creationflags=NO_WINDOW).stdout.strip()


def check_installer(setup: str, out: str) -> None:
    """Install silently, look, uninstall, and leave the shortcuts as they were.

    Silent, so nothing shows and the installed app is never opened (the
    finish page's "Open PC Widgets" is skipped). No tasks, so it adds no
    Startup or Desktop shortcut.
    """
    target = os.path.join(out, "test-install")
    shutil.rmtree(target, ignore_errors=True)
    saved = {p: open(p, "rb").read() for p in shortcuts() if os.path.exists(p)}
    try:
        subprocess.run([setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                        f"/DIR={target}", "/TASKS=", f"/LOG={out}\\install.log"],
                       check=True, timeout=300)
        exe = os.path.join(target, "PC Widgets.exe")
        assert os.path.exists(exe), "the installer put no exe in place"
        menu = shortcuts()[0]
        assert os.path.normcase(link_target(menu)) == os.path.normcase(exe), (
            "the Start menu shortcut doesn't open the installed exe")
        check_media_helper(exe)

        uninstaller = os.path.join(target, "unins000.exe")
        subprocess.run([uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                       check=True, timeout=300)
        # The uninstaller hands over to a copy of itself in %TEMP% and returns.
        deadline = time.monotonic() + 60
        while os.path.exists(exe) and time.monotonic() < deadline:
            time.sleep(0.5)
        assert not os.path.exists(exe), "the uninstaller left the exe behind"
        time.sleep(2)
        assert not os.path.exists(menu), "the uninstaller left the Start menu shortcut"
        left = [n for n in os.listdir(target)] if os.path.isdir(target) else []
        assert not left, f"the uninstaller left {left}"
        print("  ok    installs, its Start menu shortcut opens it, and it uninstalls cleanly")
    finally:
        for path, data in saved.items():
            try:
                if open(path, "rb").read() == data:
                    continue
            except OSError:
                pass
            # Rewritten in place: Windows refuses to replace a hidden file, and
            # a person may well have hidden a desktop shortcut.
            with open(path, "r+b" if os.path.exists(path) else "wb") as fh:
                fh.write(data)
                fh.truncate()


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "build"))
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--no-test", action="store_true")
    ap.add_argument("--installer", action="store_true")
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
    if args.installer:
        setup = build_installer(src, app, out)
        if not args.no_test:
            check_installer(setup, out)
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
