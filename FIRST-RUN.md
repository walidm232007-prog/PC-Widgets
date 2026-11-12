# First run

What PC Widgets shows the first time it opens after the installer, and the
plan for building it. The look was settled on mockups with the developer on
2026-11-12; this file is the record of what was decided and why.

| milestone | state |
|---|---|
| M1 – groundwork: tell a fresh install apart, Windows' theme and units, the town search, Weather's Set your town button | done |
| M2 – the welcome screen: every screen dims, Welcome, Start Setup | done |
| M3 – the steps: location, the widget slideshow, the dock, the finish | next |
| M4 – finish: readable button colours everywhere, docs, installer build, a live try | not started |

## Why it needs one

Until now a fresh install put five widgets down the right edge of the main
screen - always in dark, even after a light installer - and opened the app
window on its Gallery. Nothing said the widgets were already on the desktop,
that they could be dragged or right-clicked, or where the app went when the
window closed. The Weather card had no town and read "Right-click → Set
location" until someone found the menu. And the app couldn't tell a first run
from a layout it had failed to read: both logged "starting from a default
board".

## The design

Shown once, on a fresh install, as a full-screen moment rather than a dialog:

1. **Welcome.** As Setup closes, every screen dims and blurs behind its own
   wallpaper and "Welcome" comes into focus, set in Inter, then an outlined
   **Start Setup** button (it fills white under the pointer, and with keyboard
   focus) and **Skip Setup**. The steps run on the main screen; the others stay
   dimmed and empty.
2. **Location.** "Where are you?": a town search that lists matches as you
   type, each with its region, and °F/°C. Along the bottom, the Weather widget
   in its three sizes shows the highlighted town. Only the typed text is sent,
   to Open-Meteo; nothing looks up where the PC is.
3. **Widgets.** A sideways slideshow of the real widgets along the bottom of
   the screen. The one in the middle is in focus, with a size picker and
   Add/Added; the scroll wheel, the arrow keys or a drag move it one widget at a
   time. A Light/Dark switch above it changes every widget at once.
4. **Dock.** The Glass Dock on or off, its look (frosted glass or a solid
   colour) and the neon outline, with the real dock along the bottom changing
   as you choose. Music stays on the left and the dock takes the taskbar's
   place, as the app's defaults already are; Settings changes both.
5. **All set.** A short closing screen with the essentials (drag to move,
   right-click for options, Ctrl+Alt+W), then Done lifts the dim while the
   widgets settle into place, and one tip, once, points at the tray.

Rules that hold throughout:

- **Only a fresh install sees it.** An update never does: layouts saved
  before the setup existed load as done. While it runs the desktop has no
  widgets yet; Done puts up the ones chosen, and Skip (or Esc) on any screen the
  five starter widgets. Settings gets "Set up again".
- **It never interrupts.** Not at sign-in - a Startup-shortcut start with no
  layout puts up the starter widgets and waits for the first time the window
  opens - and never over a full-screen game. Alt+Tab takes you away and back.
- **It answers to Windows' own settings.** The theme starts on Windows' app
  mode and °F/°C on the region. No motion when animation effects are off; a
  plain dark screen when transparency effects are off. The dim adapts to the
  wallpaper so white text stays readable on any picture.
- **Nothing the installer asked is asked again** (Start with Windows, the
  desktop shortcut), and the rest of the look stays in Settings.

## What M1 laid down

- `store.load()` tells three cases apart: no layout anywhere is a fresh
  install (`setup = "pending"`), a layout without the field is an update
  (`"done"`, the default), and a layout that can't be read is a fault (a plain
  default board and a log line). Until M2 a fresh install still shows the
  starter widgets as before; the flag is recorded and nothing reads it yet.
- `first_run_board()` starts in Windows' app mode
  (`winapi.apps_use_light_theme`) with the starter Weather card in the region's
  units (`winapi.temperature_unit`). New Weather cards get the region's units
  through `WidgetCard.new_config()`; cards already on a desktop keep theirs.
- `weather.find_towns()` returns every match with its region through
  `net.fetch`; `widgets/ui/townsearch.py` is the popup a card opens, with
  separate messages for no match and no connection.
- `palette.button_fill()` deepens an accent until white text on it passes
  4.5:1 (the blue accent gives 3.7:1, green 2.3:1). The new button uses it; M4
  moves every filled button onto it.
- Selftest: `_check_first_run` and `_check_town_search`, with no network.

## What M2 laid down

- `widgets/ui/welcome.py`. One frameless window per screen, covering all of
  it, taskbar included, each painting that screen's wallpaper - the blurred
  copy the cards' glass already keeps, softened further - under a dark veil.
  The veil starts at 65 % and darkens a bright wallpaper until white text keeps
  7:1. The main screen's window carries Welcome, Start Setup and Skip Setup and
  is the one in Alt+Tab and the taskbar, as "PC Widgets Setup".
- The intro runs on the frame clock: the veil fades in over 0.3 s, Welcome
  comes into focus from 0.45 to 1.25 s, the buttons appear by 1.8 s. A click or
  Enter during it jumps to the end. The windows fade with their own opacity, so
  a large screen isn't repainted every frame.
- Keys: Esc is Skip Setup, Enter is Start Setup, Tab reaches the buttons.
  Focus starts on the screen, not on Start Setup, so it opens as an outline.
- It is topmost only while it has the focus; Alt+Tab puts it behind whatever
  you switched to, with the taskbar back, and returning raises it again.
- `tray.show_window()` opens the welcome instead of the app window while the
  setup is pending, from wherever it is asked: Setup's Finish, the Start menu,
  the tray, a second launch. It waits while a full-screen game has the
  foreground and opens when it leaves. At startup `welcome_due()` empties the
  board for an ordinary launch and gives a start at sign-in the starter widgets,
  with no welcome until the window is opened.
- Skip puts up the starter widgets, saves the setup as done and lifts the veil
  over 0.45 s. Until M3 brings the steps, Start Setup ends it the same way.
- Windows' animation effects off: no fades. Transparency effects off: a plain
  dark screen instead of the wallpaper.
- Selftest: `_check_welcome`, offscreen. A veil is never activated there: a
  window the size of a screen in the foreground reads as a full-screen game.
