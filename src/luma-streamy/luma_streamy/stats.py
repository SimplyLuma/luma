# SPDX-License-Identifier: Apache-2.0
"""System numbers for the Stats panel: CPU, GPU and its encoder, RAM, disk write rate and free space.

Pure Python reading /proc and /sys; each ``sample()`` is the change since the last one, so the panel
calls it at most once a second and only while it is shown or something is live.

GPU busy comes from DRM fdinfo (every client's per-engine time): i915 and amdgpu report nanoseconds
per engine (``drm-engine-<name>``, with ``drm-engine-capacity-<name>`` when an engine class has several
instances), xe reports cycles (``drm-cycles-<class>`` over ``drm-total-cycles-<class>``). Busy is the
busiest engine; "encoder" is the video engine (i915 ``video``, xe ``vcs``, amdgpu ``enc``).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Engine names that do video encode, by driver vocabulary.
VIDEO_ENGINES = ("video", "vcs", "enc", "vcn", "video_enc")
#: How often the set of processes holding a GPU is looked for again (fdinfo is re-read every sample).
RESCAN_S = 10.0


@dataclass
class System:
    cpu: float | None = None  # 0..1, all cores
    gpu: float | None = None  # 0..1, the busiest engine
    encoder: float | None = None  # 0..1, the video engine
    engines: dict = field(default_factory=dict)  # engine -> 0..1
    ram_used: int = 0
    ram_total: int = 0
    disk_write: float | None = None  # bytes/s to the recording's disk
    disk_free: int | None = None
    disk_total: int | None = None


def parse_cpu(stat_text: str) -> tuple[int, int]:
    """(busy, total) jiffies from /proc/stat's first line."""
    fields = [int(v) for v in stat_text.split("\n", 1)[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    total = sum(fields[:8])
    return total - idle, total


def parse_meminfo(text: str) -> tuple[int, int]:
    """(used, total) bytes."""
    values = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            values[name] = int(parts[0]) * 1024
    total = values.get("MemTotal", 0)
    return total - values.get("MemAvailable", values.get("MemFree", 0)), total


def parse_diskstats(text: str, device: str) -> int | None:
    """Bytes written so far by ``device`` (sectors are 512 bytes in diskstats)."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) > 9 and parts[2] == device:
            return int(parts[9]) * 512
    return None


def parse_fdinfo(text: str) -> dict | None:
    """One DRM fd's client: {"id", "driver", "ns": {engine: ns}, "cycles": {class: (busy, total)},
    "capacity": {engine: n}}, or None for a non-DRM fd."""
    info: dict = {"ns": {}, "cycles": {}, "total": {}, "capacity": {}}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        value = value.strip()
        if key == "drm-driver":
            info["driver"] = value
        elif key == "drm-client-id":
            info["id"] = value
        elif key.startswith("drm-engine-capacity-"):
            info["capacity"][key[len("drm-engine-capacity-"):]] = int(value or 1)
        elif key.startswith("drm-engine-") and value.endswith("ns"):
            info["ns"][key[len("drm-engine-"):]] = int(value.split()[0])
        elif key.startswith("drm-total-cycles-"):
            info["total"][key[len("drm-total-cycles-"):]] = int(value.split()[0])
        elif key.startswith("drm-cycles-"):
            info["cycles"][key[len("drm-cycles-"):]] = int(value.split()[0])
    if "driver" not in info:
        return None
    info.setdefault("id", "")
    return info


def engine_busy(before: dict, after: dict, wall_ns: int) -> dict:
    """Per-engine busy (0..1) across every client, from two readings {client key: parse_fdinfo()}."""
    ns: dict[str, float] = {}
    cycles: dict[str, list[int]] = {}
    for client, now in after.items():
        then = before.get(client)
        if then is None:
            continue
        for engine, value in now["ns"].items():
            delta = value - then["ns"].get(engine, value)
            capacity = max(1, now["capacity"].get(engine, 1))
            ns[engine] = ns.get(engine, 0.0) + max(0, delta) / capacity
        for engine, value in now["cycles"].items():
            busy = value - then["cycles"].get(engine, value)
            total = now["total"].get(engine, 0) - then["total"].get(engine, now["total"].get(engine, 0))
            slot = cycles.setdefault(engine, [0, 0])
            slot[0] += max(0, busy)
            slot[1] = max(slot[1], total)  # one GPU clock: the same total for every client
    busy = {engine: min(1.0, value / wall_ns) for engine, value in ns.items() if wall_ns > 0}
    for engine, (b, total) in cycles.items():
        if total > 0:
            busy[engine] = min(1.0, b / total)
    return busy


class Sampler:
    """Reads the system once per call; numbers that need two readings are None on the first."""

    def __init__(self, disk_path: str | os.PathLike | None = None, *, proc: str = "/proc", sys: str = "/sys") -> None:
        self.proc, self.sys = Path(proc), Path(sys)
        self.disk_path = Path(disk_path) if disk_path else Path.home()
        self._cpu: tuple[int, int] | None = None
        self._disk: tuple[float, int] | None = None
        self._drm: tuple[int, dict] | None = None
        self._drm_fds: list[Path] = []
        self._scanned = 0.0
        self._device = self._block_device()

    def _block_device(self) -> str | None:
        path = self.disk_path
        while not path.exists() and path != path.parent:
            path = path.parent
        try:
            dev = os.stat(path).st_dev
        except OSError:
            return None
        block = self.sys / "dev/block" / f"{os.major(dev)}:{os.minor(dev)}"
        try:
            name = block.resolve().name
        except OSError:
            return None
        return name if (self.sys / "class/block" / name).exists() or block.exists() else None

    def sample(self) -> System:
        system = System()
        now = time.monotonic()
        try:
            busy, total = parse_cpu((self.proc / "stat").read_text())
            if self._cpu is not None and total > self._cpu[1]:
                system.cpu = (busy - self._cpu[0]) / (total - self._cpu[1])
            self._cpu = (busy, total)
        except (OSError, ValueError, IndexError):
            pass
        try:
            system.ram_used, system.ram_total = parse_meminfo((self.proc / "meminfo").read_text())
        except (OSError, ValueError):
            pass
        try:
            stat = os.statvfs(self._existing(self.disk_path))
            system.disk_free, system.disk_total = stat.f_bavail * stat.f_frsize, stat.f_blocks * stat.f_frsize
        except OSError:
            pass
        if self._device:
            try:
                written = parse_diskstats((self.proc / "diskstats").read_text(), self._device)
            except OSError:
                written = None
            if written is not None:
                if self._disk is not None and now > self._disk[0]:
                    system.disk_write = max(0, written - self._disk[1]) / (now - self._disk[0])
                self._disk = (now, written)
        self._sample_gpu(system)
        return system

    @staticmethod
    def _existing(path: Path) -> Path:
        while not path.exists() and path != path.parent:
            path = path.parent
        return path

    def _scan_drm(self) -> None:
        found = []
        for pid in os.listdir(self.proc):
            if not pid.isdigit():
                continue
            fd_dir = self.proc / pid / "fd"
            try:
                fds = os.listdir(fd_dir)
            except OSError:
                continue
            for fd in fds:
                try:
                    if os.readlink(fd_dir / fd).startswith("/dev/dri/"):
                        found.append(self.proc / pid / "fdinfo" / fd)
                except OSError:
                    continue
        self._drm_fds = found
        self._scanned = time.monotonic()

    def _sample_gpu(self, system: System) -> None:
        if time.monotonic() - self._scanned > RESCAN_S:
            self._scan_drm()
        readings: dict = {}
        for path in self._drm_fds:
            try:
                info = parse_fdinfo(path.read_text())
            except OSError:
                continue
            if info is not None:
                # One client can hold several fds: count it once.
                readings[(info["driver"], info["id"] or str(path))] = info
        now = time.monotonic_ns()
        if self._drm is not None and readings:
            busy = engine_busy(self._drm[1], readings, now - self._drm[0])
            if busy:
                system.engines = busy
                system.gpu = max(busy.values())
                video = [v for k, v in busy.items() if k.split("-")[0] in VIDEO_ENGINES or k in VIDEO_ENGINES]
                system.encoder = max(video) if video else None
        self._drm = (now, readings)


def format_bytes(value: float) -> str:
    for unit, size in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if value >= size:
            return f"{value / size:.1f} {unit}"
    return f"{value:.0f} B"


def percent(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)} %"
