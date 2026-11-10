# Packaging

How `PC Widgets.exe` and its installer are built, so people can install PC
Widgets without installing Python, running pip or opening a terminal.

## Build it

From a checkout, with a Python 3.13 that is only for building:

```bash
python -m venv C:\pcw-build\env
```

```bash
C:\pcw-build\env\Scripts\python -m pip install -r packaging/requirements.txt
```

```bash
C:\pcw-build\env\Scripts\python tools/build_exe.py --out C:\pcw-build\out
```

The result is `<out>\dist\PC Widgets\`: `PC Widgets.exe` and its `_internal`
folder, about 100 MB, with the licences beside them (see "What is in it").
Keep the build folder's path short: PySide6's wheel holds paths long enough
that pip fails under a deep folder without Windows' long-path support.

`tools/build_exe.py` builds the commit (`--ref`, default `HEAD`), not the working
folder. It exports the commit with `git archive`, so uncommitted edits, logs,
the layout mirror and a local SF Pro copy in `fonts/` are never in a build; it
refuses to build if an `.otf` turns up anyway, or if a licence is missing from
beside the exe or THIRD-PARTY.md doesn't name the PySide6 in the build - those
checks run even with `--no-test`. Then it checks the exe itself:

- **the media helper** answers, with the bundled WinRT projection importable;
- **the taskbar guard** brings a hidden window back when its pipe closes - the
  window is parked off every monitor, so nothing shows;
- **`--selftest`** passes: every widget rendered offscreen, and the bundle's
  copies of the media API and the audio capture are the ones that load. The
  exe has no console, so its report goes to `_selftest\selftest.log` and the
  exit code says whether it passed. `%LOCALAPPDATA%` is pointed into the build
  folder for the test, so nothing lands in the profile;
- **its properties** - what Explorer shows under Details - name the publisher,
  copyright and version in `widgets/version.py`, read back the way Explorer
  reads them.

`--no-test` skips them.

## The installer

```bash
C:\pcw-build\env\Scripts\python tools/build_exe.py --out C:\pcw-build\out --installer
```

That also compiles `packaging/pc-widgets.iss` with Inno Setup 6 (free;
`winget install JRSoftware.InnoSetup --scope user`) into
`<out>\PC-Widgets-Setup-<version>.exe`, about 30 MB. What it does:

- **No admin.** It installs for the current user, to
  `%LOCALAPPDATA%\Programs\PC Widgets`, so there is no UAC prompt and nothing
  for other accounts on the PC.
- **Three clicks.** Choose whether it starts with Windows (on) and whether it
  gets a desktop shortcut (off), Install, then Finish, which opens it.
- **The app's own shortcuts.** The Start menu and Startup shortcuts are at the
  paths the app uses itself, so Settings → Start with Windows and the
  installer's box are the same switch, and the app never makes a second one.
- **Updates** are the newer installer run over the old one. It replaces the
  bundle whole, licences included, so nothing from an older build lingers,
  and if the board is running Windows closes it first (which also ends the
  taskbar guard, so the taskbar comes back). The layout, presets and settings
  are in `%APPDATA%` and stay.
- **Uninstall** is in Settings → Apps like any other program. It removes the
  program folder and every "PC Widgets" shortcut that points into it, and
  leaves alone one that points anywhere else - a copy run from a checkout
  makes shortcuts with the same name. The layout stays in `%APPDATA%\PCWidgets`
  in case it is reinstalled.

`--installer` checks it too: the installer's own properties, then a silent
install into `<out>\test-install` with no Startup or Desktop shortcut, the
Start menu shortcut opening the installed exe, Settings → Apps naming the right
publisher, the installed exe's media helper, then a silent uninstall leaving
nothing behind. The installed app is never opened. A developer's own "PC Widgets"
shortcuts are put back byte for byte afterwards, since the test install
replaces the Start menu one.

**Unsigned**, Windows SmartScreen shows "Windows protected your PC" the first
time; More info → Run anyway. Signing the installer and the exe removes that,
and needs a code-signing certificate or signing service.

## Releasing

`.github/workflows/release.yml` does the build above on a clean GitHub Windows
machine, so a release never depends on what one PC has installed.

1. Raise `VERSION` in `widgets/version.py` and commit it to `main`.
2. **Actions → Release → Run workflow** on `main`. That builds and tests
   everything, `--installer` included, and keeps the installer as an artifact
   for a week, to try before anyone else gets it.
3. Tag the commit and push the tag: `git tag v1.0.0`, then
   `git push origin v1.0.0`. The workflow runs again and publishes a GitHub
   Release with the installer and its SHA-256. A tag that doesn't match
   `widgets/version.py` fails before anything is built.

The README's download link points at the latest release, so it needs no
change. If a test fails, the self test's log is attached to the run.

## What is in it

`packaging/pc-widgets.spec` says what goes in, and why:

- **One folder, not one file.** A one-file exe unpacks itself to `%TEMP%` at
  every launch: slower to start, more often flagged by antivirus, and a
  different path for the guard and the helper each run. One folder also keeps
  each Qt library a separate DLL in `_internal\PySide6\`, which is what lets a
  user swap in their own, as the LGPL requires.
- **Only the Qt the app uses.** `PySide6_Essentials`, not the whole of PySide6:
  the app imports QtCore, QtGui, QtWidgets and QtSvg. Software OpenGL (20 MB),
  the Direct2D backend, Qt's network library and Qt's translations are dropped;
  nothing here uses them. numpy stays, for the glass blur, the neon outline and
  the visualiser's spectrum.
- **Data at the same paths as in a checkout** - `fonts/`, `assets/`, `vendor/`,
  `agents/` - so code that finds them from its own `__file__` works unchanged.
- **The licences beside the exe.** The folder passes on Qt, Python, numpy,
  PyInstaller's run-time code and the vendored packages, so it carries their
  licences: `LICENSE.txt` (the app's own), `THIRD-PARTY.md`, and `licenses\`
  with a folder per component. They sit next to `PC Widgets.exe`, not in
  `_internal`, because that is where a person looks. PyInstaller only puts data
  files inside `_internal`, so the spec copies them in once the folder is
  assembled. Qt's LGPL and GPL texts come from `packaging/licenses/` (the
  PySide6 wheels carry no licence text), Python's, numpy's and PyInstaller's
  from the copies installed in the build Python, and the rest from where they
  already sit in the repository. The spec stops before building if one is
  missing. THIRD-PARTY.md says what each one covers.
- **The version and the publisher** come from `widgets/version.py`: Studio
  Hendrixx, a copyright line and the project's page, in the exe's properties,
  the installer's, and Settings → Apps, where the publisher used to read
  "PC Widgets" - the product, not who made it. `tools/build_exe.py` passes them
  to Inno Setup on the command line, so the two can't disagree and the "©" never
  depends on the `.iss` file's encoding. A code signature, when there is one,
  is a separate name, carried by the certificate.

`packaging/requirements.txt` pins the versions the build was tested with.
Raising PySide6 there means updating the Qt section of `THIRD-PARTY.md` too -
its version and its links to Qt's source - and the build stops until it
names the new one.

## Running as the exe

`widgets/core/paths.py` is everything that differs from a checkout:

- The taskbar guard and the Now Playing helper are separate processes. From a
  checkout they are scripts run by Python; the exe starts itself again with
  `--guard` or `--winrt-read`, which `main.py` hands over before Qt is loaded.
- Shortcuts and start-with-Windows point at the exe.
- The layout's second copy and the logs go to `%LOCALAPPDATA%\PCWidgets`, not the
  program folder, which every update replaces. The layout itself stays in
  `%APPDATA%\PCWidgets`, as from a checkout, so moving between the two keeps it.

Only one board runs at a time, whichever form it is in: they share the layout,
and the second one launched opens the first one's window.
