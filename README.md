# PC Widgets

iPhone/Mac-style widgets for the Windows desktop. Rounded translucent cards that
live *on* your desktop, snap to a grid, and come to the front on a hotkey.

Double-click **PC Widgets** — the launcher sits at the top of this folder, and
there's a copy on your Desktop. (`run.bat` still works as a fallback.)

**Ctrl+Alt+W** brings the whole board to the front and sends it back down again.
The tray icon adds widgets, switches theme and accent, and changes the layer.

## Starting with Windows

**Start with Windows** in the tray menu toggles it, and **Add desktop shortcut**
makes another launcher. Or from a terminal:

```bash
python main.py --install-startup
```

`--remove-startup` undoes it, `--shortcut` makes the desktop one, and `--app`
recreates the launcher in this folder.

The launchers are Windows shortcuts, so they store an **absolute** path. If you
move or rename the project folder, fix all three in one go:

```bash
python main.py --app --shortcut --install-startup
```

`run.bat` is relative and survives a move, so it always works as a fallback.

Moving the folder also breaks anything *else* pointing at the old location — on
this machine that was the desk-gremlin's Claude Code hooks in
`~/.claude/settings.json`. The shared `design_system` lookup is now searched
relative to the project instead of from an absolute path, so that one travels
with you.

This uses the **Startup folder**, not a registry Run key or a scheduled task.
All three work without admin, but only this one is somewhere you can find, see
and delete without being told how — the right property for something that
silently runs at every boot. The shortcut lives at:

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PC Widgets.lnk
```

It points at `pythonw.exe` rather than `python.exe`, so nothing flashes up at
login, and the single-instance guard means launching it twice does nothing.
`tools/make_icon.py` generates `assets/widgets.ico` at nine sizes — Windows picks
a different one for the taskbar, the Start menu and Explorer, and a 16px tile
drawn at 16px keeps its corners where one downscaled from 256px turns to mush.

One optional dependency: the **Now Playing** widget uses the `winrt-*`
namespace packages for album art and playback position. Without them it falls
back to reading the track from window titles, and says so instead of inventing a
position. `winsdk` is the older package for the same API and has no wheels past
Python 3.12 — on 3.13 install these:

```bash
pip install winrt-runtime winrt-Windows.Media.Control winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections
```

Everything else needs nothing beyond PySide6, which you already had. Every icon,
chart and card is drawn at runtime with `QPainter` — there are no image assets.

---

## The widgets

| widget | sizes | what it shows |
|---|---|---|
| **Clock** | S, M | analog face; big digital readout with seconds at medium |
| **Weather** | S, M, L | current temp and conditions; 4-day forecast at large |
| **Stocks** | S, M, L | 1 / 3 / 6 symbols with intraday sparklines and change pills |
| **System** | S, M | live CPU with a sparkline, RAM used/total, uptime |
| **Storage** | S, M | disk usage ring, turning amber past 75% and red past 90% |
| **Calendar** | S, M, L | today; full month grid with today ringed |
| **Notes** | S, M, L | a real editable sticky note, saved as you type |
| **Workspace** | S, M, L | a building site with one lit bay per coding agent |
| **Now Playing** | S, M, L | album art, progress and working transport controls |

Sizes follow the phone convention — small (2×2), medium (4×2), large (4×4) —
rather than free resizing. Fixed sizes are what make a board of mixed widgets
look composed instead of scattered, and they mean each tile is designed for three
known shapes rather than an infinite number.

Right-click **Stocks → Edit symbols** for the watchlist. The same endpoint serves
equities, indices (`^GSPC`) and crypto (`BTC-USD`), so one list can mix them.

## Workspace

A building site with one bay per coding agent. A bay is warm, lit and busy while
that agent is working — its figure swinging a mallet, a block added per tool
call, the tool name and elapsed time in the caption — and dark and slumped when
it isn't. It answers "is anything happening, and who's doing it" from across the
room without reading a word.

Claude publishes to it already. **Codex: see [agents/AGENTS.md](agents/AGENTS.md)**
— it's the full contract plus how to draw your own avatar. Two steps, neither of
which touches any existing code:

1. Run `agents/signal.py --agent codex --event <hook>` from your hooks.
2. Replace `CodexAvatar` in `widgets/core/avatars.py` (it's a deliberately plain
   placeholder right now, so the bay is never empty).

State lives in `%APPDATA%\AgentWorkspace\<agent>.json` — **one file per agent**,
so two agents can never race each other and neither has to know the other
exists. An agent that reports in without being registered still gets a bay,
drawn with the placeholder avatar. A turn with no events for 90 s is treated as
over, so a crashed agent doesn't hammer forever.

Avatars are drawn procedurally in a unit space with a shared rig that supplies
breathing, the forward lean, the idle slump and the tool swing — so an avatar
only describes what it *looks* like, not how it moves. Notably `draw_arm()`
attaches the tool to the hand by construction; deriving a hand position
separately is how you end up with a hammer floating next to a hand.

## Placement

**Widgets drop exactly where you let go of them.** Grid snapping is off by
default — it fights you the moment you try to line something up against a
wallpaper feature or a window edge. Turn it on with **Snap to grid** in the tray
if you'd rather have it, and **Lock positions** to stop accidental drags.

The only thing always enforced is that a card stays reachable on a monitor. That
clamp also runs on load, because a saved position can stop being valid — a
monitor gets unplugged, the resolution changes, or the card geometry changes
between versions — and a widget that restores off-screen can't be dragged back.

`WidgetCard.start()` also re-asserts the position immediately after `show()`.
Windows can place a window itself the first time it appears, overriding the
`move()` that ran before — and since the layout is persisted at startup, the OS's
choice then gets saved and faithfully restored on every later launch. That is how
a board once cascaded into the top-left corner and stayed there.

## Typography

The widgets are set in **SF Pro** — Apple's actual system font, loaded from
`fonts/` at startup via `addApplicationFont`. That registers the faces with this
process only: nothing is installed, nothing else on the machine changes, and
deleting the folder cleanly falls back down the stack.

`typography.py` reproduces three things, and only the first is the typeface:

- **Optical sizes.** Apple sets 20pt and up in SF Pro *Display* and smaller text
  in SF Pro *Text*; the display cut is more tightly spaced and finer-detailed.
  The resolver switches at that break.
- **Tracking.** The part people miss, and it matters more than the face. SF
  applies a size-dependent letter-spacing curve — loose at caption sizes,
  tightening to about −3% by a 40pt number. `TRACKING` is Apple's published
  table; `tracking_percent()` interpolates it. Large numerals set at default
  spacing are the clearest giveaway that type isn't Apple's.
- **Label hierarchy.** Apple's label colours are pure white/black at descending
  alpha (100% / 60% / 30%), not separate tinted greys. Bluish greys are a large
  part of why a widget can look wrong even with the right face.

If `fonts/` is empty the stack falls back through Inter, Helvetica Neue (SF's
direct ancestor and Apple's system font through iOS 7–8), then Segoe UI Variable.

**Weights available:** Text Regular and Semibold, Display Bold and Light. Apple
ships more (Display Regular/Medium/Semibold, Text Medium/Bold), but see the note
below — Qt resolves a request to the nearest available weight in the family, so a
Semibold display request renders as Display Bold. The difference is slight.

<details>
<summary>Getting SF Pro off Apple's .dmg on Windows</summary>

Apple distributes SF Pro as a macOS disk image, and there's no HFS-aware
archiver here. `scratchpad/sfpro_extract.py` does it with the standard library:
UDIF image → HFS+ volume → `SF Pro.pkg` (a xar archive) → Payload (gzip cpio) →
fonts. Three things each cost a run:

1. **UDIF block tables.** Each BLKX carries a `firstSectorNumber` its chunks'
   output sectors are relative to, and a `dataOffset` its input offsets are
   relative to. Ignore either and the volume assembles into plausible garbage
   that still contains valid magic numbers — it looks fine until a decompressor
   chokes halfway through a stream.
2. **The Payload's declared encoding is `application/octet-stream`** even though
   the bytes are plainly gzip, so compression has to be sniffed, not trusted.
3. **The cpio is `odc`, not `newc`.** Different magic and a completely different
   header layout, so a newc parser finds zero records and reports no fonts
   rather than failing.

What it can't do is follow HFS+ extents. The 207 MB Payload is fragmented, so a
linear read runs out about 73 MB in — which still yields ~103 MB of cpio and 17
of the faces, including every one the widgets draw with. Recovering the rest
would mean writing an HFS+ catalog and extent reader for a handful of extra
weights.
</details>

## Style

The cards are drawn to sit next to Apple's rather than Windows':

- **Continuous corners, not circular ones.** A plain rounded rectangle meets its
  straight edge at an abrupt change of curvature, and that is exactly what reads
  as a hard or sharp corner. `squircle()` starts curving about 1.5× the radius
  out from the corner and eases in, so the transition is invisible.
- **No border.** A drawn 1px outline was the other half of the sharpness. The
  card is separated from the desktop by a soft ambient shadow instead.
- **A transparent gutter around the card**, 18px, inside a correspondingly larger
  window. Without it the antialiased corner is clipped dead on the window
  boundary — which looks like a cut corner — and there is nowhere for a shadow
  to fall.
- **Sentence-case headers** in a secondary colour, not letter-spaced uppercase.
  That treatment is a Windows convention and was the single most un-Apple thing
  on the card.

## Frosted glass

The cards are genuinely see-through: a blurred copy of the desktop shows through
each one, tinted, clipped to the squircle. **Frosted glass** and **Glass
strength** (Clearer / Balanced / Frostier) are in the tray menu.

Windows offers two materials for this — Mica and Acrylic — and neither works
here. Both are applied by DWM to the whole *window*, and the window is
deliberately larger than the card so the corners have room to antialias and the
shadow has somewhere to fall. A DWM material paints that transparent gutter too,
which puts every card in a grey block. DWM can round the window itself, but only
at its own ~8px radius, nowhere near the 28px the cards use.

So `glass.py` does the blur instead, which is possible because of *where* these
widgets live: parked on the desktop layer, the thing behind them is the
wallpaper. It composites the wallpaper across the virtual screen exactly as the
shell does (scale-to-fill per monitor), blurs it once, and each card paints the
slice sitting behind it — at any radius, perfectly antialiased.

The blur is a heavy downscale, a few box passes at that small size, and a smooth
upscale. At 1/14 scale a 3000×1920 desktop is a ~214×137 image, so it costs
milliseconds and only reruns when the wallpaper or display layout actually
changes (checked every 20s, rebuilt only on a real change).

⚠️ **The Windows 11 Mica/Acrylic backdrop is off by default and should stay off**
— see above.

**Known limit:** the glass shows the blurred *wallpaper*, not whatever window
happens to be behind. In the default desktop layer that is exactly correct,
since nothing is between the cards and the wallpaper. When the board is peeked
to the front over an app, the material still shows wallpaper.

## Layers

Phone and Mac widgets sit *on* the desktop, below your work — which is right
until you actually want to look at one. So the board has two states, and the
hotkey toggles between them.

| mode | behaviour |
|---|---|
| **On the desktop** (default) | parked behind every normal window |
| **Floating** | ordinary window layer |
| **Always on top** | over everything, permanently |

Verified by z-order: in desktop mode the cards sit at indices 9–13 with the
foreground app at 0; in top mode they occupy 0–4 with the first app at 5.

Widgets never take focus (`WS_EX_NOACTIVATE`), never appear in the taskbar or
Alt-Tab (`WS_EX_TOOLWINDOW`), and a slow timer re-asserts the parked layer so a
focus change can't leave one stranded on top of your work.

## Data sources

Everything comes from the OS or from a keyless API, so nothing needs installing
and nothing needs an account.

- **CPU / RAM / disk / uptime** — `GetSystemTimes`, `GlobalMemoryStatusEx`,
  `GetDiskFreeSpaceExW`, `GetTickCount64` via ctypes. CPU is a delta between
  successive samples, which is why the tile polls rather than reading on demand.
- **Stocks** — Yahoo Finance's public chart endpoint, no API key. Gives price,
  previous close and intraday points for the sparkline in one call per symbol.
  It rejects obviously-scripted clients, so the request carries a browser
  `User-Agent`.
- **Weather** — [Open-Meteo](https://open-meteo.com), no API key. Location is set
  by you (right-click → Set location) rather than by IP geolocation: silently
  looking up where someone lives is a poor default for a thing that just wants to
  show the temperature. Fetching happens on a worker thread and hands the result
  back through a Qt signal, because a blocked network call must never freeze the
  board and a repaint must never be triggered off the GUI thread.

## Notes on the build

**It borrows your palette.** `widgets/core/palette.py` loads `tokens.py` out of
the shared `design_system` so these sit in the same visual family as the Face &
Finger Tracker and the Voice Transcriber. That package imports its own modules by
flat name so it can't be imported as a package — but `tokens.py` is pure data
with no Qt and no internal imports, so it loads cleanly on its own. Its directory
is *appended* to `sys.path`, never inserted, because it contains generically named
modules (`theme.py`, `components.py`) that must not shadow ours. There's a
built-in fallback palette if it isn't there.

**Qt's high-DPI scaling is off**, same reasoning as the desk gremlin: widget
positions are Win32 screen coordinates in physical pixels, and mixing those with
Qt's logical pixels drops cards a scale-factor away from where you put them.

**The wallpaper trick doesn't work here.** The classic way to embed widgets into
the desktop is to reparent them onto Explorer's `WorkerW` window. `winapi.py`
implements it, but on this Windows 11 build `find_wallpaper_host()` returns
`None` — the shell no longer exposes that layer the same way. The bottom-of-
z-order mode is the real path; the reparenting is left in as an opportunistic
upgrade for builds where it still works.

## The layout file

`%APPDATA%\PCWidgetsoard.json` holds the board, with `board.json.bak` one step
behind it.

`save()` **refuses to write an empty layout over a real one**, because the board
persists on startup: a single run that came up with no cards used to overwrite
the saved arrangement with an empty list, and every launch after that was empty
too — one transient failure erased the board permanently, and it looked exactly
like the app failing to start. `build()` also skips persisting when it was asked
for widgets and produced none, and says so on stdout instead of silently
succeeding at nothing. `--reset` still wipes it deliberately via
`allow_empty=True`.

## Self test

```bash
python main.py --selftest
```

Renders every widget at every size it supports, in both themes, to
`_selftest/*.png` plus a contact sheet per theme — so the design can be looked at
and iterated on without launching anything. The weather and stocks tiles are
given fixed sample data so a render never depends on the network or on the
market being open.

It also asserts that a free drop leaves a card exactly where it was released, and
that snap mode still snaps — the regression that matters most here.

Note that offscreen renders can't catch everything: the acrylic-backdrop problem
above was only visible in a real screenshot, because DWM composites the material
behind the window rather than into the widget's own painting.

```bash
python main.py --reset
```

Restores the default layout.

## Adding a widget

Subclass `WidgetCard`, implement `paint_content`, register it:

```python
class MyWidget(WidgetCard):
    kind = "mine"
    title = "My Widget"
    supported_sizes = ("small", "medium")
    refresh_ms = 5000

    def on_tick(self):
        self.value = get_something()

    def paint_content(self, p, r):
        self.label(p, QRectF(r.left(), r.top(), r.width(), 12), "Mine")
        self.text(p, r, str(self.value), 30, self.palette_.text)
```

The card handles chrome, dragging, snapping, the menu and persistence. Tiles only
draw. `label`, `text`, `bar` and the palette are provided; add it to
`load_builtin_tiles()` and it appears in the tray's Add menu.

## Licence and credits

MIT — see [LICENSE](LICENSE). Third-party terms are set out in
[THIRD-PARTY.md](THIRD-PARTY.md); the short version:

- **Qt/PySide6** is LGPL v3 and is *not* redistributed here — you `pip install`
  it yourself. That changes if you ever freeze this into a single `.exe`.
- **Weather data** from [Open-Meteo](https://open-meteo.com), published under
  CC BY 4.0.
- **Sky photographs** from [Unsplash](https://unsplash.com), under the Unsplash
  License. Photographers credited in `assets/weather/CREDITS.md`.
- **SF Pro is not included.** Apple's licence doesn't permit redistributing the
  files, so `.gitignore` excludes them and the type stack falls back to Inter,
  Helvetica Neue, then Segoe UI Variable. See `fonts/README.txt` to add them
  locally.
- **The stocks widget reads an undocumented Yahoo Finance endpoint**, which is
  against their terms of service and can break without warning. Swap it if that
  matters to you — THIRD-PARTY.md lists alternatives.

Not affiliated with, authorised by, or endorsed by Apple.

## Before publishing

```bash
python tools/prepublish.py
```

Refuses to pass if a font file or anything credential-shaped would be committed,
if `LICENSE` still has a placeholder name, or if the notices are missing. Exit 0
means it's safe to push.
