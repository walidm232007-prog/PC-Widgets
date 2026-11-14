# First run

What PC Widgets shows the first time it opens after the installer, and the
plan for building it. The look was settled on mockups with the developer on
2026-11-12; this file is the record of what was decided and why.

| milestone | state |
|---|---|
| M1 – groundwork: tell a fresh install apart, Windows' theme and units, the town search, Weather's Set your town button | done |
| M2 – the welcome screen: every screen dims, Welcome, Start Setup | done |
| M3 – the steps: location, the widget slideshow, the dock, the finish | done |
| M4 – finish: readable button colours everywhere, docs, installer build, a live try | next |

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
4. **Dock.** The Glass Dock on or off, its look (glass or a solid colour, as
   Settings words it) and the neon outline, with the real dock along the bottom
   changing as you choose. Music stays on the left and the dock takes the
   taskbar's place, as the app's defaults already are; Settings changes both.
5. **All set.** A short closing screen with the essentials (drag to move,
   right-click for options, Ctrl+Alt+W), then Done lifts the dim while the
   widgets settle into place, and one tip, once, points at the tray.

Rules that hold throughout:

- **Only a fresh install sees it.** An update never does: layouts saved
  before the setup existed load as done. While it runs the desktop has no
  widgets yet; Done puts up the ones chosen. Skip (or Esc) on any screen keeps
  what has been chosen so far and the starter board's choices for the rest, so
  on the welcome itself it means the five starter widgets. Settings › App has
  "Set up again", where the same button is Cancel and changes nothing.
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
- Start Setup eases between its outline and its white fill over 0.18 s, its
  label going from white to dark with it, and Skip Setup brightens the same
  way. The fade steps on the frame clock, at the monitor's own refresh rate,
  and a fade turned round half way takes half as long.
- It is topmost only while it has the focus; Alt+Tab puts it behind whatever
  you switched to, with the taskbar back, and returning raises it again.
- `tray.show_window()` opens the welcome instead of the app window while the
  setup is pending, from wherever it is asked: Setup's Finish, the Start menu,
  the tray, a second launch. It waits while a full-screen game has the
  foreground and opens when it leaves. At startup `welcome_due()` empties the
  board for an ordinary launch and gives a start at sign-in the starter widgets,
  with no welcome until the window is opened.
- Skip puts up the starter widgets, saves the setup as done and lifts the veil
  over 0.45 s. (Start Setup ended it the same way until M3 brought the steps.)
- Windows' animation effects off: no fades. Transparency effects off: a plain
  dark screen instead of the wallpaper.
- Selftest: `_check_welcome`, offscreen. A veil is never activated there: a
  window the size of a screen in the foreground reads as a full-screen game.

## What M3 laid down

- `widgets/ui/setup_steps.py`, on the main screen's veil. Start Setup blurs
  Welcome away and the steps come in: **Where are you?**, **Choose your
  widgets**, **Add the Glass Dock?** and **You're all set**, with step dots at
  the top, Skip at the top right and Back and Continue at the bottom corners.
  Pages are laid out on the 1920 x 1080 design and scaled to the screen, so a
  laptop gets the same page smaller and a 16:10 or ultrawide screen gets room
  round it; the corner buttons stay at the screen's own corners. A step gives
  way to the next in 0.4 s, the old page gone by half way and the new one
  coming in after it, rather than both at half strength.
- Nothing is applied until the end. `Choices` holds the town, units, theme,
  the widgets and their sizes, and the dock; `apply()` makes the board match
  in one batch (`Board.batch()`), with one save. Done applies everything; Skip
  applies what has been chosen so far and the starter board's choices for the
  rest. On a second run (Settings › App › Set up again) the steps open on the
  board as it is, Skip is Cancel and changes nothing, and Done touches a widget
  already there only where its choice changed - a new size, or the town and
  units on the Weather card that showed them. Widgets the slideshow doesn't
  offer (the coding agents' two) are never touched.
- New widgets go down the right edge exactly as the starter board lays them
  out (`pack()`): a column a medium card wide, small cards in pairs, filled
  from the top and never back up, round any card already there, carrying on to
  the left when a column runs out of height. The starter five land where they
  always have.
- Location: the town search is `TownQuery`, shared with the popup a card opens
  (debounced, on a worker thread, stale answers dropped). Matches list under
  the box with their regions; the arrow keys and the pointer move the
  highlight, Enter or a click picks, Esc closes the list. Weather's three sizes
  show the highlighted town - the first match's forecast is fetched at once,
  others once the highlight rests 0.35 s - and the picked one after. One
  forecast per town, fetched in Celsius; °F is worked out from it, so the
  units switch costs no fetch. Offline, the cards give way to a line saying
  the forecast will come once there's a connection.
- Widgets: the gallery's Everyday and Your PC widgets, in its order, drawn as
  the gallery draws its samples, Weather with the town from step 1 (or its Set
  your town button if none). The slideshow glides on the frame clock; the
  wheel, the arrow keys, a drag (with a flick) and a click on a neighbour move
  it. The real size control and Add button sit over the middle slot once it
  has settled; while it moves, each slot paints its own copy, and a slot's
  controls fade out before its Added label fades in. Light/Dark re-draws every
  widget at once; a count beside it says how many are added.
- Dock: a card of three rows - the dock on or off, its look, the neon outline -
  in the theme chosen, the last two greyed while the dock is off. The preview
  is a real `GlassDock(preview=True)` reading a stand-in board (`_DockBoard`)
  that carries the setup's choices, with the taskbar's pins and open apps read
  on a worker thread as the setup opens. It sits where the real dock will, no
  wider than the room between Back and Continue, and its neon moves while the
  page is up. The real dock changes only at the end, and comes up last, in the
  look already chosen.
- All set: the essentials with the keys drawn as keys, and Done. The dim lifts
  over the widgets in their places, and one tray tip, once, says where the app
  went (the dock's tray arrow when the dock is on).
- Keys: Esc is Skip (or closes the town list first), Enter is Continue (or
  picks the highlighted town), the arrows move the slideshow, Tab goes through
  every control with a white ring.
- Selftest: `_check_setup_steps`, offscreen with the town search and forecast
  canned, and `_check_welcome` now follows Start Setup into the steps. Renders
  go to `_selftest/_setup_*.png`.
