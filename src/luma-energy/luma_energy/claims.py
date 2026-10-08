# SPDX-License-Identifier: MPL-2.0
"""The reasons an application must be left alone whatever its windows are doing.

Every entry here exists because getting it wrong is not a lost watt, it is a
dropped call, a stopped song, a broken download or a missed alarm. The list is
expected to grow: it grows by someone finding a case we did not think of, which
is why the person's own switch exists as well.
"""
from __future__ import annotations

import os
from pathlib import Path

PROC = Path("/proc")

#: An application below this, over the sampling window, is doing nothing worth
#: protecting. Anything running a transfer, a build, a sync or an upload is
#: above it, because moving bytes costs processor time. One cheap test stands
#: in for most of "is it actually busy?" without a single new kernel interface.
QUIET_FRACTION_OF_A_CORE = 0.005

#: Processes that hold an audio, camera or call claim announce themselves by
#: having the device open. Reading who has what open is far cheaper, and far
#: harder to get wrong, than asking four different daemons.
DEVICE_HINTS = (
    "/dev/snd/",
    "/dev/video",
    "/dev/media",
    "/dev/dri/renderD",
)


def _pids_in(cgroup_procs: Path) -> list[int]:
    try:
        return [int(line) for line in cgroup_procs.read_text().split() if line.isdigit()]
    except OSError:
        return []


def holds_device(pids: list[int], hints: tuple[str, ...] = DEVICE_HINTS) -> bool:
    """Does any process here have a sound card, camera or encoder open?

    A renderD node is included because an application encoding or decoding
    video through it is doing exactly the kind of work whose interruption the
    person would see.
    """
    for pid in pids:
        directory = PROC / str(pid) / "fd"
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for entry in entries:
            try:
                target = os.readlink(directory / entry)
            except OSError:
                continue
            if any(target.startswith(hint) for hint in hints):
                return True
    return False


def holds_alarm(pids: list[int]) -> bool:
    """Is anything here waiting on a clock that is meant to wake the machine?

    A frozen cgroup's timers do not fire; the kernel delays them and they all
    arrive on thaw. For an application with no window and nothing scheduled
    that is correct and is what phones have done for years. For one holding a
    real alarm it is a missed alarm, so it is never frozen.
    """
    for pid in pids:
        try:
            entries = os.listdir(PROC / str(pid) / "fdinfo")
        except OSError:
            continue
        for entry in entries:
            try:
                text = (PROC / str(pid) / "fdinfo" / entry).read_text()
            except OSError:
                continue
            if "clockid: 8" in text or "clockid: 9" in text:
                # CLOCK_REALTIME_ALARM (8) and CLOCK_BOOTTIME_ALARM (9).
                return True
    return False


def cpu_usec(cgroup_path: Path) -> int:
    """Processor microseconds this cgroup has used, or 0 if it cannot be read."""
    try:
        for line in (cgroup_path / "cpu.stat").read_text().splitlines():
            if line.startswith("usage_usec"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def quiet(before: int, after: int, seconds: float) -> bool:
    """Has this cgroup been doing essentially nothing?"""
    if seconds <= 0:
        return False
    used = max(0, after - before) / 1_000_000.0
    return (used / seconds) < QUIET_FRACTION_OF_A_CORE


class Claims:
    """Why an application may not be limited or frozen right now.

    ``reasons`` returns them all rather than the first, because the honest
    answer to "why is this still running?" is the whole list, and that is what
    the person is shown.
    """

    def __init__(self, keep_running: set[str] | None = None) -> None:
        #: The person's own "keep running in the background", which outranks
        #: everything else here and is never overridden by a measurement.
        self.keep_running = keep_running or set()

    def reasons(self, cgroup: str, cgroup_path: Path,
                busy: bool, inhibited: bool) -> list[str]:
        found: list[str] = []
        if cgroup in self.keep_running:
            found.append("the person asked for it")
        # Measured work and a system inhibitor are true whether or not we can
        # read the process list. An earlier version returned early when
        # cgroup.procs could not be read, which silently dropped "still
        # working" -- so an application we could see least about was the one
        # we were most willing to limit. Exactly backwards.
        if busy:
            found.append("still working")
        if inhibited:
            found.append("asked the system to stay awake")
        pids = _pids_in(cgroup_path / "cgroup.procs")
        if not pids:
            return found
        if holds_device(pids):
            found.append("sound, camera or video hardware in use")
        if holds_alarm(pids):
            found.append("an alarm is set")
        return found
