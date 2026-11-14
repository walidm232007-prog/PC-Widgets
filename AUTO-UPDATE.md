# Automatic updates

How an installed PC Widgets keeps itself up to date, and the plan for
building it. Agreed with the developer on 2026-11-14, to land before 1.0.3.

| milestone | state |
|---|---|
| M1 – find, trust and download an update | done |
| M2 – install it: a silent upgrade that reopens the app, tested end to end | next |
| M3 – Settings › App and the tray: the switch, Check now, the status, one tip | not started |
| M4 – release: the signing key, the workflow signs and attaches the update file, a CI dry run | not started |

## Why

The installer already upgrades in place: its AppId never changes, so running
a newer `PC-Widgets-Setup` over an older copy replaces it, and the widgets and
settings (in `%APPDATA%\PCWidgets`) stay. What was missing is the app noticing
a new release and doing that by itself, so nobody has to go looking.

## The design

1. **Check.** Once a day the installed app asks GitHub for `update.json` on the
   latest release (one small HTTPS request; nothing about the PC is sent). A
   copy run from a checkout never does.
2. **Trust.** `update.json` holds the new version, the installer's address,
   size and SHA-256, and a signature over them. The app believes none of it
   until the signature checks out against the public key built into it. The
   private half lives only in the repository's secrets, where the release
   workflow signs with it. The installer isn't code-signed, so this is what
   keeps a tampered release from installing itself everywhere.
3. **Download.** In the background, into the app's own `updates` folder,
   through the same HTTPS-only connection as every other fetch; then the
   length and SHA-256 must match.
4. **Install.** The next time PC Widgets starts (at sign-in or when it's
   opened), or at once from "Restart to update": the app runs the installer
   silently and quits, and the installer upgrades in place - per user, no
   admin prompt, settings kept - and reopens PC Widgets. A failed install
   leaves the old version running and isn't retried in a loop.
5. **Settings › App.** An "Automatic updates" switch (on), "Check now", and a
   status line; one tray tip when an update is ready.

Copies of 1.0.2 have no updater, so they move to 1.0.3 by running its
installer once, over the top; from 1.0.3 on, updates come by themselves.

## What M1 laid down

- `widgets/core/signature.py`: ECDSA P-256 through Windows' own cryptography
  (CNG, `bcrypt.dll`, by ctypes), so checking a signature needs no package.
  Keys are CNG's own blobs in base64 (72 bytes public, 104 private);
  signatures are 64 bytes, r then s, over the SHA-256 of the signed bytes.
  `verify` never raises for bad input: anything malformed is just no match.
- `widgets/core/updater.py`:
  - `read_update` believes an update file only after its signature checks
    out, and then only if it names a version, this repository's own installer
    address for exactly that version, a size between 1 MB and 300 MB, and a
    SHA-256. A signed file pointing anywhere else is still refused.
  - `canonical` is the exact bytes a manifest is signed as (sorted keys, no
    spaces, ASCII), so the file's layout doesn't matter.
  - `Updater` checks on a worker thread and reports `status` (off, idle,
    checking, downloading, up_to_date, ready, offline, failed) through
    `changed`. It keeps its state in `updates/state.json`: when it last
    checked (a day between checks, three hours after a failure) and the
    installer it has ready. An installer already downloaded and still
    matching is reused; one that changed on disk is dropped; a download that
    doesn't match the release is deleted.
  - `PUBLIC_KEY` is empty until the release key exists (M4); with no key, no
    update is trusted, so no build can install anything unsigned.
- `net.download` and `net.save_stream`: an installer streamed to disk, hashed
  as it comes, into a `.part` file that becomes the real one only when the
  length promised has arrived and no more - so a cut-off download never looks
  finished. `net.fetch` and `net.download` share one opener (HTTPS only,
  Windows' roots, redirects only to HTTPS).
- Selftest: `_check_updater`, with GitHub canned - no network.
