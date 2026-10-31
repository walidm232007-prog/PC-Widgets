# Glass Dock

Turn on **Settings → Glass Dock → Floating dock**. A rounded dock sits above
the bottom edge of the main display, with apps in the middle, music on either
side, and Windows controls at the right. It starts off, so updating PC Widgets
doesn't change an existing desktop.

The compact dock is 48 px tall inside a 64 px window (including the floating
gap and shadow), scaled with the display. Its width grows from 720 to 980 px
as apps are opened. Normal and minimised windows stay in the app list, including
the PC Widgets app itself; app icons come from the windows when available.

The material is the same blurred wallpaper the widgets use, with the board's
glass strength. Solid colour skips that layer. Choose a colour for either
material, or right-click the dock and choose **Use theme colour** to follow
light and dark mode again. Music position is saved independently.

Start opens Windows Start. Clicking a running app brings it forward; clicking
the active app minimises it. Right-click an app to pin it, or use **Pin an app…**
for an executable or shortcut. An overflow menu keeps all apps reachable when
there isn't enough room. Pins belong to the dock; existing Windows taskbar pins
are not imported or changed.

The music section shows Spotify or Apple Music through the same backend as
Now Playing. Click to play or pause; right-click for previous and next. The
five bars use the music widget's audio analyser and peak-meter fallback. When
capture isn't available they stay still rather than inventing sound. Track
reads happen on a worker; audio frames repaint only the music section, over a
cached backdrop.

The sound/network button opens Windows quick settings, and the clock opens
notifications and the calendar. The arrow reveals the real Windows taskbar
for twelve seconds, including its third-party tray icons. This first milestone
does not recreate the notification area or its app-specific menus. The small
network and speaker symbols are shortcuts, not live connection or mute meters.
Battery percentage appears on wider layouts when a battery is present.

**Replace Windows taskbar** hides the primary taskbar while the dock is active.
The dock reserves the extra space it needs so maximised windows stop above it.
That changes the work area without changing widget coordinates: toggling the
dock, changing taskbar height and restarting all preserve the saved arrangement.
Other displays retain their native taskbars. Turning replacement off keeps
Windows' taskbar and floats the dock above it. Fullscreen apps on the main
display hide the dock. Disable the dock or quit PC Widgets to restore Windows.

Hiding Explorer is temporary: no registry or Explorer patch is involved. A
small guard process holds a pipe from the board and restores the taskbar when
the pipe closes, including when the board crashes. The desktop-space reservation
belongs to a window, and Windows removes it when the process ends.

The dock and Now Playing share one audio capture instance. Starting two capture
instances at once could crash inside PortAudio's device discovery, before the
dock had a chance to show its app buttons.

## Checks

`py -3.13 tools/dock_selftest.py` renders both materials, both themes, both music
positions and three widths. It checks hit areas, preference roundtrips, old
layout defaults, native app enumeration and restoration after a test process
is forcibly terminated. It uses temporary state, never the user's board.

`py -3.13 tools/dock_selftest.py --live` also briefly shows the dock, checks the
native taskbar and desktop work area, then tests disable, re-enable and close.
`py -3.13 main.py --selftest` exercises the existing widgets and app window.

The initial version targets the primary display and Windows 11. Native taskbar
thumbnail previews, drag reordering, automatic import of Windows pins, and a
dock on each display are not part of this milestone. App buttons represent
windows, rather than grouping every window of a process into one button.
