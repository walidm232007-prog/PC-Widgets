"""
airdrop
=======
Real AirDrop, with Apple's devices, from something that isn't one.

This is the protocol core for the Drop widget: Discover, Ask and Upload over
HTTPS on Apple Wireless Direct Link, in the form an iPhone on iOS 27 speaks
them. It is plain Python with no Qt, because it doesn't run inside the board:
AWDL only exists on a Mac (built in) or on Linux under OWL, so on Windows this
runs inside WSL2 with a USB Wi-Fi adapter, and the widget talks to it from the
Windows side.

Written from what a real iPhone was recorded sending and expecting (M0), not
from OpenDrop's code - OpenDrop is GPL, this app is MIT, and OpenDrop's sends
stopped working around iOS 16 anyway.

    archive.py    the cpio archive and dvzip blocks, and unpacking them safely
    messages.py   Discover / Ask / Upload bodies and headers
    identity.py   this machine's TLS certificate
    link.py       the AWDL interface, its socket options, waking iPhones
    discovery.py  Bonjour for _airdrop._tcp on the link
    client.py     sending
    server.py     receiving
    __main__.py   python -m airdrop find | send | receive
    selftest.py   python -m airdrop.selftest - no network, no phone

Needs ``zeroconf`` and ``cryptography`` (``airdrop/requirements.txt``), on the
side that runs it - the board itself still needs nothing but PySide6.
"""
