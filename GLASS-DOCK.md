# Glass Dock

Turn on **Settings → Glass Dock → Floating dock**. A rounded dock floats just
above the bottom edge of the main display: music on one side, Start and your
apps in the middle, and Windows' own controls at the right. It starts off, so
updating PC Widgets doesn't change an existing desktop.

The capsule is 52 px tall inside a 64 px window (the rest is the floating gap
and room for its shadow), scaled with the display, and exactly as wide as what
is in it. App icons are 32 px - the size Windows draws them, so they come out
sharp rather than resampled. The first version drew them at about 23 px through
a scaled painter with no smoothing, which is why they looked jagged.

Glass is liquid glass, the same as the cards' (see the README's Liquid glass
section): the wallpaper clear under a tint, bent along the rim, with a thin
rim lit from the top left. Where it can't bend, before the board's clear copy
of the desktop is built or without numpy, it is frosted: the blurred
wallpaper under a tint, with a sheen on its upper half and a hairline lit
along the top edge. Solid colour is matte: one flat colour and an even
hairline. It kept the sheen and the lit edge at first, and still read as glass.

## Seeing through to the windows behind

The cards' glass is the wallpaper, because the wallpaper is what is behind
them. The dock floats over everything, so with a window open under it the
wallpaper was the wrong picture: a white page behind a dock showing a blurred
beach. With **Settings → Glass Dock → See through windows** (on by default),
the glass shows what is really there, blurred, or bent by liquid glass.

Windows' own materials can't do it here. Acrylic and blur-behind cover the
whole window, the shadow gutter included, so the capsule would sit in a grey
block. They get Windows' 8 px corners or a jagged region instead of the
squircle, and they can't be bent. So the dock grabs the strip of screen it sits
on and runs it through the same blur and bend as the wallpaper.
`SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` leaves the dock and its
music menu out of that grab, so the strip shows what is under them. The price
is that they leave every other capture too: **with See through windows on, the
dock doesn't appear in screenshots, recordings or screen sharing.** Turning it
off puts them back.

A grab of the strip takes about 5 ms on a Ryzen 5 5600G at 1080p, but only
0.4 ms of that is CPU: the rest is waiting on the compositor. All the work
happens on a thread of its own, never in the dock's 200 Hz frame:

- **Only while a window covers the strip.** With just the desktop behind it,
  the dock uses the wallpaper glass, which is the same picture for nothing. A
  quick look through the window list, about half a millisecond, decides.
- **About 50 a second while what's behind moves, ten while it's still.** The
  first version grabbed ten a second, and scrolling or a video behind the
  dock stepped visibly. Now each grab is compared with the last; while they
  keep changing it grabs again as soon as the thread has rested as long as
  it worked, so it never takes more than half a core. A quarter of a second
  with no change drops it back to ten a second, just to notice the next one.
- **The thread builds the glass too:** the half-size frosted planes liquid
  glass bends (3 ms in numpy; 5.8 ms through QImage), or the blur for frosted
  glass, never both. It also bends each new capture for the panes the dock
  last drew (3 ms), so the dock's own redraw is only drawing, about 2.3 ms,
  where bending there cost 4.9.
- **The dock redraws only when the strip changed.** An idle page behind it
  costs ten grabs a second, about 1% of a core.
- **Nothing while the dock is down**, so a maximised window or a full-screen
  game pays nothing. Sliding up, the dock starts with the last capture, as it
  does with the last bend, and a new one lands within a tenth of a second.

