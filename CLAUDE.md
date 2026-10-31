# PC Widgets — notes for Claude

Read this first in any session here. It covers how this project is worked on and
where the current work stands. The README is the design record; this file is
the handoff.

## How to work on this project

- **Fetch and pull before anything else**, and read any new commits. Work
  happens on two machines, a Mac and this Windows PC, and either may have
  pushed. Do it again before every milestone and every commit.
- **Plan → approval → milestone.** Propose a plan and wait for a yes. Build one
  milestone at a time, check it really works (run it, not just tests), and
  commit at the end of each one. Push only when asked.
- **Commits:** a subject like `Area: what changed, in plain words`, then a prose
  body. Work goes on a branch, never straight on `main`.
- **The repo is public.** No personal names, emails, phone or device names, or
  absolute user paths in code, docs or commits. Use placeholders like
  "Sam’s iPhone". `python tools/prepublish.py` must pass before a push.
- The board needs only PySide6. `python main.py --selftest` renders every
  widget offscreen. Match the README's voice: explain *why*, with the evidence.

## Current work: real AirDrop (branch `airdrop-core`)

**The goal:** the Drop widget sends files to an iPhone, iPad or Mac and
receives them, through **real AirDrop**, not a lookalike, with nothing installed
on the Apple side. The developer asked for real AirDrop only: no LAN fallback.

| milestone | state |
|---|---|
| M0 – does AirDrop still work with current iOS? | ✅ done on a Mac, with an iPhone 15 Pro on iOS 27 |
| M1 – our own protocol core, `airdrop/` | ✅ done, commit `6d074d6`: sent 2 images, received 1 |
| **M2 – the Windows radio (the kill risk)** | **next, on this PC - blocked on a USB Wi-Fi adapter, SVM in the BIOS and WSL2 (see "This PC")** |
| M3 – the Drop widget (drag to send, Accept/Decline, progress) | not started |
| M4 – packaging (WSL helper setup, first-run checks) | not started |

`airdrop/` is plain Python 3.9+ with no Qt. It needs `pip install -r
airdrop/requirements.txt` (zeroconf, cryptography). Start with
`python -m airdrop.selftest`: 20 checks with no network or phone, including a
whole transfer over loopback TLS. It should run on Windows too, but that is
untested. The module docstrings explain the protocol.

## Open decision: will users need a USB Wi-Fi adapter?

**Undecided - the developer wants to come back to this before release.** The
developer doesn't want users to have to buy a USB Wi-Fi dongle. For real AirDrop
on Windows, almost every user would have to:

- **Windows can't do it.** Windows Wi-Fi drivers don't let apps send raw,
  hand-made frames or hop channels on Apple's schedule, which AWDL needs. That
  holds for every brand of card.
- **WSL2 can't reach a built-in card.** It can only be handed USB devices
  (through usbipd), and built-in Wi-Fi, in laptops and desktops alike, plugs
  into the motherboard over PCIe. There is no PCIe passthrough for WSL2 on
  client Windows.
- **Not any dongle either:** it has to be a chip with good in-kernel Linux
  support for monitor mode and frame injection (see M2 step 1).
- **More setup on top:** installing WSL2 (admin and a restart), enabling
  virtualisation in the BIOS on some PCs, handing the adapter to WSL each time,
  and Bluetooth to wake an iPhone before sending to it. That is a lot for a
  widgets app.

The options on the table:

1. **Real AirDrop as an opt-in for enthusiasts:** a list of supported dongles
   and a one-time setup guide. Genuine AirDrop with nothing on the iPhone, but
   few users would set it up.
2. **Something that works for everyone, with no hardware:** the PC and the
   iPhone talk over the home Wi-Fi network - for example the PC shows a QR code
   and the iPhone sends or receives through Safari or a Shortcut, no app to
   install. It won't appear in the iPhone's AirDrop list. This means relaxing
   the "real AirDrop only, no LAN fallback" rule above.
3. **Both:** the home-network route for everyone, real AirDrop on top for
   anyone with a supported dongle.

Until this is decided, M2 is still worth doing on this PC: it answers whether
real AirDrop works at all, which options 1 and 3 depend on.

## Next: M2 — can this PC speak AWDL?

AirDrop runs over **AWDL** (Apple Wireless Direct Link), a direct Wi-Fi link
between devices. The router and Ethernet play no part. Windows can't do AWDL
itself, so the plan is: a USB Wi-Fi adapter handed to **WSL2** with
**usbipd-win**, running **OWL** (github.com/seemoo-lab/owl, an open AWDL
implementation for Linux, archived in Oct 2026), which creates `awdl0`. The
core then runs inside WSL. The PC is on Ethernet, so the adapter can be given
over to AWDL entirely.

**This PC, as checked from Windows (2026-10-30; Wi-Fi re-checked 2026-10-31).**
All of these are the developer's to change - hardware, BIOS, admin installs and
restarts:

