# SPDX-License-Identifier: MPL-2.0
"""Applying and, above all, undoing limits on an application's own cgroup.

Everything here is runtime-scoped and reversible. Nothing Luma sets survives a
reboot, a logout, or this service stopping, because a power feature that can
leave a machine in a state the person cannot explain or undo is worse than no
power feature.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")

#: systemd's own name for a cgroup, which is how properties are set. Setting
#: them through systemd rather than by writing the files directly means
#: systemd knows what it has been told and will not undo it at the next reload.
def unit_of(cgroup: str) -> str:
    """"/user.slice/.../app-gnome-foo-123.scope" -> "app-gnome-foo-123.scope"."""
    name = cgroup.rstrip("/").rsplit("/", 1)[-1]
    return name if name.endswith((".scope", ".service", ".slice")) else ""


def path_of(cgroup: str) -> Path:
    return CGROUP_ROOT / cgroup.lstrip("/")


def _systemctl(*arguments: str) -> bool:
    try:
        finished = subprocess.run(
            ("systemctl", "--user", *arguments),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return finished.returncode == 0


def set_properties(unit: str, properties: dict[str, int]) -> bool:
    """Set runtime properties, which evaporate on reboot and on stop."""
    if not unit or not properties:
        return False
    arguments = [f"{key}={value}" for key, value in sorted(properties.items())]
    return _systemctl("set-property", "--runtime", unit, *arguments)


def clear_properties(unit: str, keys: list[str]) -> bool:
    """Put a unit back exactly as it was found.

    systemd restores a property's default when it is set to its documented
    "unset" value, which is what each of these is.
    """
    defaults = {"CPUWeight": "100", "IOWeight": "100", "CPUQuota": ""}
    arguments = [f"{key}={defaults.get(key, '')}" for key in keys if key in defaults]
    if not arguments:
        return True
    return _systemctl("set-property", "--runtime", unit, *arguments)


def set_uclamp(cgroup: str, percent: int | None) -> bool:
    """Tell the governor this group does not need a high clock.

    systemd has no property for it, so it is written directly. "max" is the
    kernel's own word for "no opinion", which is what removing the hint means.
    """
    target = path_of(cgroup) / "cpu.uclamp.max"
    value = "max" if percent is None else str(max(1, min(100, percent)))
    try:
        target.write_text(value)
    except OSError:
        return False
    return True


def freeze(unit: str) -> bool:
    return _systemctl("freeze", unit)


def thaw(unit: str) -> bool:
    return _systemctl("thaw", unit)


def frozen(cgroup: str) -> bool:
    try:
        events = (path_of(cgroup) / "cgroup.events").read_text()
    except OSError:
        return False
    return "frozen 1" in events


def exists(cgroup: str) -> bool:
    return path_of(cgroup).is_dir()
