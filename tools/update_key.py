"""
update_key.py
=============
Make the release key that signs PC Widgets' updates (AUTO-UPDATE.md).

    python tools/update_key.py new --set-secret

An ECDSA P-256 key pair from Windows' own cryptography (signature.py):

- the private half goes straight to ``gh secret set UPDATE_SIGNING_KEY`` for
  this repository, where the release workflow signs with it, and is saved to
  ``private-key.txt`` in the key folder for safekeeping. It is never printed.
- the public half is printed and saved to ``public-key.txt``: it goes into
  widgets/core/updater.py as PUBLIC_KEY.

The key folder is ``%USERPROFILE%\\PC Widgets release key`` unless --folder says
otherwise. Move private-key.txt into a password manager, then delete it. Lose
it and copies carrying this public key can never be sent another update;
leak it and anyone could sign one. A second run refuses to replace a key
that's already there.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from widgets.core import signature  # noqa: E402
from widgets.version import HOMEPAGE  # noqa: E402

SECRET = "UPDATE_SIGNING_KEY"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["new"])
    ap.add_argument("--folder", default=os.path.join(os.path.expanduser("~"),
                                                     "PC Widgets release key"))
    ap.add_argument("--set-secret", action="store_true",
                    help=f"also set {SECRET} on the repository with gh")
    args = ap.parse_args()

    os.makedirs(args.folder, exist_ok=True)
    private_path = os.path.join(args.folder, "private-key.txt")
    public_path = os.path.join(args.folder, "public-key.txt")
    if os.path.exists(private_path):
        raise SystemExit(f"{private_path} is already there - a release key exists. "
                         "Replacing it would leave every installed copy unable to update.")

    private, public = signature.generate()
    probe = signature.sign(private, b"PC Widgets")
    assert signature.verify(public, b"PC Widgets", probe), "the new key doesn't sign"
    with open(private_path, "w", encoding="ascii") as fh:
        fh.write(private + "\n")
    with open(public_path, "w", encoding="ascii") as fh:
        fh.write(public + "\n")

    if args.set_secret:
        repo = HOMEPAGE.removeprefix("https://github.com/").strip("/")
        done = subprocess.run(["gh", "secret", "set", SECRET, "--repo", repo],
                              input=private.encode("ascii"), capture_output=True)
        if done.returncode != 0:
            print(f"gh couldn't set {SECRET} on {repo}: "
                  f"{done.stderr.decode(errors='replace').strip()}")
            print(f"Set it yourself:  gh secret set {SECRET} --repo {repo} < \"{private_path}\"")
            return 1
        print(f"{SECRET} is set on {repo}.")

    print(f"Public key (goes into widgets/core/updater.py as PUBLIC_KEY), also in {public_path}:")
    print(f"  {public}")
    print(f"Private key saved to {private_path}.")
    print("Put it in a password manager, then delete that file. It isn't shown here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
