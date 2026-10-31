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

Icons are the shell's own artwork for the app - what Start and the taskbar
show, Store apps included - rendered at the size needed. Without an app ID the
window's own icon is used, then the program's. An app with no icon at all gets
its initial on a colour of its own. The shell's bitmaps are converted by hand:
`QImage.fromHBITMAP` dropped their transparency and turned them upside down.

Click an app to bring it forward, or to minimise it when it is already in
front; with several windows each click brings the next. Middle-click opens a
new window. Right-click lists its windows, and offers New window, Pin/Unpin
and Close. Pins can be Start apps, programs, shortcuts or links (**Pin an
app…**); the dock's pins are its own, and the taskbar's are not changed.

## Music

The music section shows Spotify or Apple Music through the same backend as Now
Playing: the cover, title and artist, and five bars from the music widget's
analyser. Hover shows play or pause over the cover; click toggles it;
right-click has previous and next.

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

Either way maximised windows stop at the dock's top. The dock reserves only
what Explorer does not already keep (with an auto-hiding taskbar, that is all
of it), through a hidden window of its own, and Windows drops the reservation
with that window. Widget coordinates do not move when only the work area
changes: toggling the dock, changing taskbar height and restarting all keep the
saved arrangement.

A fullscreen app on the main display - a game, a video - hides the dock, and
the taskbar is left alone. The first version showed the hidden taskbar every
second during a game with a call that also activated it, which takes the focus
away from a fullscreen game.

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

The dock is for the main display on Windows 11. Taskbar thumbnails, drag to
reorder, importing the taskbar's pins, a dock on each display and auto-hide
are not part of it yet.
