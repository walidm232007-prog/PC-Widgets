# Third-party material

What this project uses that isn't its own, and on what terms. The source code
itself is MIT — see [LICENSE](LICENSE).

## Qt, via PySide6 — LGPL v3

**Not distributed here.** There is no copy of Qt in this repository and none in
the release archive; installation is `pip install PySide6`, which fetches it from
PyPI under the user's own agreement with the Qt Company.

That matters, because the LGPL's obligations attach to *distributing* the
library. This project only distributes Python source that imports it, so no LGPL
condition is triggered and the code above can be MIT.

**If you ever freeze this into a single executable** — PyInstaller, Nuitka,
cx_Freeze — that changes: you would then be distributing Qt, and LGPL v3 applies.
At minimum you would need to ship the LGPL text, state that Qt is used and
licensed under LGPL v3, and let a user replace the Qt libraries with their own
build. Dynamic linking (the default for these tools) keeps that possible.

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
with loopback support. PortAudio — Copyright © 1999-2011 Ross Bencina and Phil
Burk, **MIT**. The 64-bit CPython 3.13 build ships unmodified in `vendor/`, with
both licence texts in `vendor/pyaudiowpatch-0.2.12.8.dist-info/licenses/`.
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
<https://github.com/pywinrt/pywinrt> — Copyright © Microsoft Corporation,
**MIT License**. The media widget uses it to read the system media session.

A copy ships in `vendor/` (about 3MB) rather than being installed from PyPI. On
at least one machine the app came up unable to import it from the user's roaming
site-packages — `No module named 'winrt'`, with the directory present, populated
and on `sys.path` — and the cause was never established. Shipping it beside the
code, ahead of the profile on `sys.path`, removed the dependency on that working.
See `vendor/README.txt` and `widgets/core/media.py::_ensure_user_site`.

MIT permits redistribution provided the notice travels with it; `vendor/README.txt`
carries the copyright and licence, and the upstream `dist-info` folders are kept
alongside the package. Deleting `vendor/` falls back to an installed copy.

## `typing_extensions`  *(redistributed, see `vendor/`)*

<https://github.com/python/typing_extensions> — **Python Software Foundation
License (PSF-2.0)**, licence text kept in its `dist-info/licenses/`. Required by
`winrt`, so it ships alongside it; vendoring one without the other fixes nothing.

Everything else that talks to Windows goes through `ctypes` against the OS itself.

## AirDrop core — `zeroconf` and `cryptography`  *(not distributed here)*

`airdrop/` runs where Apple Wireless Direct Link exists - a Mac, or Linux under
OWL (inside WSL2 on Windows) - and needs two packages there, installed with
`pip install -r airdrop/requirements.txt`. The board itself imports neither.

- **python-zeroconf** <https://github.com/python-zeroconf/python-zeroconf> —
  **LGPL-2.1**. Bonjour on the AWDL link. Used as a library, unmodified, and not
  shipped in this repository, so as with Qt no LGPL obligation is triggered. Its
  dependency `ifaddr` is MIT.
- **cryptography** <https://github.com/pyca/cryptography> — **Apache-2.0 or
  BSD-3-Clause**, at the user's choice. Makes this machine's self-signed TLS
  certificate.

**OpenDrop and OWL** (<https://github.com/seemoo-lab>, TU Darmstadt) are
**GPL-3.0**, and none of their code is here. OpenDrop was run, separately and
outside this repository, to learn how AirDrop behaves on current iOS; the core
was written from recordings of a real iPhone. OWL is the intended AWDL link on
Linux and runs as its own program, not part of this one.

**AirDrop** is a trademark of Apple Inc., used only to say what this
interoperates with. The widget will not be called AirDrop.

## Visual design

The look is modelled on Apple's iOS and macOS widgets. Copying a visual idiom is
not itself a copyright matter, but this project is not affiliated with,
authorised by, or endorsed by Apple, uses none of their trademarks or logos, and
should not be named or presented as though it were theirs.
