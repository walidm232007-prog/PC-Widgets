r"""Log the top CPU consumers every few seconds, so a spike leaves evidence.

Windows keeps no per-process CPU history, so "it hit 100% half an hour ago" is
unanswerable after the fact - which is exactly the position this was written in.
Start it before a gaming session and the spike is on record.

    python tools\cpuwatch.py            # 5s samples to tools/cpuwatch.log
    python tools\cpuwatch.py 2 60       # every 2s for 60 minutes
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
QUERY = 0x1000
CORES = os.cpu_count() or 1


def cpu_times(pid):
    h = k32.OpenProcess(QUERY, False, pid)
    if not h:
        return None
    try:
        ft = (wintypes.FILETIME * 4)()
        if not k32.GetProcessTimes(h, *[ctypes.byref(f) for f in ft]):
            return None
        to_s = lambda f: ((f.dwHighDateTime << 32) | f.dwLowDateTime) / 1e7
        return to_s(ft[2]) + to_s(ft[3])
    finally:
        k32.CloseHandle(h)


def snapshot():
    import subprocess
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process | Select-Object Id,ProcessName | ConvertTo-Csv -NoTypeInformation"],
        capture_output=True, text=True, creationflags=0x08000000).stdout
    procs = {}
    for line in out.splitlines()[1:]:
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) == 2 and parts[0].strip('"').isdigit():
            pid = int(parts[0].strip('"'))
            t = cpu_times(pid)
            if t is not None:
                procs[pid] = (parts[1].strip('"'), t)
    return procs


def main():
    every = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    minutes = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
    log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cpuwatch.log")

    print(f"sampling every {every:g}s for {minutes:g} min -> {log}")
    print("leave this running, play, then read the log.  ctrl-c to stop.")
    prev, prev_t = snapshot(), time.monotonic()
    deadline = time.monotonic() + minutes * 60

    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n==== started {time.strftime('%Y-%m-%d %H:%M:%S')} "
                 f"({CORES} logical cores) ====\n")
        while time.monotonic() < deadline:
            time.sleep(every)
            now, now_t = snapshot(), time.monotonic()
            span = now_t - prev_t
            rows = []
            for pid, (name, t) in now.items():
                was = prev.get(pid)
                if was is None:
                    continue
                pct = (t - was[1]) / span * 100.0
                if pct > 1.0:
                    rows.append((pct, name, pid))
            rows.sort(reverse=True)
            total = sum(r[0] for r in rows)
            line = (f"{time.strftime('%H:%M:%S')}  total {total / CORES:5.1f}% of CPU  |  "
                    + "  ".join(f"{n}({p}) {c / CORES:.1f}%" for c, n, p in rows[:6]))
            fh.write(line + "\n")
            fh.flush()
            print(line[:150])
            prev, prev_t = now, now_t


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