**Liquid glass blurs more while things move behind it.** On the live board the
capture kept up at 57 grabs and 45-51 new frames a second, measured from the
board's log while a page scrolled behind the dock, and that was still visibly
steppy. The screen moves the page every 5 ms; the glass moves every 20, a frame
or two late, and liquid glass is clear enough to show each jump. Faster would
take the graphics card (DXGI desktop duplication, or Windows' own acrylic,
which can't bend). So, the way a moving scene smears behind real frosted
glass, each new frame frosts the glass two steps harder, up to about 13 px of
blur, which hides the jumps; once nothing has moved for a quarter of a second
it eases back to the chosen Blur in six steps, one each 1/30 s. Frosted glass
is blurred enough already and stays as it is.

So a video playing behind the dock costs about half a core in all, a few
percent of a 12-thread CPU, and only while it plays there. Two Windows timer
details set the rate: `Event.wait` sleeps in 15.6 ms steps, so short rests use
`time.sleep`, which keeps to the millisecond from Python 3.11; and
`thread_time` moves in the same 15.6 ms steps, so the work is timed by the
clock from the end of the grab instead.

The window previews keep the wallpaper glass: they are opaque, and Windows
draws their edge.

## How the glass is kept

The dock bends its glass once for where it rests and keeps it, so a hover, a
cover fade or the neon redraws it without bending it again (1.5 ms for the
bend, on a Ryzen 5 5600G). Sliding down below a maximised window and back up,
it uses the bend from where it rests up, as the frosted glass was sampled
there for the whole slide: the window moves, and nothing is redrawn. While a
new app's slot opens, the capsule is a new width every frame. Then it is bent
the way a card is while being dragged: on a 4 px grid, at its width rounded up
to 16 px and drawn into it, about 0.5 ms a frame over a handful of widths.
Its rim is built from the two ends of a short capsule's rim with one column
stretched between them, which is exact, since a straight edge is lit the
same all along. Working out a rim for every frame's width cost 5 ms, a whole
200 Hz frame. Once the row settles, the dock is bent in full again.

## Neon outline

**Settings → Glass Dock → Neon outline**, or right-click the dock → Glass Dock
→ Neon outline, puts a glowing ring round the dock: a hot, near-white core on
the edge and a bloom of colour around it. Colours are a preset (Cyber, Cyan,
Magenta, Aurora, Sunset, Fire, Toxic, Ice), one of your own, or up to three,
blended round the ring. Motion is Static, Fade (in and out over three seconds)
or Circulate (one lap in six; a single colour runs as a comet with a tail).
Strength - Subtle, Medium, Bright, Intense - sets how wide and bright the glow
is. It is off by default.

The colour is laid along the outline by distance travelled round it, not by
angle from the centre: on a bar fifteen times wider than it is tall, angle
crawls along the long edges and races round the ends. The glow is a real blur,
built once per size and strength; a moving frame only re-colours the pixels the
glow covers, about 1.5 ms, 30 times a second while the dock shows. Static costs
nothing after the first frame. The window grows 10 px taller while the outline
is on, so the glow above the dock isn't cut off.

## Start

The Start button is the Windows 11 logo as Windows' own shell draws it - the
geometry and gradient of `Icons\WindowsLogo.svg` in the
`MicrosoftWindows.Client.Core` system app: two-by-two tiles, only the outer
corners rounded, cyan (#4DD2FF) at the top left to Windows blue (#0078D4).
The tiles and the gap are snapped to whole pixels, or the gap smears at this
size. `tools/dock_selftest.py` compares it with that file when Windows has it.
The logo is Microsoft's mark, used for the one thing it means: this opens Start.
Right-click opens the Win+X menu, as on the taskbar.

## Apps

Windows of one app share one button, grouped the way the taskbar groups them:
by the app ID a window or its package declares (Chrome's web apps and each
Store app get their own button), otherwise by program. The taskbar's own pins
come first, in its order, then the dock's own pins; other apps follow in the
order they opened, and stay put when the focus moves.

The taskbar's pins are read live from Explorer's list (`Taskband\Favorites`),
not the User Pinned folder: the folder holds shortcuts only, in no order, and a
Store app pinned to the taskbar has no shortcut at all. So **Pin to taskbar**
from Start shows up in the dock too. Unpinning one from the dock only takes it
off the dock - the taskbar is never changed - and **Show all taskbar pins**
brings it back.

An app that opens and isn't pinned slides in instead of appearing in one
frame: its slot widens from nothing while the icon grows and fades in, the
apps after it slide along, and the dock widens evenly about its centre. A
closed app shrinks out where it stood, and can't be clicked while it goes. It
takes a quarter of a second. Meanwhile the window stays wide enough for the row
both before and after and the row is centred inside it, so the window itself
is moved only at the start and the end, never every frame. A frame costs about
2 ms, plus about 7 ms with the neon outline on, whose glow is rebuilt for each
new width.

Icons are the shell's own artwork for the app - what Start and the taskbar
show, Store apps included - rendered at the size needed. Without an app ID the
window's own icon is used, then the program's. An app with no icon at all gets
its initial on a colour of its own. The shell's bitmaps are converted by hand:
`QImage.fromHBITMAP` dropped their transparency and turned them upside down.

Hover an app with windows open and after a moment a panel rises above it with
a live picture of each window, as on the taskbar: the app's icon and the
window's title on top, and a close button on the card under the pointer.
Click a picture to bring that window forward; close it from its button or with
a middle-click. Moving along the row while the panel is up switches to the next
app at once; moving away lets it go. The pictures are the compositor's own
live thumbnails (`DwmRegisterThumbnail`), video and all - nothing is captured
or copied. The compositor never draws one into a layered window, and Qt makes
every see-through frameless window layered (it registers fine, then shows
nothing), so the panel is opaque: the board's glass edge to edge, with Windows
11's own rounded corners, border and shadow. The glass is liquid, the whole
panel bent as one pane with Windows' corners for where it comes to rest, and
drawn there as the panel unfolds or glides, so it is bent once per place, not
once per frame. It has no lit rim: Windows draws this window's edge. Apps with
no window open keep their name as a tooltip.

Being opaque, the panel can't fade as a whole, so it rises: it unfolds upward
out of the dock with its bottom edge still, while the titles fade in and the
pictures fade in through the compositor's own opacity. Moving to another app
glides it across, the new cards fading in on the way; leaving folds it back
down. With the neon outline on, the panel gets one too: a see-through window
just behind it carries the dock's neon - same colours, motion and strength -
round Windows 11's corner shape, with its glow shining outward where an opaque
panel can't draw, and Windows' own hairline border is turned off so the tube
takes its place. Checked on a live desktop: a window shows
live, and a minimised one keeps its last picture; a window that has never been
on any screen shows nothing, which is why the selftest checks registration
and layout, not pixels.

Click an app to bring it forward, or to minimise it when it is already in
front; with several windows each click brings the next. Middle-click opens a
new window. Right-click lists its windows, and offers New window, Pin/Unpin
and Close. Pins can be Start apps, programs, shortcuts or links (**Pin an
app…**); the dock's pins are its own, and the taskbar's are not changed.

## Music

The music section shows Spotify or Apple Music through the same backend as Now
Playing: the cover, title and artist, and five bars from the music widget's
analyser. Click it for the music menu; right-click still has previous, play or
pause, and next.

A new track's cover dissolves into the next over 420 ms, eased, as on the Now
Playing widget, by the widget's rule: a new sleeve is a new track, or a cover
arriving for one - never a fresh copy of the same image, which the backend
decodes on every poll and which restarted the widget's fade endlessly once. A
title that lands before its cover shows the track's colour, and the cover fades
in when it comes. The outgoing cover stays solid under the incoming one: both
at half strength let the dark dock show through mid-fade, which the widget's
covers, already dimmed behind its glass, hide but the dock's would not. The
music menu shares the dock's fade, so the two change together.

The music menu is the dock's own capsule: the same squircle corners, shadow,
liquid glass and lit rim or matte fill, and the same neon when the outline is
on, all drawn by the dock's code. It is see-through like the dock, so it fades
in as it slides up above the music, and out as it sinks. Its glass is bent once
for where it comes to rest, as the dock's is, not at every step of the rise. The first
version was an opaque panel like the window previews, with Windows' smaller,
rounder corners, and it didn't match; only the previews need to be opaque. It
is compact - 264 by about 140 px at 100% - with a 48 px cover, the title,
artist, player and album beside it, and how far through the track it is - the
player reports the position at most once a second, and between readings it is
counted on locally so the bar moves smoothly. Below, one row: previous, play or
pause and next - play and pause show the new state at once, before the player
confirms it - then the speaker, to mute, and the volume slider; the mouse wheel
anywhere over the menu sets the volume too. It is Windows' own volume, the default output's, as
quick settings sets it, so it is the same number the dock's speaker shows and
the volume keys change; dragging up unmutes, as in quick settings. With
nothing playing the buttons dim and the volume still works. The menu never
takes the focus, so a game or a document keeps the keyboard; it learns of a
click outside it by watching the mouse buttons while it is open, and a click
outside, on the dock, or on the music again closes it.

**Visualiser** (Settings → Glass Dock, or right-click the dock) is Default,
the bars in the accent colour, or Neon: the bars take the neon outline's
colours, motion and strength, whether or not the outline itself is on. Still
or fading, the colours spread across the five bars once; circulating, they run
across them at the ring's pace, and a single colour is the ring's comet, a
bright head with a tail. Each bar gets the tube's near-white core and a glow
sized by the strength. The tail keeps a floor, unlike the ring's, so a bar
behind the head never vanishes while it still meters the music. Paused, the
bars keep their colours, dimmed, still and without a glow. The glow is a few
stepped layers rather than the ring's blur: at 3 px wide, a real blur 50 times
a second buys nothing you can see.

The bars meter the speakers only while a track is playing, exactly as Now
Playing does. The first version opened the capture as soon as the dock was on
and never closed it, so the bars moved to a voice call or a game with no music
at all, for 1.4% of a core all day. The dock and Now Playing share one capture:
two PortAudio instances starting together crashed the board.

## Windows' controls

The arrow opens Windows' own hidden icons right above it, with the taskbar
still hidden. Win+B gives the hidden bar's "Show hidden icons" arrow the
keyboard focus, and the dock presses Enter only once UI Automation says that is
where the focus is (with no hidden icons Win+B lands on a tray icon, and Enter
would open that app). Explorer then leaves the flyout wherever it is moved, so
it is moved above the dock's arrow. All of that was tried on a live desktop
first. The first version showed the taskbar for twelve seconds instead, and an
auto-hiding taskbar shows parked below the screen: no dock and no taskbar at
all. **Show Windows taskbar** still brings the real bar up in the dock's place
until it loses the focus. The network and speaker symbols are live, in Windows' own Segoe Fluent Icons:
Ethernet, Wi-Fi, mobile or no route; the volume level or muted. Scroll over
them to change the volume; click for quick settings. The clock (12-hour, like
the board's) opens notifications and the calendar. A battery appears when
there is one.

## The taskbar and the work area

**Replace Windows taskbar** hides the main display's taskbar while the dock is
showing. Hiding Explorer's bar is temporary: no registry or Explorer patch is
involved. A small guard process holds a pipe from the board and restores the
taskbar when the pipe closes - when the dock is turned off, the board quits, or
the board crashes. Other displays keep their own taskbars. Turning replacement
off keeps the taskbar and floats the dock just above it.

The dock works like an auto-hiding taskbar. A maximised or fullscreen window
on the main display - most people's "full screen" is the maximise button -
sends it down below the screen's edge, and that window keeps the whole height.
Rest the pointer on the bottom edge for a moment and the dock slides back up
over the window; move away and it slides down again. With nothing maximised it
simply stays up. A hidden pointer never brings it up: a game that has captured
the mouse hides the pointer, so the dock can't appear mid-match, only in menus
where the pointer shows. The edge is watched ten times a second, only while the
dock is down.

So the dock reserves no screen space. It did at first, and maximised windows
stopped 64 px short of the bottom; a dock that moves out of their way is what
an auto-hiding taskbar does instead. With replacement on and Windows' own
taskbar not set to auto-hide, Explorer still keeps its own strip, so maximised
windows stop at the top of the hidden bar. Widget coordinates do not move when
only the work area changes: toggling the dock, changing taskbar height and
restarting all keep the saved arrangement.

The taskbar is left alone while something is fullscreen. The first version
showed the hidden taskbar every second during a game with a call that also
activated it, which takes the focus away from a fullscreen game.

## How it stays out of the way

Everything native - the window list, app IDs, icons and names, the network and
the speakers - is read on a worker thread and handed to the dock as one
snapshot when something changed. Painting never calls into another process:
icon reads inside paint pumped COM messages and re-entered paint. Foreground
changes and windows opening or closing arrive as WinEvent hooks, so the dock
reacts at once instead of on the next poll. The dock itself is drawn once into
a cached image and only the bars are redrawn while music plays. That image is
straight-alpha on purpose: on a premultiplied one Qt renders text with ClearType
regardless of the font, and every letter had red and blue edges.

## Checks

`py -3.13 tools/dock_selftest.py` puts nothing on screen. It renders both
materials, both themes, both music sides, narrow to wide, playing and idle, and
checks the buttons sit inside the glass without overlapping, the text has no
colour fringes, the shell's icons keep their orientation and transparency
against Qt's own rendering, and the Start logo matches Windows' own. It checks
grouping and clicks, preference roundtrips, native reads, widget positions
through work-area changes, the shared capture, and the guard restoring a
window after its owner is killed. It uses temporary state, never the board's.

`--windows` also opens two ordinary test windows (they flash in the taskbar)
to check real apps get buttons. `--live` shows the dock, hides and restores the
real taskbar and checks the work area comes back. `py -3.13 main.py
--selftest` exercises the widgets and the app window.

The dock is for the main display on Windows 11. Drag to reorder, importing the taskbar's pins, a dock on each display and auto-hide
are not part of it yet.
