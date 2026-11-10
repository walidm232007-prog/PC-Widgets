# Third-party material

What this project uses that isn't its own, and on what terms. The source code
itself is MIT — see [LICENSE](LICENSE).

PC Widgets reaches people in two forms, and they pass on different things. A
**checkout** of this repository is Python source plus the fonts, the photos and
`vendor/`; Qt comes from `pip install PySide6`. The **installer** on the
Releases page is a frozen build of the same code (`packaging/` explains): it
carries all of that, and also Qt, PySide6, shiboken6, numpy and Python itself.
Every licence the installer passes on is in its program folder,
`%LOCALAPPDATA%\Programs\PC Widgets`, beside the app's own `LICENSE.txt` and a
copy of this file:

| `licenses\` | covers |
|---|---|
| `Qt\` | Qt, PySide6 and shiboken6: the LGPL v3, and the GPL v3 it builds on |
| `Python\` | the Python runtime, and what Python's Windows build includes |
| `numpy\` | numpy, and the OpenBLAS, LAPACK and GCC runtime inside it |
| `PyInstaller\` | the exe's launcher and the run-time code it adds |
| `PyAudioWPatch\` | PyAudioWPatch (Apache 2.0) and PortAudio (MIT) |
| `winrt\` | the Python/WinRT packages (MIT) |
| `typing_extensions\` | typing_extensions (PSF) |
| `Inter\` | the Inter typeface (SIL OFL 1.1) |
| `weather-photos\` | credits for the Unsplash photographs |

`packaging/pc-widgets.spec` copies them in, and `tools/build_exe.py` refuses a
build that is missing one.

## Qt, via PySide6 — LGPL v3

Qt is © The Qt Company Ltd. and other contributors, and is available under the
GNU Lesser General Public License v3, the GPL, or a commercial licence. PC
Widgets uses it under the **LGPL v3**, through Qt for Python: PySide6 for the
bindings, and shiboken6 for the layer beneath them, both on the same terms.

**From a checkout, not distributed.** There is no copy of Qt in this
repository; installing it is `pip install PySide6`, which fetches it from PyPI
under the user's own agreement with the Qt Company. The LGPL's obligations
attach to *distributing* the library, and a checkout only distributes Python
source that imports it, so nothing is triggered there.

**In the installer, redistributed under the LGPL v3.** Freezing the app into an
exe means shipping Qt with it. The installer passes on Qt 6.11.1, from the
`PySide6_Essentials 6.11.1` and `shiboken6 6.11.1` wheels, and what the LGPL
asks of that is met like this:

- **The licence goes with it.** `licenses\Qt\LGPL-3.0.txt`, with
  `licenses\Qt\GPL-3.0.txt` beside it, because the LGPL v3 is written as a set
  of extra permissions on top of the GPL v3 and relies on its definitions. The
  wheels carry neither text, so both are kept in `packaging/licenses/`.
- **It says that Qt is used, and under what.** This section, which is in the
  installed folder too.
- **Qt stays replaceable.** The build is one folder, not one file, and Qt is
  linked dynamically: each Qt library is its own DLL in `_internal\PySide6\`,
  loaded when the app starts and never merged into the exe. The next section
  says how to swap them.
- **Its source is available.** Qt 6.11.1 is at
  <https://download.qt.io/official_releases/qt/6.11/6.11.1/> — `single/` holds
  all of Qt in one archive, and the libraries in the build come from `qtbase`
  and `qtsvg` in `submodules/`. PySide6 and shiboken6 6.11.1 are at
  <https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.1-src/>.
  The build ships Qt's DLLs as they come in the wheels, unmodified; it only
  leaves some out (`packaging/README.md` says which).

The application's own code stays MIT. The LGPL allows that for a program that
uses the library through its interface rather than containing a modified copy,
and the source is public as well, so anyone can run it against a Qt of their
choosing without the installer at all.

**Raising the PySide6 version** in `packaging/requirements.txt` means updating
this section: the version above is the one whose source the links point to.
`tools/build_exe.py` checks that this file names the PySide6 and shiboken6 in
the build, and stops if it doesn't.

### Using your own Qt

Two ways, and neither needs anything from this project.

**Swap the DLLs in the installed folder.** Quit PC Widgets from its tray icon.
In `%LOCALAPPDATA%\Programs\PC Widgets\_internal\PySide6\`, replace the
`Qt6*.dll` files, and the plugins under `plugins\`, with the same files from
your own build of Qt, then start the app again: it loads yours. They have to
match what the bindings were compiled against — Qt 6.11.1, 64-bit Windows,
built with MSVC. Qt keeps its public interface binary-compatible across Qt 6,
but PySide6 also relies on some of Qt's internals, so for another 6.x version
rebuild PySide6 and shiboken6 against it from the source above, and replace
the `.pyd` files in `_internal\PySide6\` and the `_internal\shiboken6\` folder
as well. Running the installer again puts the originals back.

**Or run it from source.** Clone this repository, install PySide6 — from PyPI,
or one you built against your own Qt — and run `python main.py`.
[INSTALL.md](INSTALL.md) walks through it. The installer adds nothing a
checkout doesn't have.

## Python — PSF License  *(in the installer)*

The installer carries the Python runtime it was built with — `python313.dll`
and the parts of the standard library the app uses — under the **Python
Software Foundation License**, which asks for its notice to stay with every
copy. Python's Windows build appends the licences of what it ships with:
OpenSSL (for HTTPS), libffi (for `ctypes`), bzip2, and the conditions that come
with the Microsoft C runtime it is linked against. `licenses\Python\LICENSE.txt`
is that file, copied from the Python that built the exe.

shiboken6 and numpy bring copies of Microsoft's Visual C++ runtime as well
(`msvcp140.dll` and its relatives), as their wheels ship them.

## numpy — BSD 3-Clause  *(in the installer)*

numpy does the glass blur, the neon outline and the visualiser's spectrum.
<https://numpy.org> — Copyright © NumPy Developers, **BSD 3-Clause**. Its
Windows build carries OpenBLAS (BSD 3-Clause), with LAPACK and the GCC runtime
library (GPL v3 with the GCC Runtime Library Exception, which allows it inside
any program) built into the DLL in `numpy.libs\`. numpy's own licence file
covers all of them; `licenses\numpy\` is that file plus the licences of the
code numpy includes, copied from the installed package.

From a checkout numpy is optional, and installed by the user.

## The exe's launcher — PyInstaller  *(in the installer)*

`PC Widgets.exe` is PyInstaller's bootloader, which starts Python and hands it
the app. <https://pyinstaller.org> — **GPL v2 or later, with an exception** that
gives unlimited permission to embed the bootloader in another program and to
distribute the result without any restriction coming from it. The small
run-time hooks PyInstaller adds to the exe are under the **Apache License 2.0**.
`licenses\PyInstaller\COPYING.txt` has the terms and both texts.

## Weather photographs — Unsplash License

Seven JPEGs in `assets/weather/`, one per condition. Each was checked against
Unsplash's API to confirm it is a standard-licence photo and **not** Unsplash+
(premium) content, which is subscription-only and could not be included.

The Unsplash License grants an irrevocable, worldwide licence to download, copy,
modify, distribute and use the photos for free, including commercially, without
permission or attribution. Its one restriction — compiling photos to build a
competing stock-photography service — does not apply here. Credited anyway:

| file | condition | photographer |
|---|---|---|
| `clear.jpg` | clear sky | [Grooveland Designs](https://unsplash.com/photos/zjoydJb17mE) |
| `partly.jpg` | partly cloudy | [Nick Fewings](https://unsplash.com/photos/xbYbeaxLWHI) |
| `cloud.jpg` | overcast | [Vita Leonis](https://unsplash.com/photos/ZfRMc1_RUuA) |
| `rain.jpg` | rain | [Amadeus Moga](https://unsplash.com/photos/NgqViwevvzs) |
| `storm.jpg` | thunderstorm | [Felix Mittermeier](https://unsplash.com/photos/WLGHjbC0Cq4) |
| `snow.jpg` | snow | [Taylor Heery](https://unsplash.com/photos/d3aMKpVIjr8) |
| `fog.jpg` | fog | [Timothy Abraham](https://unsplash.com/photos/w6F1zapeREw) |

## Weather data — Open-Meteo

Forecasts and geocoding from [open-meteo.com](https://open-meteo.com), used
without an API key. Free for non-commercial use; the data is published under
**CC BY 4.0**, so the attribution above and in the README is the condition being
met. Requests go directly from each user's own machine, so there is no shared
quota to exhaust.

## Market data — Yahoo Finance ⚠️

The stocks widget reads `query1.finance.yahoo.com`, which is an **internal,
undocumented endpoint**, not a public API. Yahoo's terms of service prohibit
automated access to and redistribution of their market data, and they have acted
against libraries that rely on this endpoint.

This is a terms-of-service question rather than a copyright one, and the requests
are made by each user from their own machine — but it is the one component here
that is not on clearly permitted footing, and it can be blocked or broken at
Yahoo's discretion at any time.

If that matters for how you use or redistribute this, replace the source in
`widgets/tiles/stocks.py`. Feeds with terms that permit this kind of use include
Stooq's CSV endpoint, and Finnhub or Twelve Data with a user-supplied free key.

## Inter typeface — SIL Open Font License 1.1

The board is set in [Inter](https://rsms.me/inter/), by Rasmus Andersson and the
Inter Project Authors. Its two variable files, `fonts/InterVariable.ttf` and
`fonts/InterVariable-Italic.ttf`, ship unmodified from the official 4.1 release,
with the licence beside them in `fonts/LICENSE-Inter.txt`.

The OFL allows the fonts to be bundled with software, redistributed and used
commercially, as long as the licence goes with them and the fonts aren't sold on
their own. They are loaded per-process with `addApplicationFont`, so nothing is
ever installed system-wide.

**Apple's SF Pro is not used.** The board was first designed around it, but
Apple's licence covers designing user interfaces for Apple platforms and does not
permit redistributing the files, so Inter replaced it. `.gitignore` still keeps
any stray copy out of the repository, and `tools/prepublish.py` refuses to
publish one.

## Audio capture — PyAudioWPatch  *(redistributed, see `vendor/`)*

The media widget's visualiser reads the sound leaving the machine so the bars
show the real spectrum. That needs WASAPI loopback, which plain PortAudio (and
therefore `sounddevice`) does not expose on Windows.

PyAudioWPatch 0.2.12.8 <https://github.com/s0d3s/PyAudioWPatch> — Copyright ©
2022 S0D3S, **Apache License 2.0**, a fork of PyAudio bundling a PortAudio build
with loopback support. PyAudio — Copyright © 2006 Hubert Pham, **MIT**, its
notice kept at the top of `vendor/pyaudiowpatch/__init__.py`. PortAudio —
Copyright © 1999-2011 Ross Bencina and Phil Burk, **MIT**. The 64-bit CPython
3.13 build ships unmodified in `vendor/`, with the Apache and PortAudio licence
texts in `vendor/pyaudiowpatch-0.2.12.8.dist-info/licenses/`.
Installed in the user's site-packages it was invisible to the board on the
machine this was built for, so the visualiser silently ran on the fallback
below; see `vendor/README.txt`.

**Still optional.** Delete it and the widget falls back to Windows' Core Audio
peak meter through `ctypes` — no dependency at all, real output level but no
frequency information, so the bars move together — and failing that, to an
animation.

Nothing is recorded. Blocks of samples are analysed and discarded about forty
times a second; nothing is written to disk and nothing leaves the process. It
reads the *output* mix, never a microphone. Right-click the widget → **Audio
visualiser** turns it off, and the choice persists.

## Windows APIs — `winrt-*` packages  *(redistributed, see `vendor/`)*

Python/WinRT, the Python projection of the Windows Runtime.
<https://github.com/pywinrt/pywinrt> — Copyright © Microsoft Corporation and
© 2021-2025 David Lechner, **MIT License**. The media widget uses it to read the system media session.

A copy ships in `vendor/` (about 3MB) rather than being installed from PyPI. On
at least one machine the app came up unable to import it from the user's roaming
site-packages — `No module named 'winrt'`, with the directory present, populated
and on `sys.path` — and the cause was never established. Shipping it beside the
code, ahead of the profile on `sys.path`, removed the dependency on that working.
See `vendor/README.txt` and `widgets/core/media.py::_ensure_user_site`.

MIT permits redistribution provided the notice travels with it. The upstream
wheels carry no licence file, so the text is kept in `vendor/LICENSE-winrt.txt`,
taken from pywinrt's v3.2.1 tag to match the version vendored; the upstream
`dist-info` folders are kept alongside the package as they came. Deleting
`vendor/` falls back to an installed copy.

## `typing_extensions`  *(redistributed, see `vendor/`)*

<https://github.com/python/typing_extensions> — **Python Software Foundation
License (PSF-2.0)**, licence text kept in its `dist-info/licenses/`. Required by
`winrt`, so it ships alongside it; vendoring one without the other fixes nothing.

Everything else that talks to Windows goes through `ctypes` against the OS itself.

## Visual design

The look is modelled on Apple's iOS and macOS widgets. Copying a visual idiom is
not itself a copyright matter, but this project is not affiliated with,
authorised by, or endorsed by Apple, uses none of their trademarks or logos, and
should not be named or presented as though it were theirs.
