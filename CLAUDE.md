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
| **M2 – the Windows radio (the kill risk)** | **next, on this PC - waiting on one USB Wi-Fi stick. The route changed on 2026-11-09: no WSL (see "Next: M2")** |
| M3 – the Drop widget (drag to send, Accept/Decline, progress) | not started; most of it can be built before the stick arrives ([DROP.md](DROP.md)) |
| M4 – packaging (the helper, the stick's firmware, first-run checks) | not started |

`airdrop/` is plain Python 3.9+ with no Qt. It needs `pip install -r
airdrop/requirements.txt` (zeroconf, cryptography). Start with
`python -m airdrop.selftest`: 20 checks with no network or phone, including a
whole transfer over loopback TLS. It should run on Windows too, but that is
untested. The module docstrings explain the protocol. On Windows it is no
longer the part that talks to the iPhone (see M2), so its jobs now are the Mac
tool, the record of what a real iPhone does, and a second device to test a PC
against.

## Open decision: will users need a USB Wi-Fi adapter?

**Undecided - the developer wants to come back to this before release.** The
developer doesn't want users to have to buy a USB Wi-Fi dongle. Real AirDrop on
Windows still needs one, though far less comes with it than when this was first
written:

- **Built-in Wi-Fi can't do it.** Windows Wi-Fi drivers don't let apps send
  raw, hand-made frames or hop channels on Apple's schedule, which AWDL needs.
  That holds for every brand of card. The one project that gets round it
  (winawdl) runs a single Realtek card through a driver signed on the machine
  itself, which an installer for ordinary users can't do.
- **One kind of stick can:** a USB adapter with the MediaTek MT7612U chip, run
  from user space by a helper program (M2). No WSL, no virtualisation switch in
  the BIOS, no usbipd, nothing installed and nothing left running as
  administrator.
- **What a user would do:** buy that stick, plug it in, and approve one Windows
  prompt that moves it onto Windows' own WinUSB driver. From then on the stick
  belongs to Drop: Windows can't use it for Wi-Fi until its driver is put back
  by hand in Device Manager.
- **Sending to an iPhone also needs Bluetooth on the PC**, for the wake-up.
  Receiving needs none.

The options on the table:

1. **Real AirDrop as an opt-in:** a short list of supported sticks and a setup
   that is one prompt. Genuine AirDrop with nothing on the iPhone, for the
   users who buy the stick.
2. **Something that works for everyone, with no hardware:** the PC and the
   iPhone talk over the home Wi-Fi network - for example the PC shows a QR code
   and the iPhone sends or receives through Safari or a Shortcut, no app to
   install. It won't appear in the iPhone's AirDrop list. This means relaxing
   the "real AirDrop only, no LAN fallback" rule above.
3. **Both:** the home-network route for everyone, real AirDrop on top for
   anyone with a supported stick.

Until this is decided, M2 is still worth doing on this PC: it answers whether
real AirDrop works at all, which options 1 and 3 depend on.

## Next: M2 — real AirDrop on this PC, with a USB stick and no WSL

AirDrop runs over **AWDL** (Apple Wireless Direct Link), a direct Wi-Fi link
between devices. The router and Ethernet play no part, and Windows can't do
AWDL itself. Until 2026-11-09 the plan was a USB adapter handed to WSL2 and run
by OWL. **That plan is dropped**, because two MIT projects now do the job
without Linux:

- **Owly** (github.com/t4t5/owly): AWDL and AirDrop as Rust libraries. It takes
  the place of OWL, which is GPL-3.0 and archived.
- **HowlDrop** (github.com/MrSco/howl-drop, a fork of Owly): adds a driver for
  the MT7612U that runs in user space over WinUSB, and one helper program,
  `howldrop-agent.exe`, that is the radio, AWDL, a TCP/IP stack of its own and
  AirDrop's receiver and sender. It has run natively on Windows since 0.20.0
  (2026-11-02), and without an administrator since 0.21.0.

Its author reports, from their own PC with an Alfa AWUS036ACM: a 14.7 MB video
from an iPhone in about 5 s, a photo in about 1 s, and a photo sent to the
iPhone. **None of it has run on this PC.** It is one person's project and its
native Windows part is a week old. M2 is finding out whether it holds here.

What follows from it:

- **`airdrop/` has nothing to run on here.** The helper keeps its network
  stack inside its own process, so Windows gets no AWDL network interface for
  Python to open a socket on. This was confirmed in the helper's source. On
  Windows the helper does the AirDrop conversation and the board drives it.
- **The board drives the helper** through what it prints (event lines) and
  three folders (outbox, asks, inbox). [DROP.md](DROP.md) has that contract as
  read from the source, the design for M3 and M4, the licences and the risks.
  **DROP.md is a proposal: the developer has not approved it yet.**
- **It can ship inside this app:** MIT code, and firmware for the stick that
  may be redistributed as a binary. Neither OWL nor WSL is needed.

**This PC, as checked from Windows (2026-11-09).** Hardware, Windows prompts
and restarts are the developer's to do:

- **No USB Wi-Fi stick yet.** It has to have the MT7612U chip. The Alfa
  AWUS036ACM (`0e8d:7612`) is the one HowlDrop was tested with. A Netgear A6210
  (`0846:9053`) and an ASUS USB-AC55 (`0b05:17eb`) have the same chip and are
  untested. A product's name doesn't fix its chip, so check the USB ID. The
  TP-Link Archer T2U Plus (`2357:0120`, an RTL8821AU) does not work. HowlDrop's
  notes say a USB 2.0 port worked best.
- The built-in Wi-Fi, an Intel Dual Band Wireless-AC 3168 on PCIe, is no use
  for this: no Windows route drives a built-in card, and under Linux Intel's
  cards of that generation (7265, 8260, 8265) crash on AWDL.
- **Windows' Bluetooth will send the wake-up.** `tools/drop_wake.ps1` ran here:
  Windows started the advertisement at once and kept it up. Whether an iPhone
  hears it is untested, and reception is weak - the strongest Wi-Fi network at
  about 45% - which fits the board's Wi-Fi/Bluetooth antenna, a separate piece
  that screws onto the rear I/O, not being attached. The same antenna serves
  the Bluetooth.
- **The helper has not been built here.** Its source was read at tag
  `windows-v0.21.2` (commit `4df0bb4`). This PC has Rust 1.89, and MSVC 14.29
  with a Windows SDK that was set up without an installer. Whether that builds
  it is not known yet.
- Virtualisation is off in the BIOS and WSL isn't installed. Neither matters
  any more.
- The selftest has never run off the Mac: this PC's Python has no zeroconf, and
  importing the client fails on it.

**The M2 test, once the stick is here.** The developer runs the build, the
helper and the Windows prompt: a Claude session on this PC was stopped from
building third-party code, so a session prepares the commands and reads what
comes out.

1. **Build the helper.** In a checkout of HowlDrop at the commit above:
   `cargo build --release --locked -p howldrop-agent`. It needs Rust 1.88 or
   newer and the MSVC C compiler. Then put the stick's two firmware files,
   `mt7662.bin` and `mt7662_rom_patch.bin`, in a folder named `firmware` beside
   `howldrop-agent.exe`; DROP.md says where they come from and gives their
   hashes.
2. **Plug the stick in** and look at it:
   `Get-PnpDevice -PresentOnly | Where-Object InstanceId -match 'VID_0E8D&PID_7612' | Select-Object FriendlyName, InstanceId, Service`.
   `Service` is the driver that has it.
3. **Start the helper** in a terminal, in an empty folder:
   `howldrop-agent.exe --name "Drop test" --staging drop\staging --deliver drop\inbox --identity drop\identity --receive everyone --events`.
   If it prints `stick needs-winusb` and stops, the stick is still on its Wi-Fi
   driver: run `howldrop-agent.exe --setup` once in an administrator terminal,
   then start it again. `up` means the PC is on the share sheet. Ctrl+C stops
   it.
4. **Receive.** Send a photo from the iPhone with the PC's Bluetooth turned
   off, which also shows that receiving needs none. Then start the helper with
   `--receive ask --ask-dir drop\asks` and answer one ask each way, by making
   the empty file `drop\asks\ID.accept` or `drop\asks\ID.decline`.
5. **Send.** Add `--outbox drop\outbox --discard`. Put the iPhone on Everyone
   for 10 Minutes, run `tools\drop_wake.ps1` in a second terminal, and copy a
   photo into `drop\outbox`. Then once more without the wake-up but with the
   iPhone's own share sheet open, to learn whether that alone is enough.
6. **Write down** how long a photo and a video of about 15 MB take each way,
   what the helper costs in CPU while nothing is happening, and what happens
   after the PC sleeps, when the stick is unplugged, and when the helper is
   started with `--until-stdin-closes` and its input is closed.
7. **From the Mac,** with Finder's AirDrop window open: see whether
   `python -m airdrop find` lists the PC and `python -m airdrop send` reaches
   it. Nobody has tried `airdrop/` against the helper.

If the PC never shows on the iPhone's share sheet, this route has failed here.
What is left then is the same stick under WSL2, which Owly's issue 71 shows
working with the kernel WSL 2 ships. It brings back the BIOS switch and the
admin installs, and the board's side would not change: the Linux build of the
helper prints the same events.

**The Bluetooth wake-up.** An idle iPhone keeps its AirDrop receiver off until
a nearby sender's Bluetooth LE advertisement says someone wants to share. On a
Mac, Finder's AirDrop window sends it (`link.wake_nearby()`). On Windows the
helper leaves it to whoever runs it, and Windows' own Bluetooth can send it
with nothing installed: `tools/drop_wake.ps1` holds it for a number of seconds,
and DROP.md has what is in it. If the iPhone doesn't hear it from this PC, in
order of cost:

- screw the board's Wi-Fi/Bluetooth antenna onto the rear I/O (the likely
  cause of the weak signal, see "This PC");
- hold the phone right next to the PC;
- a USB Bluetooth dongle.

**Out of date until M2 passes:** the README's AirDrop section and the AirDrop
part of THIRD-PARTY.md still describe WSL2 and OWL. They state what has been
shown to work, so they change when this route has been.

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
- **OpenDrop and OWL are GPL-3.0.** None of their code may go in this MIT repo,
  and neither is part of the plan any more. Owly and HowlDrop are MIT; DROP.md
  lists what shipping them takes. Recordings of real transfers stay on the Mac,
  outside the repo, because they hold personal data. The same goes for what the
  helper prints: it names devices and files.
