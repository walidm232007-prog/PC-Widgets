"""
prepublish.py — refuse to let this repository go public with something in it
that shouldn't be.

    python tools/prepublish.py

Exits 0 if it is safe to push, 1 if not. Run it before `git push`, or wire it up
as a pre-commit hook.

Every check here exists because the mistake is silent and expensive: a font file
or an API key that reaches a public remote has to be treated as leaked even after
you delete it, because git keeps history and crawlers are fast.

When the folder is a git repository, the checks look at what git would actually
publish - tracked files - rather than what happens to be on disk. That is the
distinction that matters: an ignored font is fine, a committed one is not.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Extensions that must never be committed, and why.
FORBIDDEN_EXT = {
    ".otf": "font file (SF Pro is Apple's and may not be redistributed)",
    ".ttf": "font file (check its licence before committing)",
    ".ttc": "font file (check its licence before committing)",
    ".lnk": "Windows shortcut (holds absolute paths to one machine)",
}

# The fonts that may be published, each with the licence that has to travel
# with it. Inter is under the SIL OFL, which allows bundling it with software
# as long as its licence goes along.
ALLOWED_FONTS = {
    "fonts/InterVariable.ttf": "fonts/LICENSE-Inter.txt",
    "fonts/InterVariable-Italic.ttf": "fonts/LICENSE-Inter.txt",
}

# Things that look like credentials. Each pattern is deliberately narrow enough
# not to fire on ordinary prose.
SECRETS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{12}"), "Anthropic API key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{32,}"), "OpenAI-style API key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "GitHub token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    # The published-calendar link is a bearer URL: anyone holding it can read
    # the calendar, so it is a secret even though it looks like a plain link.
    (re.compile(r"outlook\.(?:live|office)\.com/owa/calendar/[A-Za-z0-9]{8,}"),
     "published Outlook calendar link (grants read access to the calendar)"),
]

TEXT_EXT = {".py", ".md", ".txt", ".json", ".bat", ".cfg", ".ini", ".toml",
            ".yml", ".yaml", ".html", ".js", ".css", ""}

problems: list = []
notes: list = []


def fail(msg: str) -> None:
    problems.append(msg)


def note(msg: str) -> None:
    notes.append(msg)


# --------------------------------------------------------------------------- #
def in_git() -> bool:
    try:
        r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           cwd=ROOT, capture_output=True, text=True)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except FileNotFoundError:
        return False


def tracked_files() -> list:
    r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                       capture_output=True, text=True)
    return [p for p in r.stdout.splitlines() if p.strip()]


def walked_files() -> list:
    out = []
    skip = {"__pycache__", ".git", "_selftest", ".venv", "venv", "node_modules"}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            out.append(os.path.relpath(full, ROOT).replace("\\", "/"))
    return out


def ignore_rules() -> list:
    """The glob patterns from .gitignore, enough of them to be useful.

    Before `git init` there is no index to ask, so "would this be published?"
    has to be answered from .gitignore instead. Without this the checker calls
    every ignored file a blocker, and a checker that cries wolf gets skipped -
    which defeats the point of having one.
    """
    path = os.path.join(ROOT, ".gitignore")
    if not os.path.isfile(path):
        return []
    rules = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if line and not line.startswith("#") and not line.startswith("!"):
                rules.append(line.rstrip("/"))
    return rules


def is_ignored(rel: str, rules: list) -> bool:
    from fnmatch import fnmatch
    base = os.path.basename(rel)
    for rule in rules:
        if fnmatch(rel, rule) or fnmatch(base, rule):
            return True
        # A directory rule covers everything beneath it.
        if "/" not in rule and f"/{rule}/" in f"/{rel}":
            return True
        if rule.endswith("*") and rel.startswith(rule[:-1]):
            return True
    return False


# --------------------------------------------------------------------------- #
def check_forbidden(files: list, tracked: bool) -> None:
    for rel in files:
        if rel in ALLOWED_FONTS:
            continue
        ext = os.path.splitext(rel)[1].lower()
        why = FORBIDDEN_EXT.get(ext)
        if why:
            if tracked:
                fail(f"{rel} would be published - {why}")
            else:
                note(f"{rel} is present but untracked ({why}) - keep it that way")


def check_font_licences(files: list) -> None:
    """A bundled font is only publishable with its licence beside it."""
    published = set(files)
    for rel, licence in ALLOWED_FONTS.items():
        if rel in published and licence not in published:
            fail(f"{rel} would be published without {licence} - its licence "
                 "must travel with it")


def check_secrets(files: list) -> None:
    for rel in files:
        if os.path.splitext(rel)[1].lower() not in TEXT_EXT:
            continue
        full = os.path.join(ROOT, rel)
        try:
            if os.path.getsize(full) > 2_000_000:
                continue
            with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                body = fh.read()
        except OSError:
            continue
        for pattern, label in SECRETS:
            m = pattern.search(body)
            if m:
                line = body[:m.start()].count("\n") + 1
                fail(f"{rel}:{line} looks like a {label}")


def check_paths(files: list) -> None:
    """Absolute user paths give away a username and break on every other machine."""
    pattern = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+([^\\/\s\"']+)", re.I)
    for rel in files:
        if os.path.splitext(rel)[1].lower() not in TEXT_EXT:
            continue
        try:
            with open(os.path.join(ROOT, rel), "r", encoding="utf-8",
                      errors="ignore") as fh:
                body = fh.read()
        except OSError:
            continue
        m = pattern.search(body)
        if m:
            line = body[:m.start()].count("\n") + 1
            note(f"{rel}:{line} has an absolute path naming user {m.group(1)!r}")


def check_licence() -> None:
    path = os.path.join(ROOT, "LICENSE")
    if not os.path.isfile(path):
        fail("no LICENSE file - without one, default copyright applies and "
             "nobody may legally use this")
        return
    with open(path, encoding="utf-8") as fh:
        body = fh.read()
    if "YOUR NAME HERE" in body:
        fail("LICENSE still says 'YOUR NAME HERE' - put your name in the "
             "copyright line")


def check_docs() -> None:
    for name, why in (("THIRD-PARTY.md", "records what the third-party terms are"),
                      (".gitignore", "keeps the fonts and local files out")):
        if not os.path.isfile(os.path.join(ROOT, name)):
            fail(f"no {name} - it {why}")
    gi = os.path.join(ROOT, ".gitignore")
    if os.path.isfile(gi):
        with open(gi, encoding="utf-8") as fh:
            body = fh.read()
        if "fonts/*.otf" not in body:
            fail(".gitignore does not exclude fonts/*.otf")


# --------------------------------------------------------------------------- #
def main() -> int:
    git = in_git()
    if git:
        files = tracked_files()
        print(f"git repository: checking {len(files)} tracked file(s)\n")
        # Untracked-but-present forbidden files are only worth a note.
        check_forbidden(files, tracked=True)
        present = set(walked_files()) - set(files)
        check_forbidden(sorted(present), tracked=False)
    else:
        files = walked_files()
        rules = ignore_rules()
        would_ship = [f for f in files if not is_ignored(f, rules)]
        ignored = [f for f in files if is_ignored(f, rules)]
        print(f"not a git repository yet: {len(files)} file(s) on disk, "
              f"{len(would_ship)} would be committed, "
              f"{len(ignored)} covered by .gitignore\n")
        check_forbidden(would_ship, tracked=True)
        check_forbidden(ignored, tracked=False)
        files = would_ship        # only scan what would actually be published

    check_font_licences(files)
    check_secrets(files)
    check_paths(files)
    check_licence()
    check_docs()

    for n in notes:
        print(f"  note  {n}")
    if notes:
        print()
    if problems:
        for p in problems:
            print(f"  STOP  {p}")
        print(f"\n{len(problems)} blocker(s). Do not publish yet.")
        return 1
    print("  ok    nothing forbidden, no secrets, licences and notices in place")
    print("\nSafe to publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
