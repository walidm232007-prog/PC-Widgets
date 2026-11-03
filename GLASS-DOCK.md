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

Frosted glass is the board's blurred wallpaper under a tint, with a sheen on
its upper half and a hairline lit along the top edge. Solid colour is matte:
one flat colour and an even hairline. It kept the sheen and the lit edge at
first, and still read as glass.

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
nothing), so the panel is opaque: the board's frosted wallpaper edge to edge,
with Windows 11's own rounded corners, border and shadow. Apps with no window
open keep their name as a tooltip.

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

The music menu rises above it like the window previews, and folds away the same
way, with the neon round it when the outline is on. It shows the cover, the
title, artist, player and album, and how far through the track it is - the
player reports the position at most once a second, and between readings it is
counted on locally so the bar moves smoothly. Below are previous, play or pause
and next; play and pause show the new state at once, before the player
confirms it. Then the volume: a slider, the mouse wheel anywhere over the menu,
and the speaker to mute. It is Windows' own volume, the default output's, as
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

The dock is for the main display on Windows 11. Drag to reorder and a dock on
each display are not part of it yet.
