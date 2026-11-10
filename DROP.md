# Drop on Windows — the design

How the Drop widget will do real AirDrop on a Windows PC: which program does
what, how the board drives it, and what has to ship. Written on 2026-11-09 from
reading the source of the helper it relies on. **Nothing here has run on a PC of
ours yet, except the Bluetooth wake-up.** CLAUDE.md has the state of the work
and the next step; this file is the reference for building it.

## The shape

```
iPhone  ~~ AWDL: Wi-Fi, 5 GHz, channel 149 ~~  USB Wi-Fi stick (MT7612U)
                                                    |  USB, on Windows' WinUSB driver
                                          the helper, howldrop-agent.exe
                                          radio driver + AWDL + TCP/IP + AirDrop
                                                    |  prints events; shares three folders
                                          the board: the Drop tile
                                          Accept / Decline, progress, drag to send
                                                    |  starts and stops
                                          the wake-up: a Bluetooth LE advertisement
                                          from Windows' own Bluetooth, while sending
```

- **The helper** is HowlDrop's `howldrop-agent.exe`
  (<https://github.com/MrSco/howl-drop>, MIT, a fork of
  <https://github.com/t4t5/owly>, MIT). One process, run as the user: a driver
  for the stick in user space, AWDL, a TCP/IP stack of its own, and AirDrop's
  receiver and sender. It is used as it is, built from a pinned commit.
- **The board** starts it, reads what it prints, shows it, and answers it by
  writing small files. No AirDrop protocol code runs in the board.
- **The wake-up** is the one thing the helper leaves to its host on Windows.

## Why the helper, and not our own core

`airdrop/` speaks AirDrop over a network interface: `awdl0` on a Mac. The WSL2
plan would have given it one on Linux. The native route gives Windows none: the
helper's TCP/IP stack is inside its own process (`netstack.rs`, on smoltcp),
which is exactly what lets it run without an administrator and without
installing a network adapter. There is nothing for Python to bind a socket to.

Bridging the two - a helper that exposes the link to Python - would mean
writing a new Rust program to keep a second AirDrop implementation alive beside
the one already in the helper, for no difference a user would see. So on
Windows the helper does the conversation. `airdrop/` stays what it is good for:
the Mac tool, the record of what a real iPhone does, and a second device to
test a PC against.

One thing is lost on the way. `airdrop/` sends several files as one transfer;
the helper's outbox sends each file as its own, so three photos are three
prompts on the iPhone. The library under it takes a list, so this is a small
patch to the helper if it matters.

## The helper's contract

Read from the source at tag `windows-v0.21.2` (commit `4df0bb4`, 2026-11-07),
in `crates/howldrop-agent/src`. HowlDrop's own tray app drives the helper the
same way, so this is a tested path, not a side door.

### Starting it

```
howldrop-agent.exe --name "Sam's PC"
    --staging  DROP\staging   --deliver INBOX   --identity DROP\identity
    --receive  ask            --ask-dir DROP\asks
    --outbox   DROP\outbox    --discard
    --scan-request DROP\outbox\.scan-request
    --events   --until-stdin-closes
```

| option | what it is |
|---|---|
| `--name` | the name the iPhone's share sheet shows |
| `--staging` | required: where an upload is unpacked until it is whole |
| `--deliver` | the inbox: each finished file is moved here, then reported |
| `--identity` | the receiver's TLS identity and Bonjour name, kept between runs. Its default is a Linux path, so it must be given |
| `--receive` | who may send: `everyone`, `ask` (needs `--ask-dir`) or `contacts` |
| `--ask-timeout` | seconds an ask waits for an answer, 60 by default |
| `--outbox` | sending: a file put here is AirDropped. `--discard` deletes the copy afterwards; without it the file moves to `sent\` or `failed\` |
| `--scan-request` | a file whose appearance starts a look for nearby devices |
| `--events` | print the event lines below |
| `--until-stdin-closes` | stop when standard input ends |
| `--usb-radio` | the folder with the stick's firmware; by default `firmware` beside the exe |

The firmware is two files the chip runs, not the PC: `mt7662_rom_patch.bin` and
`mt7662.bin`. Without them the radio doesn't start.

### What it prints

Everything goes to standard output, a line at a time. A line that starts with
`@howldrop` and a tab is an event, its fields separated by tabs; the helper
strips tabs and line breaks out of every field. Any other line is a log line.

| event | fields | meaning |
|---|---|---|
| `adapter` | `mt7612u-usb`, `untested`, `user-space`, USB ID | the stick it found; the ID is empty when there is none |
| `stick` | `needs-winusb` | the stick is still on a Wi-Fi driver. The helper stops; the driver switch below is needed |
| `up` | station | the PC is on the share sheet |
| `stopped` | why | it ended on an error (exit code 1) |
| `ask` | id, sender, count, bytes, names… | someone wants to send; waiting for Accept or Decline |
| `asked` | id, `accepted` / `declined` / `timeout` | how the ask ended |
| `incoming` | id, sender, count, bytes, names… | accepted and on its way |
| `receiving` | id, bytes so far, total | progress |
| `stored` | id, names… | the upload is whole |
| `received` | path under the inbox, sender, kind | one file delivered; kind is `image`, `video`, `text`, `link` or `file` |
| `failed` | id, why | a receive broke off |
| `sending` | file name, detail | where a send has got to |
| `sent` | file name, `sent` / `failed`, detail | how a send ended |
| `nearby` | `scanning` / `done`, names… | the devices that answered a look |

Two weak points for the board to plan around. The `sending` detail is an
English sentence ("looking for the iPhone", "asking: accept it on the iPhone",
"accepted; uploading 42%"), not a code, so the board shows it or matches its
beginning. And the sender's name and the file names are whatever the other
device says they are: the board must draw them as text and never use them as a
path. The one path it does use, `received`'s, is checked first: nothing
absolute, no `..`.

### What the board writes

- **To send:** copy the file to `outbox\NAME.partial`; optionally write the
  target's name, or part of it, to `outbox\NAME.howldrop-to`; then rename the
  copy to `outbox\NAME`. The helper takes a file once its size has held for two
  looks a second apart. Without a target it picks the only device that answers,
  or the one whose name says iPhone.
- **To look for devices:** create the scan-request file.
- **To answer an ask:** create `asks\ID.accept` or `asks\ID.decline`. Check
  that ID is the hex the event gave before it goes into a file name.

A delivered file never overwrites one already there: it gets `-1` before its
extension. A shared link arrives as a `.url` shortcut that nothing opens, and
shared text as a `.txt` file in `text\` under the inbox.

### Stopping it

Closing its standard input (or Ctrl+C in a terminal) takes the radio down and
gives the stick back; the exit code is 0. A board that dies takes its end of
the pipe with it, so the helper can't be left running.

HowlDrop's tray doesn't use standard input: it hands the helper a named pipe
(`--pipe`) and disconnects it. `--until-stdin-closes` is in the same source and
is the natural fit for Python's `subprocess`, but it is the less travelled of
the two, so M2 tries it first.

The helper restarts its own link up to three times when the radio drops. After
that it exits with `stopped`, and starting it again is the board's job - with a
growing pause, as the tray does, and a visible problem state after a few tries.

### The one-time driver switch

Windows binds a new MT7612U stick to its own Wi-Fi driver, and a program can
only open a USB device that is on WinUSB. `howldrop-agent.exe --setup LOG`,
run as administrator, moves each supported stick onto WinUSB - Windows' own
`winusb.inf`, the same thing Device Manager's "Let me pick" does, with no
driver package or certificate - and then restarts the stick. This is the only
step that needs an administrator, and Windows asks for it once.

Two things a user has to be told before agreeing:

- The stick stops being a Wi-Fi adapter for Windows. It belongs to Drop until
  its driver is put back by hand (Device Manager, Update driver, pick the one
  it had). Nothing undoes it automatically.
- After the PC sleeps the stick may stop answering. Restarting it from software
  needs an administrator too, so the unprivileged helper says to unplug it and
  plug it back in.

### What was checked in the source, and what wasn't

Checked: the helper's own crates have no build scripts and no macros that run
at build time; every third-party package in `Cargo.lock` (250, for the whole
workspace) comes from crates.io, pinned by checksum, and none is an HTTP
client; on Windows the helper starts no other program, sets up no scheduled
task or autostart, and outside the driver switch changes nothing in the
registry; and it opens no socket on a network of Windows' own - its listener,
its connections and its Bonjour all run on the stack inside it, reachable over
the radio alone. The AirDrop library keeps written security rules (its
`docs/SPEC.md`, SEC-1 to SEC-11: no absolute paths, `..`, links or devices; a
staging folder; no overwriting; size limits; strict HTTP) and fuzz targets for
its parsers.

Not checked line by line: the dependencies themselves, the radio driver's
register sequences, and the parsers. `airdrop/selftest.py` has ten hostile
archives; the same cases belong in whatever test first drives the helper.

## The Bluetooth wake-up

An idle iPhone keeps its AirDrop receiver off until a nearby Bluetooth LE
advertisement says someone wants to share. Receiving on the PC needs none: the
iPhone's open share sheet wakes its own radio. Sending to the iPhone needs it,
for as long as the helper is looking for the phone.

- **What is sent:** Apple's manufacturer data (company `0x004C`) with the
  AirDrop entry: `05 12`, then 18 bytes - `40`, a random 3-byte tag, four
  zeros, `03` for the version, four zero 2-byte contact hashes, `00`. Zero
  hashes wake an iPhone in Everyone mode and no other, which is the limit M0
  found.
- **Who sends it:** Windows' own Bluetooth, through
  `BluetoothLEAdvertisementPublisher`. No pairing, and other Bluetooth devices
  stay connected.
- **When:** from the moment a file goes into the outbox, or a look for devices
  is asked for, until `sent` or `nearby done` - HowlDrop's tray holds it for up
  to 200 s a send, and drops it 3 s after the last one ends.

`tools/drop_wake.ps1` sends it for a given number of seconds, for trying a send
by hand. It ran here on 2026-11-09: Windows reported the advertisement started
in under 0.1 s and kept it up. Whether an iPhone hears it has not been tested.

In the board this becomes a helper mode of the exe, like `--winrt-read`: a
short-lived process that advertises until its input closes. It needs PyWinRT's
Bluetooth advertisement package vendored beside the ones already in `vendor/`,
at the same 3.2.1.

## What the tile shows

| state | what the user sees | how the board knows |
|---|---|---|
| not set up | what Drop needs: the stick, and why | no supported USB ID present |
| needs the driver switch | one button, and the two warnings above | `stick needs-winusb` |
| starting | a quiet "starting" | helper running, no `up` yet |
| ready | "Visible as NAME", a place to drop files | `up` |
| someone is asking | who, how many, how big; Accept and Decline, and the time left | `ask` |
| receiving | a progress bar | `incoming`, `receiving` |
| received | the file, Open and Show in folder | `received` |
| sending | the stage, then a progress bar | `sending` |
| sent, declined or failed | one line saying which, and what to do | `sent` |
| problem | the reason, and "unplug the stick and plug it back in" when that is the fix | `stopped`, or the helper gone |

While Drop is on, anybody nearby can see the PC on their share sheet and ask to
send. With `--receive ask` nothing is written to disk until Accept, and no
answer in a minute is a decline. Whether Drop is on whenever the tile is on the
board, or only for a few minutes at a time like the iPhone's own setting, is
the developer's call in M3.

Nothing received is ever opened by the board on its own.

## What the board adds

M3 starts from `main`, which has moved on from this branch's base: the tiles
are in `widgets/tiles/`, helper processes start through `widgets/core/paths.py`,
and the installed app writes to its own data folder.

- `widgets/core/drop.py` - the supervisor. Owns the helper process and a
  reader thread; turns lines into state and Qt signals; `send`, `look`,
  `answer`, `start`, `stop`; restarts with a growing pause.
- `widgets/tiles/drop.py` - the tile: the states above, drag and drop.
- a `--drop-wake` helper mode in `main.py`, beside `--guard` and
  `--winrt-read`.
- the helper and its firmware in a folder of their own beside the app,
  found through `paths`.

## Testing without a radio

A PC without the stick, and GitHub's build machines, have no radio: the helper
stops at once on them. So most of M3 has to be testable without it:

- **A stand-in helper:** a small Python script that honours the same contract -
  it prints the same event lines and uses the same three folders - and plays
  scripted stories: an ask that is accepted, one that times out, a receive that
  breaks off, a send that is declined, a stick that needs the driver switch.
  The supervisor and every state of the tile run against it in
  `main.py --selftest`, offscreen.
- **Real output as fixtures:** once M2 has run, its event lines become parser
  tests - with the names in them replaced first, since they are personal data.
- **The Mac:** `python -m airdrop find` and `send` from the Mac exercise a PC's
  receiver over the air without the iPhone. A Mac set to Everyone is a second
  real receiver for the PC's sender.

The stand-in is worth building even if the native radio fails on this PC: the
Linux build of the same helper prints the same events, so the board's side
carries over to the WSL route unchanged.

## Shipping it (M4)

- **Build it ourselves, from a pinned commit.** The release workflow checks out
  HowlDrop at the commit this file names and runs
  `cargo build --release --locked -p howldrop-agent`. GitHub's Windows machines
  have Rust and the MSVC compiler it needs (Rust 1.88 or newer; `ring` and
  OpenJPEG compile C). Their release binary is not used: an unsigned exe
  from a week-old project is not something to pass on to users unbuilt.
- **Keep a copy of the source.** HowlDrop is one person's project and changes
  weekly. A fork under the developer's own account, with the pin pointing at
  it, means a deleted or rewritten upstream can't break a release.
- **The firmware** comes from linux-firmware, one commit, checked by hash, as
  HowlDrop's own release does it: commit `fed51005f935bab093a4377d0f23f5c7ede81870`,
  `mediatek/mt7662.bin`
  (SHA-256 `f7e52492f58088cae50e51a54cca68e4abc5b74f7d0b6b731dbb4c04465a94b6`)
  and `mediatek/mt7662_rom_patch.bin`
  (SHA-256 `6f0e871268f6e4d99196d90d89bcc09fe493d010366260a2cfcaa5dd66095f8c`).
  Those hashes are HowlDrop's; nobody here has downloaded the files yet.
- **Size:** HowlDrop's own download of the helper and firmware is about 3.5 MB,
  beside a 31 MB installer.
- **Licences, for THIRD-PARTY.md:** HowlDrop and Owly (MIT); the stick driver,
  which HowlDrop's notes say is a port of Linux's mt76 (BSD-3-Clause-Clear and
  ISC) but which carries no mt76 notice of its own, so ours should credit mt76;
  the firmware (MediaTek's, redistributable as a binary under
  `LICENCE.ralink_a_mediatek_company_firmware`, which has to ship with it); and
  the Rust crates compiled in, whose notices are best collected at build time.
  The helper also embeds Apple's public root certificates, for its contacts
  check.
- **Antivirus:** HowlDrop 0.20.0 was flagged by Microsoft Defender for running
  an elevated scheduled task. 0.21 dropped the task, and nothing in this design
  runs elevated except the one driver switch. The helper will still be an
  unsigned exe, as the app's installer already is.
- **The name:** the tile is Drop. "AirDrop" is Apple's trademark and is used
  only to say what Drop works with.

## Known gaps and risks

- **It is unproven here.** Everything rests on one author's report from one PC
  with one stick. M2 is the test.
- **One chip.** On Windows only the MT7612U works, and of the sticks that have
  it HowlDrop's notes list only the Alfa AWUS036ACM as tested.
- **Channel 149 only.** AirDrop uses channel 149 on 5 GHz in some regions and
  44 in others. The helper's USB radio is fixed on 149, which is what iPhones
  use in North America. Elsewhere it may never meet an iPhone, and transmitting
  on 149 isn't allowed everywhere. HowlDrop's notes also list parts of the
  chip's power calibration as not ported yet. A release has to deal with this;
  the test doesn't.
- **Sleep.** See the driver switch above. What actually happens after a sleep
  on this PC is an M2 measurement.
- **Cost while idle.** The helper asks Windows for 1 ms timers so it can keep
  AWDL's 16 ms slots, and its radio is on the whole time. What that costs in
  CPU and power while nothing is being sent is unmeasured.
- **Speed to the iPhone.** HowlDrop reports a 14.7 MB video from an iPhone in
  about 5 s natively. The other direction was about 16 s under WSL and has not
  been measured natively.
- **One file, one prompt** (above).
- **The iPhone's limits are unchanged:** Everyone for 10 Minutes to be sent to,
  and no previews on its Accept prompt.

## Routes not taken

| route | why not |
|---|---|
| The same stick under WSL2 | It works (Owly's issue 71: the kernel WSL 2 ships has the stick's driver), but needs virtualisation in the BIOS, WSL, usbipd and admin installs. The fallback if the native radio fails. |
| Built-in Wi-Fi | No Windows Wi-Fi driver lets a program send raw frames. winawdl does it for one Realtek card with a driver signed on the machine itself, which an installer can't do for users. |
| An ESP32 board as the radio (Wayline) | GPL-3.0, and 2.4 GHz channel 6 only, where an iPhone spends little time. |
| Our own radio driver | HowlDrop's is 3,000 lines ported from Linux's. Writing another gains nothing. |
| OWL | GPL-3.0 and archived. Owly replaces it. |

## Order of work

1. **M2, when the stick arrives:** the test in CLAUDE.md. It needs the helper
   built and the firmware fetched, both by hand this once.
2. **M3, which can start before the stick:** move this work onto `main`; the
   stand-in helper; the supervisor; the tile's states; the wake-up mode. Then
   wire it to the real helper.
3. **M4:** the build in the release workflow, the firmware, the licences, the
   first-run text for the driver switch, and the README and THIRD-PARTY
   passages that still describe WSL2 and OWL.
