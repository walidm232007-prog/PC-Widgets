# PC Widgets

iPhone/Mac-style widgets for the Windows desktop. Rounded translucent cards that
live *on* your desktop, snap to a grid, and come to the front on a hotkey.

## From a fresh clone

Windows 10 or 11 and Python 3.11+.

```bash
pip install -r requirements.txt
```

```bash
python main.py
```

That is the whole install. Then, optionally, make yourself launchers - in this
folder, on the Desktop and in the Start menu - and have it start with Windows:

```bash
python main.py --app --shortcut --start-menu --install-startup
```

The shortcuts are not in the repository — a Windows `.lnk` stores an absolute
path, so it would only ever be right on one machine. The command above builds
them for yours.

The typeface comes with it: **Inter** ships in `fonts/`, under the SIL Open Font
License, and is loaded for this app only. So does the audio capture the
visualiser reads the speakers with (PyAudioWPatch, in `vendor/`): installed in
the user's site-packages it was invisible to the board on the machine this was
built for, and the visualiser silently fell back to a peak meter that moves all
five bars together. numpy, which the spectrum also needs, is the one optional
package in `requirements.txt`; without it the visualiser uses that peak meter.

## Everyday use

Double-click **PC Widgets** — the launcher sits at the top of this folder once
you've run `--app`, and `--shortcut` puts a copy on your Desktop. (`run.bat`
works as a fallback.) It puts your widgets on the desktop and opens the app
window, where everything is chosen and changed. Closing the window leaves the
widgets where they are; click the tray icon, or launch PC Widgets again, to
bring it back.

**Ctrl+Alt+W** brings the whole board to the front and sends it back down again.
Right-click the tray icon for a quick menu with the common settings.

## The app window

Four pages, down the side:

- **Gallery** — every widget, previewed live and at actual size. Pick a
  size and press **Add**: the widget lands in the first clear spot on your
  display and is lifted above your windows for a moment, ringed in the accent
  colour, so you can see where it went. With more than one display, **Add to**
  picks which - it starts on the one the window is on. A badge shows which ones
  you already have.
- **On your desktop** — the widgets you have, under a heading per display.
  Select one to change its size, the display it's on, frosted or solid
  background, background and text colours, and background photo. **Show on
  desktop** points it out; **More settings…** opens the same menu as
  right-clicking it (location, watchlist, time style and so on); **Remove
  widget** takes it off, with an Undo.
