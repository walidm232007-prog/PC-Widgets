"""
sign_update.py
==============
Write a release's update.json, signed - the file installed copies look for
every day (widgets/core/updater.py, AUTO-UPDATE.md).

    python tools/sign_update.py --installer build/PC-Widgets-Setup-1.0.3.exe
                                --version 1.0.3 --out build/update.json

The private key comes from the UPDATE_SIGNING_KEY environment variable: the
release workflow's secret, made by tools/update_key.py. The file is checked
against the public key built into the app before it's written, so a secret
that doesn't match the app fails the build here, instead of every copy
quietly refusing the update.
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from widgets.core import updater  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--installer", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    key = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
    if not key:
        raise SystemExit("UPDATE_SIGNING_KEY isn't set: the release key's private half "
                         "(tools/update_key.py) belongs in the repository's secrets")
    if not updater.PUBLIC_KEY:
        raise SystemExit("widgets/core/updater.py has no PUBLIC_KEY, so no copy could "
                         "trust the update")
    raw = updater.make_update_file(args.installer, args.version, key)
    try:
        upd = updater.read_update(raw)          # with the app's own public key
    except updater.UpdateError as exc:
        raise SystemExit(f"the update file doesn't pass the app's own check: {exc} - "
                         "is UPDATE_SIGNING_KEY the private half of updater.PUBLIC_KEY?")
    assert upd.version == args.version
    with open(args.out, "wb") as fh:
        fh.write(raw)
    print(f"  ok    {os.path.basename(args.out)} for {upd.version}: {upd.size} bytes, "
          f"SHA-256 {upd.sha256[:16]}..., signed and checked against the app's key")
    return 0


if __name__ == "__main__":
    sys.exit(main())
