# PC Widgets

iPhone/Mac-style widgets for the Windows desktop. Rounded translucent cards that
live *on* your desktop, line up with each other as you drag them (the way macOS
widgets do), and come to the front on a hotkey.

## Download

**[Get the installer from the latest release](../../releases/latest)** -
`PC-Widgets-Setup-<version>.exe` - and run it. Windows 10 or 11, 64-bit. No
admin, no Python, nothing else to install. It isn't signed yet, so Windows may
say "Windows protected your PC" the first time: **More info**, then **Run
anyway**. A newer installer run over it updates it and keeps your widgets.
`packaging/README.md` covers how it is built.

From 1.1.0 it keeps itself up to date: once a day it asks GitHub whether
there's a newer release, downloads it in the background, and installs it the
next time it starts - or straight away from **Settings › Updates › Restart to
update**. It installs nothing that isn't signed with the release key built into
it, and the request sends nothing about your PC. **Automatic updates** in the
same place turns it off. Copies of 1.0.2 move to 1.1.0 by running its
installer once, over the top. [AUTO-UPDATE.md](AUTO-UPDATE.md) has the design.

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

The first time it opens after installing, every screen dims for a short setup:
your town for the weather, the widgets you want from a slideshow of the real
ones, and whether to use the Glass Dock. **Skip** keeps whatever you've chosen
so far and the five starter widgets otherwise; **Settings › App › Set up again**
runs it once more, starting from the desktop as it is, and there **Cancel**
changes nothing. It never opens at sign-in or over a full-screen game.
[FIRST-RUN.md](FIRST-RUN.md) has the design.

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
  Select one to change its size, the display it's on, glass or solid
  background, background and text colours, and background photo. **Show on
  desktop** points it out; **More settings…** opens the same menu as
  right-clicking it (location, watchlist, time style and so on); **Remove
  widget** takes it off, with an Undo.
