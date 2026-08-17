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
Desktop, `--install-startup` makes it start with Windows (all three are also in
the tray menu once it's running).

- **Ctrl+Alt+W** brings the whole board to the front, and sends it back down.
- **Right-click a widget** for its size, its settings, or to remove it.
- **Tray icon** adds widgets, switches theme and accent, and changes the layer.
- **Drag anywhere.** Positions save themselves; the board comes back exactly as
  you left it.

## About the fonts

The board is designed around Apple's SF Pro, but the font files aren't included —
Apple's licence covers designing for Apple platforms, not handing the files
around. It looks good without them: the type stack falls back to Inter, then
Helvetica Neue, then Segoe UI Variable, and the tracking curve and optical sizes
that do most of the work are built in either way.

If you want the real thing, it's a free download:

1. [developer.apple.com/fonts](https://developer.apple.com/fonts/) → **SF Pro**
   (needs a free Apple ID).
2. Open the `.dmg`, run the installer, or pull the `.otf` files out of it.
3. Copy these four into the `fonts/` folder next to this file:
   - `SF-Pro-Text-Regular.otf`
   - `SF-Pro-Text-Semibold.otf`
   - `SF-Pro-Display-Light.otf`
   - `SF-Pro-Display-Bold.otf`

They're loaded privately, for this app only — nothing is installed system-wide
and nothing else on the machine changes. Restart the board to pick them up.

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
