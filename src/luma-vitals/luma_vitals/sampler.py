# SPDX-License-Identifier: Apache-2.0
"""Read resource use from cgroups and the kernel, without root and without tracing."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")


@dataclass
class UnitSample:
    unit: str
    cpu_usec: int
    memory: int
    memory_peak: int
    swap: int
    io_read: int
    io_write: int
    #: The slices it runs in, below the session: "app.slice", "luma-background-essential.slice".
    slice: str = ""


@dataclass
class MachineSample:
    time: float
    memory_total: int
    memory_available: int
    swap_total: int
    swap_free: int
    pressure: dict = field(default_factory=dict)   # resource -> {"some": avg10, "full": avg10}
    temperature_c: float | None = None
    battery_watts: float | None = None
    units: list[UnitSample] = field(default_factory=list)


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _int(path: Path) -> int:
    text = _read(path).strip()
    return int(text) if text.isdigit() else 0


def _keyed(text: str) -> dict[str, int]:
    values = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            values[parts[0]] = int(parts[1])
    return values


def session_root(uid: int | None = None) -> Path:
    uid = os.getuid() if uid is None else uid
    return CGROUP_ROOT / f"user.slice/user-{uid}.slice/user@{uid}.service"


def container_names(proc: Path = Path("/proc")) -> dict[str, str]:
    """Podman container id prefix -> container name, read from each conmon's arguments.

    A container's processes run in libpod-<id>.scope, which names nothing a
    person recognises; its conmon was started with -c <id> -n <name>.
    """
    names = {}
    for entry in proc.iterdir():
        if not entry.name.isdigit() or _read(entry / "comm").strip() != "conmon":
            continue
        arguments = _read(entry / "cmdline").split("\0")
        pairs = dict(zip(arguments, arguments[1:]))
        identity, name = pairs.get("-c") or pairs.get("--cid"), pairs.get("-n") or pairs.get("--name")
        if identity and name:
            names[identity[:12]] = name
    return names


def units(root: Path) -> list[UnitSample]:
    """Every scope and service under the session, named by its leaf unit."""
    found = []
    containers = None
    for directory, subdirectories, _files in os.walk(root):
        name = os.path.basename(directory)
        # The session root is itself user@UID.service; counting it would hide
        # every application inside one unit.
        if Path(directory) == Path(root) or not name.endswith((".scope", ".service")):
            continue
        subdirectories[:] = []  # a unit's own cgroup already includes its children
        path = Path(directory)
        if name.startswith("libpod-") and not name.startswith("libpod-conmon-"):
            if containers is None:
                containers = container_names()
            identity = name[len("libpod-"):][:12]
            if identity in containers:
                name = f"{containers[identity]}.container"
        found.append(_unit_sample(path, name, str(path.parent.relative_to(root)) if path.parent != Path(root) else ""))
    return found


def _unit_sample(path: Path, name: str, slice_path: str = "") -> UnitSample:
    cpu = _keyed(_read(path / "cpu.stat")).get("usage_usec", 0)
    read = write = 0
    for line in _read(path / "io.stat").splitlines():
        for item in line.split()[1:]:
            key, _, value = item.partition("=")
            if key == "rbytes":
                read += int(value)
            elif key == "wbytes":
                write += int(value)
    return UnitSample(name, cpu, _int(path / "memory.current"), _int(path / "memory.peak"),
                      _int(path / "memory.swap.current"), read, write, slice_path)


def _pressure() -> dict:
    result = {}
    for resource in ("cpu", "memory", "io"):
        entry = {}
        for line in _read(Path(f"/proc/pressure/{resource}")).splitlines():
            kind, *fields = line.split()
            values = dict(f.split("=", 1) for f in fields)
            entry[kind] = float(values.get("avg10", 0))
        result[resource] = entry
    return result


def _temperature() -> float | None:
    hottest = None
    for zone in Path("/sys/class/thermal").glob("thermal_zone*"):
        kind = _read(zone / "type").strip()
        if kind.startswith(("acpitz", "x86_pkg_temp", "TCPU", "cpu", "soc")):
            value = _int(zone / "temp")
            if value:
                hottest = max(hottest or 0.0, value / 1000)
    return hottest


def _battery_watts() -> float | None:
    for battery in Path("/sys/class/power_supply").glob("BAT*"):
        if _read(battery / "status").strip() == "Discharging":
            microwatts = _int(battery / "power_now")
            if microwatts:
                return microwatts / 1_000_000
    return None


def containers(cgroup_root: Path = CGROUP_ROOT) -> list[UnitSample]:
    """System containers the person's applications run in, such as Android.

    Waydroid's Android runs in lxc.payload.<name> at the top of the cgroup
    tree, outside the session: an Android app burning two cores never
    appeared in any report.
    """
    found = []
    for path in sorted(cgroup_root.glob("lxc.payload.*")):
        if not path.is_dir():
            continue
        name = path.name[len("lxc.payload."):]
        sample = _unit_sample(path, f"{name}.lxc")
        found.append(sample)
    return found


def machine(root: Path | None = None) -> MachineSample:
    info = _keyed("\n".join(" ".join(line.replace(":", "").split()[:2]) for line in _read(Path("/proc/meminfo")).splitlines()))
    return MachineSample(
        time=time.time(),
        memory_total=info.get("MemTotal", 0) * 1024,
        memory_available=info.get("MemAvailable", 0) * 1024,
        swap_total=info.get("SwapTotal", 0) * 1024,
        swap_free=info.get("SwapFree", 0) * 1024,
        pressure=_pressure(),
        temperature_c=_temperature(),
        battery_watts=_battery_watts(),
        units=units(root or session_root()) + ([] if root else containers()),
    )