- **Presets** — your displays as they're arranged, drawn to scale with every
  widget where it sits, and your saved layouts: **Save this layout**, then
  **Apply** one to switch to it (see [Presets](#presets)).
- **Settings** — theme, accent, glass (liquid or frosted, its blur and its
  strength), opacity, where widgets sit (on the desktop, floating, always on
  top), snap and lock, start with Windows, automatic updates, Set up again, and
  Quit.

Filled buttons use a deeper shade of the accent than the accent itself, so the
white label on them passes 4.5:1 whichever accent is picked; the raw blue gives
3.7:1 and green 2.3:1. Calendar's today circle does the same.

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
| **Weather** | S, M, L | conditions now; faces for the hours and days ahead, next-hour rain, sunrise and sunset |
| **Stocks** | S, M, L | 1 / 3 / 6 symbols with intraday sparklines and change pills |
| **System** | S, M | live CPU with a sparkline, RAM used/total, uptime |
| **Storage** | S, M | disk usage ring, turning amber past 75% and red past 90% |
| **Calendar** | S, M, L | today; full month grid with today ringed |
| **Notes** | S, M, L | a real editable sticky note, saved as you type |
| **Workspace** | S, M, L | a building site with one lit bay per coding agent |
| **Now Playing** | S, M, L | album art, progress and working transport controls |
| **Timer** | S, M, L | a timer, a stopwatch, focus sessions or a countdown to a date, one face at a time |
| **Headlines** | S, M, L | the latest stories from news feeds you choose, with their pictures; a click opens one |

Sizes follow the phone convention — small (2×2), medium (4×2), large (4×4) —
rather than free resizing. Fixed sizes are what make a board of mixed widgets
look composed instead of scattered, and they mean each tile is designed for three
known shapes rather than an infinite number.

**Timer** has four faces (right-click → **Face**, or the dots under a preview
in the gallery), and each keeps its own state, so a timer left running behind
the stopwatch still rings:

- **Timer**: running, it's Apple's StandBy timer - a dial of short ticks that
  go dark as the time runs down, unwinding clockwise from twelve, round the
  time written as Apple's Clock writes it (5:00 for the whole first second,
  0:00 only once it's over), its length under it in small orange capitals, a
  grey ✕ to cancel and an orange button with a dark mark to pause. Not yet
  started, the medium and large sizes set it as iOS's timer control does:
  minutes on a ruler, a tick a minute and a number every five, lit up to the
  chosen minute and going out of focus toward both ends, as if seen past the
  mark through a lens (three blurs of it, blended column by column from sharp
  in the middle to the softest at the ends), with the length big and thin
  beside it and **Start Timer** under it. The ruler scrolls under its mark: drag
  it, click a minute, or turn the wheel - a minute a notch, ten seconds under
  a minute, five past an hour. It glides rather than jumps, along a spring
  that never overshoots, at 60 frames a second on the display's own refreshes
  and evenly spaced (every third on a 200 Hz monitor, so 67). Dragged, it
  follows the pointer exactly, one repaint a frame however fast the mouse
  reports, and settles on the nearest minute when let go, carrying on a
  little after a flick. A glide frame costs about 2 ms: the ruler is drawn
  and blurred once as one long strip, on a thread of its own, and a frame
  only cuts out the part in view and blends it, drawn over the card's kept
  layer (paint_live) rather than redrawing the card. Blurring the view afresh
  took 7 ms a frame, and a glide at 200 frames a second took over a core. Until
  the strip is built, a tenth of a second or so, the ruler is blurred afresh,
  so it's never seen sharp. Saving the length waits for the wheel to stop. The running dial's ticks fade out one by one
  rather than switching off. The small size is all dial and the wheel.
  **Duration** on the menu has presets and **Other length…**.
- **Stopwatch**: hundredths at medium and large, laps on the grey button while
  it runs, and the large size lists them with the best lap green and the
  worst red, as Apple's Stopwatch does.
- **Focus**: the same dial, in indigo for 25 minutes of focus and green for
  a 5-minute break, with a 15-minute break after the fourth round, the rounds
  as dots and today's sessions counted at large. The lengths are on the
  menu, and **Start the next one by itself** runs straight on.
- **Countdown**: days to a date you name (**Count down to…**), then hours and
  minutes on its last day, then **Today**. A new card counts down to New Year.

When a timer or a focus phase runs out, the dial flashes, Windows shows a
notification from the tray icon, and Windows' own alarm chime plays (**Sound
when done** turns it off). A countdown arriving gets the notification too.
Times are saved as moments on the clock - when a timer ends, when a stopwatch
started - so a running timer carries on through a restart of the board. One
that ran out while the board was closed shows as done, without ringing late.
The digits use tabular figures, so a counting time doesn't shuffle sideways
as its digits change width. The main button is a solid disc in its colour -
orange for a timer, green and red for a stopwatch, indigo for focus - with a
dark mark on the bright ones and a white one on the deep, as StandBy draws
them: Apple Clock's tinted discs went missing on a bright wallpaper through
frosted glass. On light glass, orange text and the ruler take a deeper orange.
A running dial redraws ten times a second and a stopwatch twenty; with nothing
running the card redraws only when what it shows changes.

**Headlines** shows the latest stories from news feeds you choose: right-click
→ **Feeds…** and put a feed's link on each line, RSS or Atom. A new card
starts on BBC News and The Verge. It's laid out after Apple's News widget.
At small, the top story's picture fills the card under its headline; at
medium, two stories sit beside their pictures; at large, the top story runs
under a banner of its picture, with as many more below it as fit. Each story
shows its source, in a colour of its own (a feed's colour comes from its place
in the list, so up to seven never share one), and how long ago it went up:
12m ago, 3h ago, Yesterday. A click opens the story in your browser; a drag
from anywhere on the card still moves it. The feeds are read every 15 minutes
on a thread of their own, two minutes after a fetch that got nothing, or
straight away with **Refresh now**. A fetch that gets nothing keeps what's
shown. Stories are merged newest first, the same story from two feeds once.
By time alone one busy feed would fill the card (BBC News publishes every few
minutes), so another source's story goes next when it's at most three hours
older, and never three from one source in a row while another has one
waiting. Only text, links and picture addresses are taken from a feed, never
its markup, and a story whose link isn't a web address is dropped.

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
each one, tinted, clipped to the squircle. By default the cards and the Glass
Dock are [liquid glass](#liquid-glass), built on the same copy of the desktop;
**Glass style → Frosted** (in Settings, or the tray menu beside **Glass** and
**Glass strength**) gives them this blur instead. Frosted glass is also what the
app window's sidebar uses, and what a card or the dock falls back to.

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
changes (checked every 20s, rebuilt only on a real change). A live wallpaper
such as Wallpaper Engine draws over those files, and then the glass is built
from what it shows instead (see [Live wallpapers](#live-wallpapers)).

⚠️ **The Windows 11 Mica/Acrylic backdrop is off by default and should stay off**
— see above.

**Known limit:** the glass shows the blurred *wallpaper*, not whatever window
happens to be behind. In the default desktop layer that is exactly correct,
since nothing is between the cards and the wallpaper. When the board is peeked
to the front over an app, the material still shows wallpaper.

## Liquid glass

The cards are liquid glass, after Apple's in iOS 26 and macOS Tahoe. It is
clearer than frosted glass, and the wallpaper bends along the rim as it would
through a thick pane. Near each edge a card shows what lies a little further
in, red bending a touch further than blue, and a thin line of light runs round
the rim, on every side but fading across the corner away from a light at the top
left. The middle shows what is straight behind it.

The maths is [Glisse](https://github.com/0kzh/glisse)'s (Kelvin Zhang, MIT,
credited in THIRD-PARTY.md). Glisse does this for the web with an SVG
displacement map over the live page: within a band along the rim, each point
looks inward by the slope of a shallow dome there, smoothed over the band's
inner edge, with red, green and blue pulled 1.1, 1.05 and 1 times as far.
`lens.py` runs the same maths in numpy over the board's own copy of the
wallpaper, which works because, as above, the wallpaper *is* what is behind a
card.

A browser redoes this every frame on the GPU; a card does it once. Its bent
slice is kept until the card moves, changes size or the wallpaper changes, so a
board sitting still costs no more than it did. Measured on a Ryzen 5 5600G with
a game running beside it, a large card bends in about 4.5 ms, a medium one in
about 3 and a small one in about 1.5. Three changes from Glisse, all for
widget-sized glass, make that possible:

- **The band is held to 26 px and the reach to 70 px.** Glisse scales both with
  the pane, which suits a 44 px switch, but turned a 348 px card into a
  fishbowl.
- **The bend is worked out on a 2 px grid and drawn scaled up**, a quarter of
  the work. The clear copy of the desktop is kept at half size anyway (about
  4 MB for a 3000×1920 desk) and frosted about as much as Glisse's own 3 px
  blur, so a finer grid would find no more detail. The rim, the one sharp
  thing, is drawn at full size. That frost is Glisse's default too, and it is
  needed: with none, a busy wallpaper's fine detail ran straight through the
  cards' small grey labels.
- **The rim follows the cards' continuous corners**, measured to the curve
  itself, where CSS draws it round a circle.

A drag bends the glass again at every step, on a 4 px grid instead of 2 px.
That's a quarter of the work again, and the detail it loses can't be seen on a
card in motion; where the card is let go, it's bent in full. A whole frame of
dragging a large card, glass and everything on it, costs about 4 ms that way,
against 2 ms for frosted glass. On a PC slow enough that a step would still take
longer than 12 ms, the card shows frosted glass while it moves.

The clear copy is built on a thread after each wallpaper change, taking about
0.4 s here. Until it lands, the cards keep the glass they had, and the new
wallpaper's blurred copy waits to land with it. On a first start there is
nothing to keep, so the cards show frosted glass until then. A card also stays
frosted without numpy (a checkout with only PySide6).

Whenever the glass behind a card that's sitting still changes, the card fades
from the old glass to the new over 0.4 s instead of switching. That covers a
new wallpaper, a live one moving, the clear copy landing, and Glass style or
Blur being changed. Only the glass fades: the tint and everything on the card
are drawn over it as usual. A card being dragged or resized switches, since
there's no old glass of the same spot to fade from. The dock fades its glass
the same way.

**Settings → Look** has the choices. **Glass style** is Liquid or Frosted, for
the cards and the dock together; Frosted drops the clear copy altogether.
**Blur** sets how much liquid glass softens the wallpaper. Light is the
default, Glisse's 3 px. None shows it crisp, and More is about 6 px, for a busy
wallpaper behind small text. A new Blur rebuilds the clear copy on its thread,
and the cards keep the old one until the new one lands, so nothing flashes
frosted while it changes. **Glass strength** is the tint over either style, and
its third step is now Stronger rather than Frostier, since the blur is a
setting of its own.

The Glass Dock, its music menu and its window previews are liquid glass too.
Each is bent once for where it comes to rest, and its slide, rise or neon
redraws that without bending it again. GLASS-DOCK.md has how. **Not liquid
yet:** the app window's sidebar is still frosted.

## Live wallpapers

Wallpaper Engine, Lively Wallpaper and apps like them never change the
wallpaper Windows has on record. They draw their own window in the desktop's
wallpaper layer, under the icons, so the glass went on showing the picture set
in Personalisation, which wasn't on screen at all.

`livewall.py` finds that layer: a `WorkerW` window holding the app's window,
inside `Progman` on Windows 11 24H2 and later, beside the icons' window before
that. `PrintWindow` with `PW_RENDERFULLCONTENT` grabs it offscreen, DirectX and
all. The grab has the live wallpaper where it draws and Windows' own wallpaper
on any monitor it leaves alone, and never the icons or any window, the cards
included. Nothing is shown and nothing moves.

When the board starts with a live wallpaper running, its first frame is built
before anything is drawn, about 0.3 s, so the cards come up showing it
instead of the files' picture for a moment first.

The board looks for a live wallpaper every 3 s with a few window lookups, so
the glass switches over soon after one starts. When it quits, the glass is
built from the files again, and fades in like any other change.

### Live glass

**Settings → Look → Live glass**, on by default, makes the glass follow the
wallpaper as it moves, at the wallpaper's own pace, up to 60 frames a second.
Wallpaper Engine draws 25 a second unless its own FPS limit (its Settings →
Performance) is raised; the glass keeps to whatever it draws. It's true glass
that still shows up in screenshots. The see-through dock leaves itself out of screen captures,
because a capture of the screen behind it would otherwise contain the dock.
Live glass never captures the screen. It captures the desktop window itself,
which holds the wallpaper and the icons but no other window, the cards
included, so nothing has to be left out of captures.

It uses Windows' own capture (Windows.Graphics.Capture, through `wgc.py`, with
no package needed). Each frame comes from the graphics card as the wallpaper
draws it, and only when it draws one. The yellow border Windows puts round a
captured window is turned off, and so is the cursor. Where that capture can't
be had, live glass falls back to PrintWindow on the wallpaper layer, about ten
frames a second: on Windows 10, where the border can't be turned off, or if
the capture fails part-way.

- **Only behind the cards in view.** A frame is read back as just a patch
  behind each card, about a quarter of a millisecond each. A card hidden behind
  a window that covers it entirely is skipped, until it's back in view. Only
  ordinary app windows count there, never the invisible overlays some game
  launchers and graphics drivers put over the whole screen.
- **Only where it moved.** A card's patch counts as moved when its thumbnail's
  average changes or any one part of it changes clearly, so a light drifting
  across one corner counts and a scatter of sparkles doesn't. Cards with
  nothing new behind them aren't touched.
- **Bent on the graphics card.** The frame is already there, so the liquid
  glass is made there too (`gpuglass.py`): three compute shaders halve and
  frost the box round the cards' patches once a frame, then bend each card's
  pane from it, with `lens.py`'s own field. The panes are bent side by side
  into one texture and come back in one read: about a quarter of a millisecond
  of CPU a frame for six cards, where bending one on the CPU took 4 to 6 ms.
  They match the CPU's bend to within a level. No package is needed: the
  shaders are compiled by `d3dcompiler_47`, part of Windows. Where Direct3D 11
  can't be had, the CPU bends as below.
- **Every change, but not flicker.** A pane goes to its card when it changed
  by three levels anywhere, or a tenth of one on the whole, so a card over a
  still corner isn't repainted. The line was ten levels once, and the small
  cards stuttered: with less of the moving picture behind them they crossed it
  far less often than the big ones beside them, 15 frames a second of the 40
  their glass changed in, in steps of 14 levels. Now every card takes every
  frame its glass changed in, 90% of them within 21 ms of the last at 50
  frames a second. A level or two (rounding, or a video's noise through the
  frost) still isn't worth a repaint.
- **The glass alone.** A card repainted for new glass and nothing else lays
  what was drawn over its glass last time - the tint, the lit rim, the content
  - back on the new glass as one image, rather than drawing it again. Anything
  else asking for a repaint, a clock ticking or a hover, draws it all. Drawing
  the content was most of a repaint: a repaint for new glass takes about
  0.3 ms where it took 0.4 to 1.9, and nine cards following a 50
  frames-a-second wallpaper took a fifth of a core to repaint instead of two
  thirds. That layer is drawn on a straight-alpha image, as the dock is, so
  the cards' type is greyscale, as Apple draws it. Drawn straight onto the
  window, Qt gave it ClearType, and every letter had red and blue edges on the
  glass. Now Playing composes its frosted buttons again for each new glass,
  about 2.3 ms. Before, the board's repaint found nothing to recompose and
  drew its old picture, so the glass behind it moved only when a poll or the
  pointer happened to recompose it. A card a picture covers has no glass to
  repaint for.
- **Bent ahead.** Without the graphics card, each moving card's liquid glass is
  bent on the background thread, on the coarser grid a card uses while it's
  dragged, and the card's repaint is then only drawing. Once the wallpaper
  rests, each card is bent in full and fades into it. Frosted cards get a
  freshly blurred slice instead.
- **No fades between frames.** They follow each other too quickly.
- **Paced.** Never more than 60 frames a second, and never building more than
  half the time. Nothing says a frame has come, so it's asked for just before
  the next is due at the wallpaper's own pace, learnt from the frames' own
  times, then every millisecond until it comes: each frame is taken a
  millisecond or so after it arrives, asking two or three times a frame. The
  waits keep to the millisecond. Python's `Event.wait` rounds a short one up
  to Windows' 15.6 ms clock ticks, which had held live glass to about 32
  frames a second whatever the wallpaper drew. While nothing is drawn, asking
  costs next to nothing. Nothing at all while a full-screen window has the
  focus.
- **Whole looks beside the frames.** Every 3 s of motion the whole frame is
  taken for the blurred copy and the glass behind no card. Building that takes
  about 30 ms, two frames at 60 a second, so while the graphics card bends the
  cards it's built on a thread of its own and the cards' frames go on.
- **Cheaper repaints.** Each card's shadow is drawn once a size and kept;
  its nine curves were half of a plain card's repaint. The weather card's
  icons are built once each too. A repaint that draws everything takes about
  1 ms for a plain card and 2 for the weather card, and only a change of what
  a card shows asks for one.

Measured on a Ryzen 5 5600G with a 3000×1920 desk and Wallpaper Engine at
about 30 frames a second, moving behind six cards, with the glass bent on the
graphics card: the background thread took about a tenth of one core, before
the cards shared one frost, and the six cards' repaints about another tenth.
On the CPU it was a third of a core at 25 frames. With nine cards following
Wallpaper Engine at 50 frames a second, the ten-level line held the whole
process to 74% of one core by leaving frames out, and every frame drawn in
full took 91%. Repainting for the glass alone took the nine cards' repaints
from 65% of a core to 20%, measured on stand-in frames at the same pace.
Fewer or smaller cards cost less, and a wallpaper that rests costs almost
nothing.

Desktop icons behind a card show through its glass, as they would through
glass. Shown over apps (see [Layers](#layers)), the cards keep showing the
desktop behind them: seeing the windows behind them would mean leaving them
out of captures.

### Smooth updates

With **Live glass** off, the glass catches up every few seconds and fades:

- **A look every 3 s, on a thread of its own, and every 6 s while the
  wallpaper keeps moving.** A wallpaper that starts moving is caught within
  3 s, and one that never stops costs half as much.
- **Judged card by card.** A 16×16 thumbnail of what lies behind each card
  (and the dock) is compared with the one its glass was built from. Only the
  cards whose thumbnail moved get new glass. The others keep theirs and
  aren't even repainted, so motion behind one card leaves the rest alone, and
  motion behind no card builds nothing.
- **Rebuilt only where it moved.** The blurred copy is redone whole, since
  that takes about 17 ms. The clear copy is only patched behind the cards
  that moved: about 1.5 ms a card, where the whole desk takes 150 ms. Each
  patch reaches 24 px past its card, further than the frost does, so it comes
  out exactly as a whole build would. While the wallpaper keeps moving, the
  clear copy is built whole once a minute too, for glass that's behind no
  card, such as the dock's menus or a card that has just been moved.
- **Faded in.** A card fades into its new glass over 0.4 s (see
  [Liquid glass](#liquid-glass)).
- **Not at all while a full-screen window has the focus.** A game covers the
  desktop, glass and all. Wallpaper Engine pauses itself then too, by default,
  and a paused wallpaper's identical frames are skipped straight away.
- **A black grab is ignored.** An app that won't draw into one leaves the
  glass showing what it did.

A grab takes about 40 ms, 15 ms of it CPU; the rest is waiting on the
compositor. The grab keeps its bitmap from one look to the next, since making
a new one each time cost a third again. A look that finds nothing new costs
about 20 ms of CPU, so about 0.65% of one core. With the wallpaper moving
behind all six cards of a typical board, an update costs about 170 ms of CPU,
including the six cards bending again and fading. Every 6 s, that's about 3%
of one core.

## Glass Dock

**Settings → Glass Dock → Floating dock** puts a dock along the bottom of the
main display: music, Start, the taskbar's pins and open apps, and Windows' own
tray, network, volume and clock. It can stand in for the taskbar, hides like
an auto-hiding one when a window is maximised, and can carry a neon outline.
It is off by default. `GLASS-DOCK.md` covers how each part works and why, and
`py -3.13 tools/dock_selftest.py` checks it without putting anything on screen.

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
  by you rather than by IP geolocation: silently looking up where someone lives
  is a poor default for a thing that just wants to show the temperature. A card
  without a town shows a **Set your town** button (or right-click → Change town),
  and the town is picked from the places that match what you type, each with its
  region - "Springfield" is a dozen places, and the old box quietly took the
  first. Only the typed text is sent, to Open-Meteo's geocoding service. A new
  card starts in °F where Windows' region measures the US way, °C elsewhere.
  Fetching happens on a worker thread and hands the result back through a Qt
  signal, because a blocked network call must never freeze the board and a
  repaint must never be triggered off the GUI thread.

  **Faces.** Right-click → Face, or the dots under the preview in the gallery,
  picks how a weather card looks, separately for each size. Classic is the
  original design. The others follow Apple's Weather widgets for the Mac:
  Conditions, Forecast, Next hour and Sunrise and sunset at small; Hourly and
  Next hour at medium; Hourly and daily and Next hour at large. Three things
  about them are worth knowing:

  - *Everything runs on the forecast place's clock.* Open-Meteo answers in the
    place's own time zone, and the hourly row, the sun's position and "sunset at
    4:13 PM" are all worked out from that answer, never from the PC's clock, so
    a card set to a town eight hours away still puts the sunset in the right
    slot.
  - *Next-hour rain comes in quarters, not minutes.* Open-Meteo's finest step
    is 15 minutes, so an hour is four readings. Each sits at the middle of its
    quarter and the bars between follow the straight line joining them; the
    sentence above the chart ("Rain starting in 15 min") is worked out from the
    readings themselves, not from the smoothed bars.
  - *The sky behind each face is measured, not picked by eye.* The condition's
    photograph fills the card under a layer of its sky colour, and how strong
    that layer is was measured for each photo: the least that keeps white text
    and the 90% white of secondary text at 4.5:1 over the brightest 2% of that
    photo. The storm photo is dark already and needs half; the bright clouds of
    the partly cloudy one need 82%. Night uses the same photos under navy. The
    selftest measures every composite again, so a new photo can't quietly make
    a face unreadable.
- **Headlines** — the RSS or Atom feeds you list, read through `net.fetch`
  like every other fetch: `https://` only (an `http://` link is asked for over
  HTTPS), a feed read to 2 MB at most and a picture to 3 MB, and a request that
  says nothing but the app's name. Only the pictures of the stories that can be
  on show are fetched, five at most, scaled down to 640 px as they arrive, and
  one already on the card isn't fetched again.
  BBC News sends 240 px thumbnails, soft across a large card's banner, so
  those are asked for at 480 from the same image server.

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

**Every fetch is HTTPS only, stays on HTTPS, and stops at a size cap**
(`net.fetch`). Python's `urlopen` on its own opens `http://`, `file://`,
`ftp://` and `data:` addresses as readily as `https://`, and follows a redirect
from HTTPS to plain HTTP. That mattered for the calendar: its address is one
you paste in, and a published calendar's link works like a password - anyone
holding it can read the calendar - so fetching it over plain HTTP would hand it
to the network. The calendar now takes `https://` or `webcal://` (the form
Outlook and Google hand out, the same link over HTTPS) and says so for anything
else, and a redirect off HTTPS is refused. Replies are read to 8 MB at most (2 MB
for a forecast or a quote, which are a few KB): past that the answer is wrong or
hostile, and reading on would only fill memory.

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

A drop has to end the move, too, so no tile may take the name of one of the
card's own private methods. Python lets a subclass shadow one without a word,
and the card then calls the tile's in its place. That is how dropping the
Headlines card once left every card on the board rimmed, as if one were still
being moved.

The display checks put a board on a made-up desk shaped like this machine's (a
main landscape monitor, a portrait one on its left), then rearrange, unplug and
replug it — on another port — and change its resolution, asserting where every
card ends up and what the saved layout says each one belongs to. The preset
checks save, change, apply and undo a layout across both, apply one with a
display missing, and fill the tray's Presets menu without a tray icon appearing.
The glass check composites two made-up monitors with a solid picture each and
reads back which colour landed where. The live wallpaper check plays the
wallpaper layer, its grabs and a full-screen game with stand-ins, so it checks
the same on any PC whatever its desktop runs, and the boards the other checks
build never follow a real one. The smooth glass check moves a live wallpaper
behind one of two cards. Only that card's glass may be rebuilt, coming out
exactly as a whole build would, and only that card may fade. It also checks
that a new wallpaper lands whole, with no frosted moment in between. The live
glass check reads patches back from a GDI bitmap, then follows frames behind
two cards: one that moved goes straight in, bent ahead, while the other is left
alone; at rest it's bent in full; a hidden card is skipped; frames from Windows'
capture are read the same way, the first taken whole; the kept shadow is the
shadow; and no card is ever left out of screen captures. The GPU glass check
bends cards on the graphics card (or Windows' software one) and compares them
with the CPU's bend, and six cards bent together with each bent alone. It runs
the frame pacing on a simulated clock against 60 and 26 frames-a-second
wallpapers, times the short waits, and checks that a whole look along the way
is built beside the cards' frames rather than in their way. The glass layer
check hands a three-level change on and holds a two-level one back, then
repaints a clock for new glass alone and asks for its kept layer laid on it to
come out pixel for pixel as drawing the layer again, and the layer drawn
again after update(). It checks the layer's type has no coloured edges, and
that a card a picture covers never goes stale. The timer check runs the Timer
card on a stand-in clock: a timer that counts, pauses, rings once and survives
a restart, but stays quiet for an end that came while the board was closed;
the wheel's steps; stopwatch laps; four focus rounds to a long break; a
countdown read in days, hours and minutes and ringing as it arrives; and a
button click, a ruler drag and a ruler click that don't move the card. It draws every face at every size in
both themes to `_selftest/_timer_dark.png` and `_timer_light.png`. The
headlines check never touches the network. Feeds written the way BBC News, The
Verge, NPR and an RSS 1.0 wire write theirs have to come out as plain titles
with their source, date and picture. The merge has to drop repeats and mix one
busy feed with the others. A fetch through a stand-in for `net.fetch` has to
ask for `https://` only, capped, saying only the app's name, and a fetch that
gets nothing has to leave the stories on show. A click on a story opens it,
and a drag from one moves the card without opening anything; let go, the card
lands as any card does, and the board isn't left rimmed. Self-test boards
never fetch (`PCWIDGETS_NO_FEEDS`). It draws every size, with pictures and
without, and the empty states in both themes to `_selftest/_headlines_dark.png`
and `_headlines_light.png`. The TLS check reads what the board's
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

## Licence and credits

MIT — see [LICENSE](LICENSE). Third-party terms are set out in
[THIRD-PARTY.md](THIRD-PARTY.md); the short version:

- **Qt/PySide6** is LGPL v3. A checkout doesn't redistribute it — you
  `pip install` it yourself. The installer does, under the LGPL: the licence
  texts are in its program folder's `licenses\` folder, the Qt DLLs stay
  separate files you can replace, and THIRD-PARTY.md says how and where Qt's
  source is. The installer also carries Python and numpy, their licences
  beside Qt's.
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

Not affiliated with, authorised by, or endorsed by Apple.

## Before publishing

```bash
python tools/prepublish.py
```

Refuses to pass if anything credential-shaped or a font file other than the
bundled Inter would be committed, if Inter or anything in `vendor/` would go
without its licence, if the LGPL text the installer needs for Qt is missing,
if `LICENSE` still has a placeholder name, or if the notices are missing. Exit
0 means it's safe to push.
