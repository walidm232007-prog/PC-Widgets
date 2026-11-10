# PyInstaller spec for PC Widgets.exe. Build it with tools/build_exe.py, which
# runs this on a clean export of the commit; packaging/README.md explains.
#
# One folder, not one file: a one-file exe unpacks itself to %TEMP% at every
# launch, which is slower to start, trips antivirus more often, and would give
# the taskbar guard and the media helper a different path on every run.

import glob
import importlib.metadata
import os
import runpy
import shutil
import sys

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


# Notices. The folder passes on Qt, Python, numpy, PyInstaller's own run-time
# code and the vendored packages, and each of their licences asks for its text
# to travel with it. They go beside the exe, not in _internal, because that is
# where a person looks for them; THIRD-PARTY.md, shipped with them, says which
# file covers what. Gathered here, before the build, so a missing one stops it
# straight away rather than after two minutes of analysis.
def txt(name):
    """Windows has nothing to open a file called LICENSE with."""
    return name if os.path.splitext(name)[1] else name + ".txt"


def installed_licences(package, folder):
    """The licence files an installed package's dist-info carries, at their
    paths inside its licenses/ folder. From the Python running this build, so
    they are always those of the copy being bundled."""
    dist = importlib.metadata.distribution(package)
    found = {}
    for f in dist.files or ():
        if len(f.parts) > 2 and f.parts[0].endswith(".dist-info") and f.parts[1] == "licenses":
            found[f"licenses/{folder}/" + txt("/".join(f.parts[2:]))] = str(dist.locate_file(f))
    if not found:
        raise SystemExit(f"{package} is installed without its licence files")
    return found


def vendored_licences(dist_info, folder):
    """The licence files of a package in vendor/, whatever its version."""
    found = {f"licenses/{folder}/" + txt(os.path.basename(path)): path
             for path in glob.glob(os.path.join(ROOT, "vendor", dist_info, "licenses", "*"))}
    if not found:
        raise SystemExit(f"no licence files in vendor/{dist_info}/licenses")
    return found


NOTICES = {
    # The app's own.
    "LICENSE.txt": os.path.join(ROOT, "LICENSE"),
    "THIRD-PARTY.md": os.path.join(ROOT, "THIRD-PARTY.md"),
    # Qt, PySide6 and shiboken6: their wheels carry no licence text, so the
    # LGPL v3 is kept in packaging/licenses/, with the GPL v3 it is written as
    # additions to.
    "licenses/Qt/LGPL-3.0.txt": os.path.join(ROOT, "packaging", "licenses", "LGPL-3.0.txt"),
    "licenses/Qt/GPL-3.0.txt": os.path.join(ROOT, "packaging", "licenses", "GPL-3.0.txt"),
    # The Windows build of Python appends what it ships with (OpenSSL,
    # libffi, bzip2, the C runtime) to its own licence.
    "licenses/Python/LICENSE.txt": os.path.join(sys.base_prefix, "LICENSE.txt"),
    "licenses/winrt/LICENSE.txt": os.path.join(ROOT, "vendor", "LICENSE-winrt.txt"),
    "licenses/Inter/LICENSE.txt": os.path.join(ROOT, "fonts", "LICENSE-Inter.txt"),
    "licenses/weather-photos/CREDITS.md": os.path.join(ROOT, "assets", "weather", "CREDITS.md"),
}
# numpy's covers the OpenBLAS, LAPACK and GCC runtime inside numpy.libs too;
# PyInstaller's covers the run-time hooks it puts in the exe (Apache 2.0).
NOTICES.update(installed_licences("numpy", "numpy"))
NOTICES.update(installed_licences("pyinstaller", "PyInstaller"))
NOTICES.update(vendored_licences("pyaudiowpatch-*.dist-info", "PyAudioWPatch"))
NOTICES.update(vendored_licences("typing_extensions-*.dist-info", "typing_extensions"))
missing = sorted(src for src in NOTICES.values() if not os.path.isfile(src))
if missing:
    raise SystemExit("refusing to build without these notices: " + ", ".join(missing))

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

# COLLECT has assembled the folder by now (coll.name is its path). Data files
# can only go inside _internal, so the notices are copied in beside the exe
# afterwards; COLLECT empties the folder first on every build, so none linger.
for dest, src in sorted(NOTICES.items()):
    target = os.path.join(coll.name, *dest.split("/"))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copyfile(src, target)
