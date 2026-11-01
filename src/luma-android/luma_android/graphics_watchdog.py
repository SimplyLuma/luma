# SPDX-License-Identifier: Apache-2.0
"""Restart Android's framework after its graphics services restart.

When the composer or SurfaceFlinger dies, init restarts them, but in the
Waydroid container the `onrestart restart zygote` that should follow never
takes effect. system_server then keeps dead SurfaceFlinger references:
window creation fails with DEAD_OBJECT, SystemUI crash-loops and no app can
open until the whole container restarts. Ending system_server makes zygote
and the framework start over against the new SurfaceFlinger (~15 s).
"""
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

CONTAINER_CGROUP = "0::/lxc.payload.waydroid"
POLL_SECONDS = 2
# SurfaceFlinger must stay newer than system_server this long; a framework
# start that is merely in progress never reaches it.
SETTLE_SECONDS = 6
MAX_RESTARTS = 3
RESTART_WINDOW_SECONDS = 600


def container_processes(proc: Path = Path("/proc")) -> dict[str, tuple[int, int]]:
    """Map surfaceflinger/system_server to (pid, start ticks) in the container."""
    found: dict[str, tuple[int, int]] = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
            if comm not in {"surfaceflinger", "system_server"}:
                continue
            if (entry / "cgroup").read_text().strip() != CONTAINER_CGROUP:
                continue
            start = int((entry / "stat").read_text().rsplit(")", 1)[1].split()[19])
        except (OSError, ValueError, IndexError):
            continue
        found[comm] = (int(entry.name), start)
    return found


def stale_framework(found: dict[str, tuple[int, int]]) -> int | None:
    """Return system_server's pid when SurfaceFlinger started after it."""
    if "surfaceflinger" not in found or "system_server" not in found:
        return None
    pid, framework_start = found["system_server"]
    return pid if found["surfaceflinger"][1] > framework_start else None


def monitor(proc: Path = Path("/proc"), sleep=time.sleep, clock=time.monotonic) -> int:
    restarts: list[float] = []
    stale_since: float | None = None
    while True:
        pid = stale_framework(container_processes(proc))
        now = clock()
        if pid is None:
            stale_since = None
        elif stale_since is None:
            stale_since = now
        elif now - stale_since >= SETTLE_SECONDS:
            restarts = [t for t in restarts if now - t < RESTART_WINDOW_SECONDS]
            if len(restarts) >= MAX_RESTARTS:
                print("Android graphics keep restarting; leaving the framework alone",
                      file=sys.stderr)
            else:
                print(f"SurfaceFlinger restarted under system_server {pid}; "
                      "restarting the Android framework", file=sys.stderr)
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                restarts.append(now)
            stale_since = None
        sleep(POLL_SECONDS)


def main() -> int:
    return monitor()
