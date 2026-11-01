# Packaging

How `PC Widgets.exe` is built, so people can install PC Widgets without
installing Python, running pip or opening a terminal.

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
folder, about 100 MB. Keep the build folder's path short: PySide6's wheel holds
paths long enough that pip fails under a deep folder without Windows' long-path
support.

`tools/build_exe.py` builds the commit (`--ref`, default `HEAD`), not the working
folder. It exports the commit with `git archive`, so uncommitted edits, logs,
the layout mirror and a local SF Pro copy in `fonts/` are never in a build; it
refuses to build if an `.otf` turns up anyway. Then it checks the exe itself:

- **the media helper** answers, with the bundled WinRT projection importable;
- **the taskbar guard** brings a hidden window back when its pipe closes - the
  window is parked off every monitor, so nothing shows;
- **`--selftest`** passes: every widget rendered offscreen, and the bundle's
  copies of the media API and the audio capture are the ones that load. The
  exe has no console, so its report goes to `_selftest\selftest.log` and the
  exit code says whether it passed. `%LOCALAPPDATA%` is pointed into the build
  folder for the test, so nothing lands in the profile.

`--no-test` skips them.

## What is in it

`packaging/pc-widgets.spec` says what goes in, and why:

- **One folder, not one file.** A one-file exe unpacks itself to `%TEMP%` at
  every launch: slower to start, more often flagged by antivirus, and a
  different path for the guard and the helper each run.
- **Only the Qt the app uses.** `PySide6_Essentials`, not the whole of PySide6:
  the app imports QtCore, QtGui, QtWidgets and QtSvg. Software OpenGL (20 MB),
  the Direct2D backend, Qt's network library and Qt's translations are dropped;
  nothing here uses them. numpy stays, for the glass blur, the neon outline and
  the visualiser's spectrum.
- **Data at the same paths as in a checkout** - `fonts/`, `assets/`, `vendor/`,
  `agents/` - so code that finds them from its own `__file__` works unchanged.
- **The version** comes from `widgets/version.py` and is written into the exe's
  properties.

`packaging/requirements.txt` pins the versions the build was tested with.

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
