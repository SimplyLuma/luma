# SPDX-License-Identifier: MPL-2.0
"""Resource use read straight from cgroup v2 files.

systemd already accounts every agent in its own cgroup; reading the kernel's
counters costs a few small reads when someone is looking and nothing when
nobody is.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")


@dataclass(frozen=True, slots=True)
class Usage:
    memory: int = 0
    memory_peak: int = 0
    cpu_usec: int = 0
    frozen: bool = False
    oom_kills: int = 0


def _read_int(path: Path) -> int:
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def _read_keyed(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in path.read_text(encoding="ascii").splitlines():
            key, _, value = line.partition(" ")
            if value.strip().isdigit():
                values[key] = int(value)
    except (OSError, UnicodeError):
        pass
    return values


def cgroup_path(control_group: str, root: Path = CGROUP_ROOT) -> Path | None:
    """The directory for a systemd ControlGroup property, if it is one."""

    if not control_group or not control_group.startswith("/") or ".." in control_group.split("/"):
        return None
    return root / control_group.lstrip("/")


def read_usage(control_group: str, root: Path = CGROUP_ROOT) -> Usage:
    directory = cgroup_path(control_group, root)
    if directory is None or not directory.is_dir():
        return Usage()
    events = _read_keyed(directory / "cgroup.events")
    memory_events = _read_keyed(directory / "memory.events")
    return Usage(
        memory=_read_int(directory / "memory.current"),
        memory_peak=_read_int(directory / "memory.peak"),
        cpu_usec=_read_keyed(directory / "cpu.stat").get("usage_usec", 0),
        frozen=events.get("frozen", 0) == 1,
        oom_kills=memory_events.get("oom_kill", 0),
    )


class CpuSampler:
    """CPU percent between two reads by the same caller.

    Each reader gets its own previous sample, so Settings refreshing every
    two seconds and the dock opening once do not skew each other's numbers.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._samples: dict[tuple[str, str], tuple[float, int]] = {}

    def percent(self, reader: str, app: str, cpu_usec: int) -> float:
        now = self._clock()
        key = (reader, app)
        previous = self._samples.get(key)
        self._samples[key] = (now, cpu_usec)
        if previous is None:
            return 0.0
        elapsed = now - previous[0]
        used = cpu_usec - previous[1]
        if elapsed <= 0 or used < 0:
            return 0.0
        return round(min(used / 1e6 / elapsed * 100.0, 100.0 * 256), 1)

    def forget_reader(self, reader: str) -> None:
        for key in [key for key in self._samples if key[0] == reader]:
            del self._samples[key]
