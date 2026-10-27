# Setting this up on a new PC

Windows 10 or 11. Two things to install, then double-click and it runs.

## 1. Python

Get it from [python.org/downloads](https://www.python.org/downloads/) — 3.11 or
newer. **Tick "Add python.exe to PATH"** on the first screen of the installer;
without it the launcher can't find Python.

## 2. Qt

Open a terminal (Win+R → `cmd`) and run:

```bash
pip install PySide6
```

That's the only required dependency. Everything else uses the standard library
and `ctypes` calls into Windows itself — no compilers, no build step.

**Optional, for the music visualiser:**

```bash
pip install PyAudioWPatch
```

That lets the bars in the media widget show the real frequency spectrum of
whatever is playing, by reading the system's audio output. Without it they fall
back to the actual output *level* (no extra install needed), and failing that to
an animation. Nothing is ever recorded — samples are measured and thrown away —
and you can switch it off entirely: right-click the widget → **Audio
visualiser**.

## 3. Run it

Double-click **run.bat**, or make yourself a proper launcher with an icon:

```bash
python main.py --app
```

That drops a **PC Widgets** shortcut in this folder. `--shortcut` puts one on the
Desktop, `--install-startup` makes it start with Windows (the flags combine, and
all three are also in the app once it's running).

- **The app window** opens when you launch it: a gallery to add widgets from,
  your widgets with their sizes, colours and background photos, and settings.
  Closing it leaves the widgets on the desktop.
- **Tray icon**: click it to open the app window; right-click for a quick menu.
- **Ctrl+Alt+W** brings the whole board to the front, and sends it back down.
- **Right-click a widget** for its size, its settings, or to remove it.
- **Drag anywhere.** Positions save themselves; the board comes back exactly as
  you left it.

## About the fonts

Nothing to download: the board is set in Inter, which comes with it in the
`fonts/` folder. It's open source, under the SIL Open Font License, and loaded
privately for this app only — nothing is installed system-wide and nothing else
on the machine changes.

## Where your settings live

`%APPDATA%\PCWidgets\` — the layout (`board.json`, plus a `.bak` one step
behind), your notes, and `errors.log` if anything ever fails to save. Deleting
`board.json` gives you a fresh default board, as does `python main.py --reset`.

## A note on the Workspace widget

That one is a rig for watching two AI coding agents work, and it reads a folder
of status files that get written by editor hooks. Without those hooks it just
sits there showing two dark bays. `agents/AGENTS.md` explains the protocol if you
ever want to wire it up; otherwise right-click it → **Remove widget**.

## If something doesn't start

Run it in a terminal instead of double-clicking, so you can see the error:

```bash
python main.py
```

`python main.py --selftest` renders every widget to `_selftest/*.png` without
putting anything on screen, which is the quickest way to tell whether the
problem is the app or the desktop layering.
