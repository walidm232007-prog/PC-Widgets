"""Read the current media session and print it as JSON. One shot, then exit.

This exists because a long-lived process can end up unable to import the WinRT
projection - the board came up that way after every reboot, with the user site
directory present and on ``sys.path`` and ``winrt`` still not importable - while
a process started moments later imports it without complaint. Rather than keep
guessing at why, the board can shell out to a fresh interpreter, which gets a
fresh ``sys.path`` and fresh import caches.

    python -m widgets.core.winrt_read [prefer] [known-track-key]

``prefer`` is a player id ("auto", "spotify", "applemusic"). ``known-track-key``
lets the caller say which sleeve it already has, so a 200KB cover is not
base64-encoded onto the pipe for every poll of the same track.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys


def _main() -> int:
    prefer = sys.argv[1] if len(sys.argv) > 1 else "auto"
    known = sys.argv[2] if len(sys.argv) > 2 else ""

    here = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    if here not in sys.path:
        sys.path.insert(0, here)

    from widgets.core.media import (
        _ensure_user_site, _pick_session, _read_stream, friendly_source)

    _ensure_user_site()          # the vendored projection, ahead of the profile

    import importlib
    root = None
    for cand in ("winrt", "winsdk"):
        try:
            importlib.import_module(f"{cand}.windows.media.control")
            root = cand
            break
        except Exception:
            continue
    if root is None:
        print(json.dumps({"ok": False, "error": "no projection importable"}))
        return 2

    control = importlib.import_module(f"{root}.windows.media.control")
    streams = importlib.import_module(f"{root}.windows.storage.streams")
    Manager = control.GlobalSystemMediaTransportControlsSessionManager
    Status = control.GlobalSystemMediaTransportControlsSessionPlaybackStatus

    async def grab() -> dict:
        mgr = await Manager.request_async()
        session, props, info = await _pick_session(mgr, Status, prefer)
        if session is None:
            return {"ok": True, "available": False}

        timeline = session.get_timeline_properties()

        def secs(v):
            return v.total_seconds() if hasattr(v, "total_seconds") else float(v)

        dur = secs(timeline.end_time) - secs(timeline.start_time)
        pos = secs(timeline.position) - secs(timeline.start_time)
        key = f"{props.artist}\x1f{props.title}\x1f{props.album_title}"

        art = ""
        if key != known and props.thumbnail is not None:
            try:
                stream = await props.thumbnail.open_read_async()
                reader = streams.DataReader(stream)
                await reader.load_async(stream.size)
                art = base64.b64encode(_read_stream(reader, stream.size)).decode()
            except Exception:
                art = ""

        return {
            "ok": True, "available": bool(props.title),
            "title": props.title or "", "artist": props.artist or "",
            "album": props.album_title or "",
            "source": friendly_source(session.source_app_user_model_id or ""),
            "playing": info.playback_status == Status.PLAYING,
            "position": pos if dur > 0 else None,
            "duration": dur if dur > 0 else None,
            "key": key, "art": art, "art_same": key == known,
        }

    print(json.dumps(asyncio.run(grab())))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
