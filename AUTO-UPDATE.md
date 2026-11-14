# Automatic updates

How an installed PC Widgets keeps itself up to date, and the plan for
building it. Agreed with the developer on 2026-11-14, for 1.1.0.

| milestone | state |
|---|---|
| M1 – find, trust and download an update | done |
| M2 – install it: a silent upgrade that reopens the app, tested end to end | done |
| M3 – Settings › App and the tray: the switch, Check now, the status, one tip | done |
| M4 – release: the signing key, the workflow signs and attaches the update file, a CI dry run | done |

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
5. **Settings › Updates.** An "Automatic updates" switch (on), "Check now", and a
   status line; one tray tip when an update is ready.

Copies of 1.0.2 have no updater, so they move to 1.1.0 by running its
installer once, over the top; from 1.1.0 on, updates come by themselves.

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

## What M2 laid down

- The installer has a silent update mode, `/RELAUNCH=background|window`
  (packaging/pc-widgets.iss). It waits up to half a minute for the app's
  single-instance mutex to go - the app quits as it starts Setup - and Restart
  Manager closes anything still holding a file after that. It upgrades in
  place, replacing `_internal` whole as before, and reopens the app the way it
  was running, with arguments from a fixed list only. It makes no shortcuts:
  without that, an update would bring back a Startup or Desktop shortcut the
  person had since removed in Settings.
- `Updater.apply()` starts the checked installer - re-hashed first, so one
  changed on disk since is never run - with `/VERYSILENT /SUPPRESSMSGBOXES
  /NORESTART /CLOSEAPPLICATIONS`, the relaunch mode and a log in the updates
  folder, detached so it outlives the app and inherits none of its handles,
  and records that it did. The caller then quits.
- `Updater.finish_install()`, at the next start, settles that record: this
  copy is the new version, so the update took (the installer is removed), or
  it's still the old one, so it didn't - and that version isn't started again
  by itself, only from Restart to update. A failing update can't loop.
- `updater.install_at_start()` runs in `main.py` right after the
  single-instance check and before anything is on screen: an installed copy
  with a checked update waiting installs it and quits; Setup reopens it.
  `updater.schedule()` runs the daily check a couple of minutes after start,
  then whenever one is due. Automatic updates are a preference in the
  updater's own `state.json` (on unless switched off; the switch is M3).
- `PC Widgets.exe --install-update <how>` installs a waiting, checked update
  at once: the build's own test of an update.
- `tools/build_exe.py --installer` now runs a real update: the build
  installed as version N, the same build packaged as N+1 left where the
  updater leaves a checked download, the installed exe told to install it.
  It must hand over and quit; Setup must upgrade in place (Settings -> Apps
  reads N+1) without making shortcuts and reopen the app - as its self test,
  which shows nothing and leaves a log. Then it all uninstalls.
- Selftest: `_check_update_install`, with Setup's start recorded instead of
  run.
- The full local build passed with it: version 1.0.2 installed, handed over
  to the same build packaged as 1.0.3, upgraded in place and reopened in
  68 seconds, Setup's log showing the silent command line and a clean
  install, and nothing left behind after the uninstall.

## What M3 laid down

- Settings has an **Updates** group, just before App. **Automatic updates**
  is the switch (on unless turned off; kept in the updater's state file).
  **PC Widgets <version>** carries a line saying what the updater is doing -
  checking, downloading with a percentage, a version ready, up to date and
  when it last checked, couldn't reach GitHub, an update that didn't install -
  and the one button that fits: Check now, or Restart to update once an update
  has downloaded, which is then the filled button in the accent's readable
  shade. Restart to update hands over to Setup and quits; Setup reopens the
  app with its window.
- A copy run from its folder says updates come with the installed app, and
  both controls rest.
- The tray says once a version that an update is ready ("It installs the next
  time PC Widgets starts. Click here to restart and update now"). Clicking that
  balloon - and only that one - restarts into the update; the app reopens as
  it was, with its window if it was open.
- The wording and the button live in the updater (`describe`, `action`), so
  they're tested without a window; at start the updater picks up where the
  last run left off, so Settings is right before any check runs.
- README's Download section says what happens from 1.1.0 on, and that 1.0.2
  copies need the 1.1.0 installer once; INSTALL.md says a copy run from source
  updates with git.
- Selftest: `_check_update_ui`, rendering the group to
  `_selftest/_app_updates_*.png`.

## What M4 laid down

- The release key exists. The developer made it on 2026-11-14 with
  `tools/update_key.py new --set-secret`, which handed the private half
  straight to the repository's `UPDATE_SIGNING_KEY` secret and saved a backup
  for a password manager; it was never printed or seen by anyone else. The
  public half is `updater.PUBLIC_KEY`. Never make a new one: installed copies
  only trust the key they carry.
- `updater.make_update_file()` writes a release's update file - version,
  installer address, size, SHA-256 and the signature over them - laid out
  readably; `tools/sign_update.py` runs it in the release workflow with the
  secret, and checks the result against the app's own public key before
  writing it. A secret that doesn't match the app fails the run, instead of
  every copy quietly refusing the update.
- `.github/workflows/release.yml` signs `update.json` after the build on every
  run; a tag attaches it to the release beside the installer, and a dry run
  keeps it with the installer artifact. The release notes say the app updates
  itself and that 1.0.2 copies need the new installer once.
- packaging/README.md's release steps cover the update file and the secret.
- Selftest: `_check_updater` now builds an update file and reads it back the
  way a copy does, checks the app's key is a real ECDSA P-256 public key, and
  that it refuses an update signed by any other key.
- The release workflow's dry run on the pushed branch passed on GitHub's
  Windows runner in under four minutes: every build check, the real update
  from 1.0.2 to 1.0.3, and the update file signed with the repository's
  secret and verified against the key built into the app.