- **No USB Wi-Fi adapter is plugged in.** The USB devices are a webcam, an audio
  interface, a mouse, a headset, a keyboard, a serial device and the built-in
  Bluetooth.
- The built-in Wi-Fi is an **Intel Dual Band Wireless-AC 3168 on PCIe**. It
  works (driver loaded, radio on, it hears networks on 2.4 and 5 GHz), but being
  PCIe it can't go to WSL. Its Bluetooth half is a USB device (8087:0AA7,
  Bluetooth 4.2), so usbipd *could* hand that to WSL.
- Reception is weak - the strongest network at about 45% - which fits the
  board's Wi-Fi/Bluetooth antenna, a separate piece that screws onto the rear
  I/O, not being attached. The same antenna serves the Bluetooth.
- **WSL and usbipd-win are not installed**, and **SVM Mode (virtualisation) is
  off in the BIOS**, so WSL2 can't run yet.
- The selftest has never run off the Mac: this PC's Python has no zeroconf, and
  importing the client fails on it.

1. **Identify the adapter's chip.** None is plugged in yet (above). Once one is,
   in PowerShell:
   `Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like "USB*" -and $_.FriendlyName -match "Wi-?Fi|Wireless|WLAN|802\.11" } | Select-Object FriendlyName, InstanceId`.
   `VID_xxxx&PID_xxxx` names the chip. It needs to be USB, dual-band (AWDL uses
   5 GHz channel 149 as well as 2.4 GHz), and supported on Linux with **monitor
   mode and frame injection**, by a driver that is part of the kernel. MediaTek
   MT7612U, MT7610U and MT7921AU are good bets; Realtek is hit and miss; old
   Ralink parts are usually 2.4 GHz only. One OWL user's MT7612U still wouldn't
   enter monitor mode, so even a good chip has to be tested.
2. **WSL2 + usbipd:** attach the adapter to WSL. WSL's stock kernel likely lacks
   Wi-Fi drivers, so expect to build a custom WSL kernel with the adapter's
   driver (mt76 for MediaTek). Prove monitor mode and injection with `iw`.
3. **OWL:** build it, run it on the adapter, and confirm `awdl0` appears.
4. **The core inside WSL:** `python -m airdrop find`, then `receive`, with the
   iPhone. Receiving likely needs no Bluetooth from the PC, because a sending
   iPhone sends its own wake-up signal. Sending does need the wake-up (below).

**The Bluetooth wake-up.** An idle iPhone keeps its AirDrop receiver off until
a nearby sender's Bluetooth LE advertisement says someone wants to share. On a
Mac, Finder's AirDrop window sends it (`link.wake_nearby()`); a PC must send it
itself. **This PC's built-in Bluetooth is broken, with a very weak signal.** In
order of cost, the options are:

- screw the board's Wi-Fi/Bluetooth antenna onto the rear I/O (likely the whole
  problem, see "This PC" above);
- try it anyway with the phone right next to the PC;
- hand the built-in Bluetooth (USB 8087:0AA7) to WSL, where BlueZ can send the
  advertisement - `link.wake_nearby()` only does it the Mac's way today;
- see whether the iPhone's own share sheet keeps its receiver awake (untested);
- a USB Bluetooth 5 dongle passed to WSL, where Linux's Bluetooth stack (BlueZ)
  can send Apple's advertisement whatever Windows allows.

Two quick Bluetooth checks are still owed, and they run on the Mac with the
iPhone, with Finder's AirDrop window *closed*:
- Can the iPhone still send to our receiver?
- Can our sender reach the iPhone while the iPhone's AirDrop share sheet is open?

## What M0 established

- The iPhone has to be in **Everyone for 10 Minutes**, re-enabled each time,
  with its screen on. Contacts Only needs Apple-signed records, which is out of
  reach.
- **Send the moment discovery answers.** An iPhone closes port 8770 a few
  seconds after the last request, and the client does this already.
- iOS 27 rejects OpenDrop's legacy `application/x-cpio` upload, reading it to
  the end and then hanging up without a reply. The working upload is
  `application/x-dvzip` (a cpio archive in 128 KiB blocks, each zlib'd or stored
  raw with the length's top bit set), with `TransferID` and `TotalBytes`
  headers. The Ask carries `TransferID`, `TransferType`, `Items` and each file's
  `FileSize`. All of this is implemented in `airdrop/`.
- iPhones send `._name` AppleDouble metadata files beside some photos. The
  receiver checks and drops them; refusing them made a transfer fail.
- Transfers between devices on the same Apple ID use a different path (QUIC via
  `_asquic._udp`). A PC can't use it and doesn't need to.
- **OpenDrop and OWL are GPL-3.0.** None of their code may go in this MIT repo.
  OWL runs as a separate program. Recordings of real transfers stay on the Mac,
  outside the repo, because they hold personal data.
