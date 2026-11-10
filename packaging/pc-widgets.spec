# PyInstaller spec for PC Widgets.exe. Build it with tools/build_exe.py, which
# runs this on a clean export of the commit; packaging/README.md explains.
#
# One folder, not one file: a one-file exe unpacks itself to %TEMP% at every
# launch, which is slower to start, trips antivirus more often, and would give
# the taskbar guard and the media helper a different path on every run.

import os
import runpy

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo)

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# widgets/version.py is plain data: the version and the publisher.
META = runpy.run_path(os.path.join(ROOT, "widgets", "version.py"))
VERSION = META["VERSION"]
NUMBERS = tuple(int(n) for n in (VERSION.split(".") + ["0"] * 4)[:4])


def tree(folder, skip=()):
    """Every file under ``folder``, kept at its relative path."""
    out = []
    for base, dirs, files in os.walk(os.path.join(ROOT, folder)):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith((".pyc", ".otf")) or name in skip:
                continue
            out.append((os.path.join(base, name), os.path.relpath(base, ROOT)))
    return out


# Read at run time from beside the modules, so they ship as files at the same
# relative paths. ``vendor/`` is put on sys.path by the app itself, ahead of
# anything else; PyInstaller's analysis never sees its code, which is why the
# standard library modules it needs are listed below.
#
# Never .otf: a local SF Pro copy in fonts/ is for that machine only.
datas = (tree("fonts") + tree("assets") + tree("vendor")
         + tree("agents", skip=("selftest.py",)))

hiddenimports = [
    "asyncio", "array", "inspect", "keyword", "locale", "socket", "tempfile",
    "uuid", "typing", "numpy",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT, os.path.join(ROOT, "agents")],
    datas=datas,
    hiddenimports=hiddenimports,
    # unittest stays: the self test uses unittest.mock.
    excludes=["tkinter", "pydoc_data", "PySide6.QtNetwork",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtOpenGL"],
    noarchive=False,
)

# Qt brings more than a widgets app uses. Software OpenGL (20 MB) is only for
# OpenGL without a driver, and nothing here draws with OpenGL; Direct2D is a
# second Windows backend beside the default one; the network library comes in
# through a plugin, and the board's HTTPS is Python's; and Qt's own
# translations are for dialogs this app doesn't use.
DROP = ("opengl32sw.dll", "qdirect2d.dll", "Qt6Network.dll")
a.binaries = [b for b in a.binaries if os.path.basename(b[0]) not in DROP]
a.datas = [d for d in a.datas
           if not d[0].replace("\\", "/").startswith("PySide6/translations/")]

pyz = PYZ(a.pure)

version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", META["PUBLISHER"]),
            StringStruct("ProductName", "PC Widgets"),
            StringStruct("FileDescription", "PC Widgets"),
            StringStruct("FileVersion", VERSION),
            StringStruct("ProductVersion", VERSION),
            StringStruct("OriginalFilename", "PC Widgets.exe"),
            StringStruct("LegalCopyright", f"{META['COPYRIGHT']}. Released under the MIT licence."),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PC Widgets",
    icon=os.path.join(ROOT, "assets", "widgets.ico"),
    version=version,
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PC Widgets", upx=False)