- **Presets** — your displays as they're arranged, drawn to scale with every
  widget where it sits, and your saved layouts: **Save this layout**, then
  **Apply** one to switch to it (see [Presets](#presets)).
- **Settings** — theme, accent, frosted glass and its strength, opacity, where
  widgets sit (on the desktop, floating, always on top), snap and lock, start
  with Windows, and Quit.

The previews are the real tiles painted offscreen, not artwork, so the gallery
can't drift from what a widget actually looks like. Nothing in the window keeps
its own copy of the board: a change made from a card's right-click menu or the
tray shows up in the window, and the other way round.

**The look.** Shaped like the widgets, and solid but for the sidebar. In the
dark theme the window is pure black with dark-grey panels on it; in the light
theme it is pure white with light-grey ones: the gallery's cards, the grouped
settings lists. Every panel is cut with the widgets' own corner, the same
continuous curve at the same 26 px radius, and so is the window itself. A panel
inside another (a preview inside its card) has its radius cut by the gap between
them, so the two curves run parallel instead of pinching. On flat colour only
the shapes and the type are left to read, so both have to be exact. Buttons and
the small controls are capsules, and every control eases between states instead
of snapping (`widgets/ui/theme.py`).

The sidebar is frosted glass - dark grey in the dark theme, light grey in the
light one - running to the window's edge, with a hairline between it and
whichever page is open. It is frosted the way Windows 11's Mica is. Mica isn't
see-through either: it is the desktop wallpaper, blurred and tinted, as though
the window were a pane laid over the desktop. So the sidebar shows the slice of
the widgets' own blurred wallpaper (see [Frosted glass](#frosted-glass)) that
lies behind it, from whichever monitor it is on, under a grey tint and a faint
grain, and the slice follows as the window moves. The light tint is the heavier
of the two, since a light grey has to stay light over a dark wallpaper. The
glass is a picture of its own, a sidebar wide, rebuilt only when the window
moves or the wallpaper changes, so scrolling a page costs what it always did.

Windows can't draw that corner: the corners it rounds are a fixed 8 px, and
circular. So the window is a layered one, transparent per pixel, that draws its
own outline, and its own soft shadow in a transparent margin round it. The
stock title bar is replaced by drawn caption buttons, but the window keeps what
Windows hangs off a real frame (Aero Snap, Win+arrow, the system menu, resizing
from the edges) by keeping the frame styles and making the frame zero-sized
instead. It resizes from the shadow just outside its edge too, where Windows 11
keeps its own invisible resize border. Maximised or snapped, it fills its space
exactly, with square corners, as Windows 11's own windows do. The shadow's
faintest fringe is cut to nothing, because in a layered window any pixel that
isn't fully transparent takes the mouse (`widgets/ui/chrome.py`).

**Scrolling.** The mouse wheel glides rather than stepping: each notch eases
over about a fifth of a second, ticking at the refresh rate of the screen the
window is on - 5 ms on a 200 Hz panel. A glide is only as smooth as a frame is
cheap, and content drawn over the window's own backdrop means a scroll can't
just shift pixels: every frame repaints everything in view. So cards, controls,
text and the backdrop each paint once into a cached image and are blitted until
something about them changes, which took a scroll frame from about 12 ms to
under 3 ms to paint. A layered window then costs a little more to put on
screen, since Windows takes its own copy of whatever changed, so the backdrop
under a scroll is filled as plain colour rather than copied from its picture;
a typical frame comes to under 4 ms all in. For the same reason the window carries no
stylesheet (one on a window routes every widget inside it through Qt's slower
stylesheet style), live thumbnails refresh one at a time and never mid-scroll,
and the gallery's hover highlight waits until a scroll has settled rather than
lighting every card the content glides under.

Launching the app while it's already running doesn't start a second board. The
new copy finds the running one's hidden control window, hands it the right to
come to the foreground, asks it to open its window and exits — about a tenth of
a second.

## Background photos

Every widget except Now Playing can have a photo behind it — **Choose photo…**
in the app, or right-click → Appearance → Background image…. A photo is almost
never the card's shape, so the editor opens straight away: the whole photo with
the card cut out of it, everything outside dimmed, and inside it **the real
card** — the photo as the card will paint it, with the card's own text, scrim and
corners on top. Placing a photo so the time doesn't land across someone's face
needs the time to be there while you do it.

Drag to move it, scroll to zoom (toward the cursor), double-click to reset.
**Fill card** is the smallest size with no gaps; **Fit whole photo** shows all
of it, with the card's own colour around it. The widget on the desktop follows
along as you edit, and Cancel puts everything back. **Adjust…** (or
right-click → Adjust background image…) reopens the editor later.

A crop is saved as the point of the photo that sits at the card's centre plus a
zoom, not as a rectangle cut out of it. A rectangle only fits one shape — switch
a medium card to large and a crop saved as "this 2:1 strip" would distort or be
thrown away — whereas a centre and a zoom carry over to any size, Files' free
size included. Zoom is relative to *cover*, so 1.0 is exactly the centre-crop
every card used before this existed, and a card with no crop saved looks just as
it always did. The editor and the card place the photo through the same
function (`widgets/core/imagefit.py`), so what the editor shows is, to the pixel,
what the card paints.

Photos are decoded the right way up (phone photos are usually stored sideways
with an EXIF note) and no larger than 2400 px on the long side: a 48-megapixel
photo decoded at full size is ~190 MB of pixels to fill a card a few hundred
wide.

## Starting with Windows

**Start with Windows** in the tray menu toggles it, and **Add desktop shortcut**
and **Add to Start menu** make more launchers - the Start menu one is also under
Settings, and is how PC Widgets turns up when you type its name into Start, or
gets pinned there. Or from a terminal:

```bash
python main.py --install-startup
```

`--remove-startup` undoes it, `--shortcut` makes the desktop one, `--start-menu`
the Start menu one (in your own Start menu folder, so no admin is needed), and
`--app` recreates the launcher in this folder.

The launchers are Windows shortcuts, so they store an **absolute** path. If you
move or rename the project folder, fix them all in one go:

```bash
python main.py --app --shortcut --start-menu --install-startup
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
login, and runs `main.py --background`: the widgets come back with the PC, but
the app window stays closed until you ask for it. A Startup shortcut made by an
older version runs plain `main.py`, which would now open the window at every
boot, so the app checks the shortcut when it starts and rewrites it if needed.
(The check reads the `.lnk` file directly — it stores its arguments as UTF-16
text — so it costs nothing when there's nothing to fix.)

The other launchers open the window, so they're set to run in a normal window
rather than minimised. That matters more than it looks: Windows applies a
shortcut's "Run: Minimised" to the first ordinary window the process shows,
which would put the app window straight onto the taskbar. The window also undoes
that itself, because a board started minimised at boot shows its first ordinary
window whenever you first open the app, hours later.
The icon is the mark, four white tiles. `widgets/core/mark.py` draws it for the
tray, the app window's sidebar (where the light theme gives it the text colour,
since white would vanish on the pale panel) and `tools/make_icon.py`, which
generates `assets/widgets.ico` at nine sizes — Windows picks a different one
for the taskbar, the Start menu and Explorer, and a 16px tile drawn at 16px
keeps its corners where one downscaled from 256px turns to mush.

The **Now Playing** widget reads the system media session through the `winrt-*`
namespace packages. **A copy ships in `vendor/`** and is loaded ahead of anything
installed, so there is nothing to install — see `vendor/README.txt` for why it is
vendored rather than a dependency, and `THIRD-PARTY.md` for its licence. Delete
that folder and it falls back to an installed copy, then to reading the track
from window titles, saying so rather than inventing a position.

Everything else needs nothing beyond PySide6. Every icon, chart and card is drawn
at runtime with `QPainter` — there are no image assets.

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

**Now Playing's bars** are the sound actually leaving the speakers, split from
bass on the left to hi-hats on the right: kick and sub-bass, the bass line,
snare body and vocals, the snare's crack and claps, then hi-hats and cymbals
(`widgets/core/audio.py`). All five are read against one reference - the
loudest band right now - with a fixed tilt per band, set from real music
played through the analyser so a balanced track gives five comparable bars.
Scaling each bar
to its own range instead was tried and made things worse: a band with almost
nothing in it was stretched to full height too, so a bass note's attack and
harmonics lit every bar and the whole visualiser jumped on each note. The
upper bars lose most of a held sound, so a pad or a cymbal wash doesn't keep
them hovering; the two bass bars keep all of a held note, on a gentler curve,
since taking it away drained an 808 to nothing a second into the note while it
was still playing.
A band that jumps is a hit and goes straight to full
height - if it lands near the loudest band - then the bar falls, quicker the
higher the band, so the bass thumps and the hats tick. The bass bands are read over a longer window than the
rest: the kick needs the frequency resolution, and a hat needs its tick kept
sharp. While a track plays, the bars redraw at the pace the audio is read,
about 47 times a second, repainting only the strip they sit in.

## Workspace

A building site with one bay per coding agent. A bay is warm, lit and busy while
that agent is working — its figure swinging a mallet, a block added per tool
call, the tool name and elapsed time in the caption — and dark and slumped when
it isn't. It answers "is anything happening, and who's doing it" from across the
room without reading a word.

Claude publishes to it already. **Codex: see [agents/AGENTS.md](agents/AGENTS.md)**
— it's the full contract plus how to draw your own avatar. Two steps, neither of
which touches any existing code:

1. Run `agents/agentsignal.py --agent codex --event <hook>` from your hooks.
2. Replace `CodexAvatar` in `widgets/core/avatars.py` (it's a deliberately plain
   placeholder right now, so the bay is never empty).

State lives in `<agent>.json` — **one file per agent**, so two agents can never
race each other and neither has to know the other exists. It is written to
*two* directories, `%APPDATA%\AgentWorkspace` and `.agentbus` inside the project,
and read as whichever copy is fresher: on the machine this was built for the
board process could not read `%APPDATA%` at all while reading the project folder
perfectly, and a status widget that silently shows nobody working is worse than
writing two hundred bytes twice. An agent that reports in without being registered still gets a bay,
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

The only thing always enforced is that a card stays reachable on the monitor it
was let go on. That clamp also runs on load, because a saved position can stop
being valid — the card geometry changes between versions, say — and a widget
that restores off-screen can't be dragged back. Monitors coming, going and
changing are dealt with before it gets that far (see below).

`WidgetCard.start()` also re-asserts the position immediately after `show()`.
Windows can place a window itself the first time it appears, overriding the
`move()` that ran before — and since the layout is persisted at startup, the OS's
choice then gets saved and faithfully restored on every later launch. That is how
a board once cascaded into the top-left corner and stayed there.

## Multiple displays

**A widget belongs to a monitor, not to a spot.** Windows measures every display
in one shared space whose origin is the top-left of the *main* one, so plugging a
monitor in on the left, making another one main, or dragging the arrangement
about in Display settings changes the coordinates of everything on every screen
without anything on them moving. A layout saved as positions comes apart the
first time that happens.

So each widget is anchored (`widgets/core/displays.py`): to its monitor, by the
monitor's device path — which names the panel's model and the port it's on — and
to a spot on that monitor's work area. Wherever the monitor sits in the
arrangement today, the widget is on it.

- **Rearranged**: the widgets on a moved monitor move with it, and nothing else
  does.
- **Unplugged** — or a DisplayPort monitor asleep long enough for Windows to drop
  it: its widgets wait on the main display, clear of the ones already there, and
  the saved layout still says where they belong. When the monitor comes back,
  they go home. Windows moves windows off a vanished monitor itself, sometimes
  before the board has heard about it, so every save first checks the monitors
  are still the ones the cards were placed on; otherwise one autosave in that
  gap would record the waiting widgets as belonging wherever Windows had put
  them.
- **On another port**, or under a new device path after a driver update: still
  the same monitor, recognised by its model. Of two identical monitors, the one
  still connected is never mistaken for the one that isn't, and a new monitor in
  exactly the old one's place is taken as its replacement.
- **At a new resolution**: each widget keeps its distance from the nearer edge,
  on each axis. A column 24 px in from the right edge stays 24 px in, where
  spreading positions out in proportion would nudge every card by a different
  amount and the layout would come apart.
- **Dropped on another monitor**, a widget belongs there — a waiting one
  included. **Display** in the app's Layout section sends one across without a
  drag, keeping as near its spot as the other screen's shape allows.

Displays are named by where they sit — **Main**, **Left**, **Right**, **Above**,
**Below** — rather than by number. Windows' numbers are per session and often
differ from the ones Display settings shows, and nobody remembers which monitor
is 2, but everyone knows which one is on their left. Beside that is the
monitor's own name, from `QueryDisplayConfig`: the older APIs call nearly every
monitor "Generic PnP Monitor".

The frosted glass is per monitor too (see [Frosted glass](#frosted-glass)), and
so is going quiet behind a game: a fullscreen game on one monitor stops the
widgets under it repainting, while the ones on another screen — often the whole
reason for putting them there — carry on.

## Presets

**A preset is the whole arrangement, saved by name**: every widget on every
display, with its size, its look, its photo and crop, its settings, and the
displays themselves. Save one with **Save this layout** on the Presets page —
it arrives named "Preset 1", ready to type over — or from the tray's
**Presets** menu, and **Apply** one to switch to it. Each is drawn as a small map
of the desk it was saved on. The one your desktop matches exactly says
**In use**, and the tray ticks it.

Applying moves the widgets that are already there — the same widget, with the
same look and settings — rather than rebuilding them, and only adds and removes
the difference. A rebuilt card starts from nothing (weather and stocks fetch
again, a photo is decoded again), and a preset that only rearranges things
should look like things moving. **Undo** puts back what it replaced.

A preset saved with a display that isn't connected now still applies: those
widgets wait on the main display, belonging to theirs, exactly as if it had just
been unplugged. The other way round works the same: a widget that is waiting
when you save is saved as belonging to its own display, so a preset saved while
a monitor sleeps still puts its widgets back on it. To keep one where it's
waiting instead, drop it there or pick its display first. The `⋯` menu updates a preset to the current layout, renames it
or deletes it, each with an Undo. Names are unique, ignoring case, so saving
under a name that's taken replaces that preset.

Presets are kept in `board.json` with the layout, so they get its mirror and its
backup too.

## Typography

The widgets are set in **Inter**, Rasmus Andersson's open-source typeface. Its
two variable files ship in `fonts/` and are loaded at startup via
`addApplicationFont`, which registers them with this process only: nothing is
installed and nothing else on the machine changes. Inter replaced Apple's SF Pro,
whose licence covers designing for Apple platforms and doesn't allow shipping it
with a Windows app; Inter is the closest free relative, and its licence (the SIL
Open Font License) lets it travel with the code.

`typography.py` reproduces three things, and only the first is the typeface:

- **Optical sizes.** Apple sets 20pt and up in SF Pro *Display* and smaller text
  in SF Pro *Text*; the display cut is more tightly spaced and finer-detailed.
  Inter's variable font has the same split as a continuous `opsz` axis, from its
  text cut at 14 to its Display cut at 32, and the resolver sets it from the
  point size: captions get the text cut, the clock's digits the display one. A
  fallback with separate Text and Display faces switches at Apple's 20pt.
- **Tracking.** The part people miss, and it matters more than the face. Apple
  applies a size-dependent letter-spacing curve — loose at caption sizes,
  tightening to about −3% by a 40pt number. `TRACKING` is Apple's published
  table for SF; `tracking_percent()` interpolates it, and Inter gets the same
  curve. Large numerals set at default spacing are the clearest giveaway that
  type isn't Apple's.
- **Label hierarchy.** Apple's label colours are pure white/black at descending
  alpha (100% / 60% / 30%), not separate tinted greys. Bluish greys are a large
  part of why a widget can look wrong even with the right face.

If `fonts/` is missing the stack falls back through Helvetica Neue (SF's direct
ancestor and Apple's system font through iOS 7–8), then Segoe UI Variable.

**Weights:** all of them. The variable files carry every weight from Thin to
Black, upright and italic, so the clock's time styles are drawn from real faces
rather than synthesised. Width is the exception: Inter has no width axis, so the
clock's Condensed and Wide are a horizontal scale. Qt can only set the
optical-size axis from 6.7; on PySide6 6.6 everything draws in the text cut.

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
at its own ~8px radius, nowhere near the 26px the cards use.

So `glass.py` does the blur instead, which is possible because of *where* these
widgets live: parked on the desktop layer, the thing behind them is the
wallpaper. It composites the wallpaper across the virtual screen exactly as the
shell does, blurs it once, and each card paints the slice sitting behind it — at
any radius, perfectly antialiased.

"Exactly as the shell does" means monitor by monitor: each one's own picture,
fitted the way Personalisation says (fill, fit, stretch, centre, tile, or one
picture spanned across them all). The wallpaper path Windows reports for the
desktop, `SPI_GETDESKWALLPAPER`, is one path — with a different picture on each
monitor it names only one of them, or the shell's transcoded copy, and that put
one monitor's picture, blurred, behind every card on the others.
`IDesktopWallpaper` answers per monitor. A picture that has since been moved or
deleted (the shell keeps showing its own copy) falls back to the desktop-wide
one.

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

**Every fetch trusts Windows' root certificates and nothing else**
(`widgets/core/net.py`). Python on Windows also trusts the intermediate
certificate store, which collects intermediates over the years and keeps the
expired ones, and OpenSSL prefers a copy it trusts to the chain a server sends -
so a stale one there makes a valid chain fail with "certificate has expired",
while every browser on the machine is fine, because browsers use Windows' own
verifier. That took the weather card down whenever DNS handed out the one
Open-Meteo server on Let's Encrypt's newer chain, which runs through an ISRG
Root X2 cross-certificate that expired in September 2025. Leaving the
intermediate store out costs nothing: a server sends its own intermediates, and
Python on Linux and macOS never had that store to lean on.

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

`%APPDATA%\PCWidgets\board.json` holds the board, with `board.json.bak` one step
behind it: every widget with the display it belongs to and its spot there, the
displays themselves (so one that's unplugged can still be named, and told from a
new monitor), the preferences, and the presets. A layout saved before displays
were tracked has positions only; each widget is given the display it's on the
first time the board loads it.

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
given fixed sample data (`widgets/core/samples.py`, which the gallery uses too)
so a render never depends on the network or on the market being open. The app
window's four pages are rendered as well, as `_selftest/_app_*.png`.

It also asserts that a free drop leaves a card exactly where it was released, and
that snap mode still snaps — the regression that matters most here — plus that a
cropped photo always fills its card and lands on the part asked for, that the
crop editor keeps on Done and restores on Cancel, that the gallery's Add,
Remove and Undo really change the board, and that the app window is cut to the
widgets' shape - solid inside, see-through past the corner's curve - with a
shadow margin that resizes the window and goes when it is maximised. Its
sidebar has to reach the window's edge with a hairline beside it, turn red with
a red desktop behind it, and stay solid hanging off the edge of the desktop,
where a see-through gap would be a hole in the window.

The display checks put a board on a made-up desk shaped like this machine's (a
main landscape monitor, a portrait one on its left), then rearrange, unplug and
replug it — on another port — and change its resolution, asserting where every
card ends up and what the saved layout says each one belongs to. The preset
checks save, change, apply and undo a layout across both, apply one with a
display missing, and fill the tray's Presets menu without a tray icon appearing.
The glass check composites two made-up monitors with a solid picture each and
reads back which colour landed where. The TLS check reads what the board's
HTTPS trusts - Windows' roots only, and never the expired certificate that broke
the weather card - without going near the network.

The checks that save a layout redirect *both* copies of it — `%APPDATA%` and the
mirror in `.boardstate/` — to a scratch folder. They used to redirect only
`%APPDATA%`, so a selftest run wrote its test board over the real mirror and
then deleted it; on a machine where the board can't write `%APPDATA%`, the next
launch would have come up with whatever stale layout was left there.

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
`load_builtin_tiles()` and it appears in the tray's Add menu and in the gallery
(under "More" until it's given a section and a one-line description in
`CATALOG` in `widgets/ui/app.py`). If it needs network data to look like
anything, give it sample data in `widgets/core/samples.py` so its gallery
preview isn't an empty card.

## AirDrop (in progress)

**Real AirDrop, not a lookalike:** the Drop widget will send files to an
iPhone, iPad or Mac and take files from one, through AirDrop itself, with
nothing to install on the Apple side. `airdrop/` is its protocol core, and it
works: on a Mac it has sent photos to an iPhone on iOS 27 and received one back.
The widget, and the Windows side of the radio, are still to come.

AirDrop isn't a network protocol your router carries. It runs over Apple
Wireless Direct Link (AWDL), a Wi-Fi link straight between the two devices, so
the core runs wherever AWDL exists: built into every Mac, and on Linux under
[OWL](https://github.com/seemoo-lab/owl), an open reimplementation. On Windows
that means inside WSL2, with a USB Wi-Fi adapter handed to it, while the board
talks to it from the Windows side - the next milestone. That is also why it is
plain Python with no Qt, beside `agents/` rather than inside `widgets/`.

```bash
pip install -r airdrop/requirements.txt
python -m airdrop.selftest
python -m airdrop find
python -m airdrop send "Sam’s iPhone" photo.jpg
python -m airdrop receive --inbox ~/Downloads
```

**What it takes on the other side.** The iPhone has to be in **Everyone for 10
Minutes** (Contacts Only needs Apple-signed records nothing else can have),
with its screen on, and woken: an idle iPhone keeps its AirDrop receiver off
until a nearby sender's Bluetooth LE advertisement says someone wants to share.
On a Mac, `send` opens Finder's AirDrop window, which sends it; a PC will have
to send it itself.

**Written from a real iPhone, not from OpenDrop.** OpenDrop, the research
implementation everyone points at, stopped being able to send around iOS 16 -
an iPhone on iOS 27 reads its upload to the end and hangs up - and it is GPL,
which this app isn't. So an iPhone was recorded sending to a receiver that kept
everything it was given, and this does what it does: an Ask with a
`TransferID` and each file's size, and an upload in `application/x-dvzip`, the
same cpio archive cut into 128 KiB blocks that are each zlib'd or stored as
they are. The module docstrings carry the details.

**Receiving takes nothing on trust,** because in Everyone mode anybody nearby
can send. The server answers only connections that come in over the AWDL link,
asks before accepting every time, and takes an upload only if it follows an
accepted Ask with that Ask's `TransferID`. Unpacking refuses absolute paths,
`..`, links and devices, any name that wasn't accepted, and more data than was
offered; it writes into a staging folder and moves into the inbox only once the
archive is whole, never over anything already there. The Apple metadata files
(`._name`) an iPhone sends beside some photos are checked and dropped. The
selftest tries ten kinds of hostile archive and asserts each leaves nothing
behind.

## Licence and credits

MIT — see [LICENSE](LICENSE). Third-party terms are set out in
[THIRD-PARTY.md](THIRD-PARTY.md); the short version:

- **Qt/PySide6** is LGPL v3 and is *not* redistributed here — you `pip install`
  it yourself. That changes if you ever freeze this into a single `.exe`.
- **Weather data** from [Open-Meteo](https://open-meteo.com), published under
  CC BY 4.0.
- **Sky photographs** from [Unsplash](https://unsplash.com), under the Unsplash
  License. Photographers credited in `assets/weather/CREDITS.md`.
- **Inter**, the typeface in `fonts/`, is © The Inter Project Authors and ships
  under the SIL Open Font License 1.1 (`fonts/LICENSE-Inter.txt`). Apple's SF
  Pro is not used or included: its licence doesn't permit either.
- **The stocks widget reads an undocumented Yahoo Finance endpoint**, which is
  against their terms of service and can break without warning. Swap it if that
  matters to you — THIRD-PARTY.md lists alternatives.
- **The AirDrop core** needs `zeroconf` (LGPL-2.1) and `cryptography`
  (Apache-2.0 / BSD), installed from PyPI where it runs, not redistributed here.
  None of OpenDrop's GPL code is used.

Not affiliated with, authorised by, or endorsed by Apple. AirDrop is Apple's
trademark, named here only to say what this works with.

## Before publishing

```bash
python tools/prepublish.py
```

Refuses to pass if anything credential-shaped or a font file other than the
bundled Inter would be committed, if Inter would go without its licence, if
`LICENSE` still has a placeholder name, or if the notices are missing. Exit 0
means it's safe to push.
