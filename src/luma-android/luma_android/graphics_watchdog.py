# SPDX-License-Identifier: Apache-2.0
"""Restart Android after its graphics services restart.

When the composer or SurfaceFlinger dies, init restarts them, but in the
Waydroid container the `onrestart restart zygote` that should follow never
takes effect. system_server then keeps dead SurfaceFlinger references:
window creation fails with DEAD_OBJECT, SystemUI crash-loops and no app can
open until the whole container restarts. Ending only system_server is not
enough: zygote's own onrestart services (netd and the media servers) do not
restart either, and Android comes back without a network. Restart Android
whole instead: the container, then the owner's session (~20 s).
"""
from __future__ import annotations

import pwd
import re
import subprocess
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


def session_owner(proc: Path = Path("/proc")) -> str | None:
    """User name owning the Luma Android session broker, if one is running."""
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            if b"/usr/libexec/luma-waydroid-session" not in argv[:2]:
                continue
            name = pwd.getpwuid(entry.stat().st_uid).pw_name
        except (OSError, KeyError):
            continue
        if re.fullmatch(r"[a-z_][a-z0-9_-]*", name):
            return name
    return None


def restart_android(proc: Path = Path("/proc")) -> None:
    owner = session_owner(proc)
    if owner is None:
        print("No Android session owner; leaving the container alone", file=sys.stderr)
        return
    # This watchdog is PartOf the container; the recovery unit outlives it.
    subprocess.run(["/usr/bin/systemctl", "--no-block", "start",
                    f"luma-android-recover@{owner}.service"], check=False, timeout=10)


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
                      "restarting Android", file=sys.stderr)
                restart_android(proc)
                restarts.append(now)
            stale_since = None
        sleep(POLL_SECONDS)


def main() -> int:
    return monitor()
