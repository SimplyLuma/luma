# SPDX-License-Identifier: Apache-2.0
"""On battery: what the machine is drawing, and what is spending it (ADR-026 §8).

Everything here is read from files the kernel already keeps. Nothing traces,
nothing polls a device, and nothing runs at all while the machine is on mains
power, because an idle laptop's own measurement is the last thing that should
cost it a watt.

Three kinds of reading, at three costs:

* the *draw* — battery charge and rate, the screen, the lid — is a handful of
  small files, read every sample;
* the *energy counters* — Intel RAPL for the package, cores, graphics and
  memory — are root-only since the PLATYPUS mitigation, so they are not read
  here at all: luma-vitals-energy averages them over ten seconds and publishes
  the averages, which carry no side channel, to /run/luma-vitals/energy.json;
* the *attribution* — which process is waking the machine, which interrupt is
  arriving, what is keeping the package out of its deep idle states — walks
  /proc and is the only expensive part, so it runs once a minute instead of
  every sample.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

POWER_SUPPLY = Path("/sys/class/power_supply")
CPUIDLE = Path("/sys/devices/system/cpu/cpuidle")
ENERGY_PUBLICATION = Path("/run/luma-vitals/energy.json")
#: How often the /proc walk runs, in seconds. Cheap enough to be honest about.
ATTRIBUTION_SECONDS = 60.0
#: Below this many wakeups a second a process is not worth naming.
WAKEUP_FLOOR = 5.0


def _read(path) -> str:
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def _int(path) -> int:
    text = _read(path).strip()
    try:
        return int(text)
    except ValueError:
        return 0


@dataclass
class Draw:
    """The battery itself, and the two things that change it most."""
    on_battery: bool
    status: str = ""
    #: Watts out of the battery now, from power_now, or charge rate x voltage.
    watts: float | None = None
    energy_wh: float | None = None
    energy_full_wh: float | None = None
    percent: int | None = None
    #: Seconds at the present rate. The battery's own estimate is not used:
    #: firmware smooths it over minutes and hides exactly the spikes we want.
    seconds_to_empty: float | None = None
    screen_percent: float | None = None
    lid_closed: bool | None = None


@dataclass
class Attribution:
    """Who woke the machine, and what stopped it going back to sleep."""
    #: process name -> wakeups a second, highest first.
    wakeups: dict[str, float] = field(default_factory=dict)
    #: process name -> percent of one core.
    cpu: dict[str, float] = field(default_factory=dict)
    #: interrupt name -> interrupts a second.
    interrupts: dict[str, float] = field(default_factory=dict)
    #: device wakeup source -> events a second.
    devices: dict[str, float] = field(default_factory=dict)
    #: idle state name -> percent of the interval spent in it.
    idle_residency: dict[str, float] = field(default_factory=dict)
    #: Percent of the interval the package spent in its deepest idle state.
    package_idle_percent: float | None = None
    #: Percent of the interval the graphics engine was gated off.
    gpu_idle_percent: float | None = None
    gpu_mhz: int | None = None
    #: Reasons the package cannot reach deep idle, most likely first.
    blockers: list[str] = field(default_factory=list)


@dataclass
class PowerSample:
    time: float
    draw: Draw
    #: RAPL domain ("package", "core", "graphics", "memory") -> watts.
    domains: dict[str, float] = field(default_factory=dict)
    attribution: Attribution | None = None
    #: How long this sample's own reading took, in milliseconds.
    cost_ms: float = 0.0


# ---------------------------------------------------------------- the draw

def _battery() -> Path | None:
    for path in sorted(POWER_SUPPLY.glob("BAT*")):
        if _read(path / "type").strip() == "Battery":
            return path
    return None


def _screen_percent() -> float | None:
    for panel in sorted(Path("/sys/class/backlight").glob("*")):
        maximum = _int(panel / "max_brightness")
        if maximum:
            return round(100 * _int(panel / "brightness") / maximum, 1)
    return None


def _lid_closed() -> bool | None:
    for state in Path("/proc/acpi/button/lid").glob("*/state"):
        text = _read(state).lower()
        if "closed" in text:
            return True
        if "open" in text:
            return False
    return None


def draw() -> Draw:
    battery = _battery()
    if battery is None:
        return Draw(on_battery=False)
    status = _read(battery / "status").strip()
    on_battery = status == "Discharging"
    micro_watts = _int(battery / "power_now")
    energy = _int(battery / "energy_now")
    full = _int(battery / "energy_full")
    if not micro_watts:  # some firmware reports charge (µAh) and voltage
        volts = _int(battery / "voltage_now") / 1e6
        micro_watts = int(_int(battery / "current_now") * volts)
        energy = int(_int(battery / "charge_now") * volts)
        full = int(_int(battery / "charge_full") * volts)
    watts = micro_watts / 1e6 if micro_watts else None
    energy_wh = energy / 1e6 if energy else None
    return Draw(
        on_battery=on_battery,
        status=status,
        watts=watts,
        energy_wh=energy_wh,
        energy_full_wh=full / 1e6 if full else None,
        percent=_int(battery / "capacity") or None,
        seconds_to_empty=(energy_wh / watts * 3600) if on_battery and watts and energy_wh else None,
        screen_percent=_screen_percent(),
        lid_closed=_lid_closed(),
    )


def domains(publication: Path = ENERGY_PUBLICATION) -> dict[str, float]:
    """Average watts per RAPL domain, as published by luma-vitals-energy.

    Absent — no Intel RAPL, or the system service is not installed — the
    battery's own rate is all we have, which is still the number that matters.
    """
    try:
        data = json.loads(publication.read_text())
    except (OSError, ValueError):
        return {}
    if time.time() - data.get("time", 0) > 60:
        return {}
    return {name: float(watts) for name, watts in data.get("watts", {}).items()}


# --------------------------------------------------------- the attribution

def _process_counters() -> dict[int, tuple[str, int, int]]:
    """pid -> (name, nanoseconds on cpu, times scheduled).

    /proc/<pid>/schedstat's third field counts how often the scheduler put the
    task on a processor, which is as close to "wakeups" as the kernel will say
    without tracing. It is one read per process and nothing else.
    """
    found = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        fields = _read(f"/proc/{entry}/schedstat").split()
        if len(fields) != 3:
            continue
        name = _read(f"/proc/{entry}/comm").strip()
        if not name:
            continue
        try:
            found[int(entry)] = (name, int(fields[0]), int(fields[2]))
        except ValueError:
            continue
    return found


def _interrupt_counters() -> dict[str, int]:
    found = {}
    for line in _read("/proc/interrupts").splitlines()[1:]:
        parts = line.split()
        if not parts or not parts[0].endswith(":"):
            continue
        counts, rest = [], parts[1:]
        for value in rest:
            if value.lstrip("-").isdigit():
                counts.append(int(value))
            else:
                break
        if not counts:
            continue
        # "30 i2c_designware.2" says more than "30" and less than the whole
        # line, whose tail is the chip, the trigger and every driver sharing it.
        name = parts[0].rstrip(":")
        drivers = " ".join(rest[len(counts):]).split()
        found[f"{name} {drivers[-1].rstrip(',')}" if drivers else name] = sum(counts)
    return found


def _wakeup_counters() -> dict[str, int]:
    found = {}
    for source in Path("/sys/class/wakeup").glob("wakeup*"):
        name = _read(source / "name").strip()
        if name:
            found[name] = _int(source / "event_count")
    return found


def _idle_counters() -> dict[str, int]:
    """Microseconds in each idle state, summed over every processor."""
    found: dict[str, int] = {}
    for state in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpuidle/state*"):
        name = _read(state / "name").strip()
        if name and name != "POLL":
            found[name] = found.get(name, 0) + _int(state / "time")
    return found


def _graphics() -> tuple[int, int | None]:
    """(microseconds the render engine was gated off, its frequency in MHz)."""
    for card in sorted(Path("/sys/class/drm").glob("card*/device")):
        gt = card / "tile0/gt0"
        if (gt / "gtidle/idle_residency_ms").exists():
            return _int(gt / "gtidle/idle_residency_ms") * 1000, _int(gt / "freq0/act_freq") or None
        legacy = card / "power/runtime_suspended_time"  # i915 and older
        if legacy.exists():
            return _int(legacy) * 1000, None
    return 0, None


def _runtime_pm_off() -> list[str]:
    """Devices told never to suspend. On a laptop each one is a standing cost."""
    stuck = []
    for control in Path("/sys/bus/pci/devices").glob("*/power/control"):
        if _read(control).strip() == "on":
            device = control.parent.parent
            # The driver's name means something to a person; 0000:00:13.1 does not.
            driver = device / "driver"
            stuck.append(driver.resolve().name if driver.is_symlink() else device.name)
    return sorted(set(stuck))


def _profile() -> str:
    """The power profile in force, from tuned's own record."""
    for path in ("/etc/tuned/active_profile", "/run/tuned/active_profile"):
        name = _read(path).strip()
        if name:
            return name
    return ""


def _blockers(draw_now: Draw, package_idle: float | None, gpu_idle: float | None,
              interrupts: dict[str, float], runtime_pm_off: list[str]) -> list[str]:
    """Plain reasons the machine is not reaching its low-power states."""
    found = []
    profile = _profile()
    if draw_now.on_battery and profile in ("throughput-performance", "latency-performance",
                                           "accelerator-performance", "network-latency"):
        found.append(f"the {profile} power profile is in force on battery")
    if _read("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference").strip() == "performance" \
            and draw_now.on_battery:
        found.append("the processor is set to favour speed over energy")
    if runtime_pm_off:
        found.append(f"{len(runtime_pm_off)} devices may not power down: " + ", ".join(runtime_pm_off[:4]))
    if _read("/sys/module/pcie_aspm/parameters/policy").find("[powersave]") < 0 \
            and Path("/sys/module/pcie_aspm/parameters/policy").exists():
        found.append("PCI Express links are not set to save power when idle")
    for name, rate in sorted(interrupts.items(), key=lambda item: -item[1])[:3]:
        if rate >= 500 and not name.startswith(("LOC", "RES", "CAL", "TLB", "IWI")):
            found.append(f"{name} is interrupting {rate:.0f} times a second")
    if gpu_idle is not None and gpu_idle < 20:
        found.append(f"the graphics engine was only idle {gpu_idle:.0f}% of the time")
    if package_idle is not None and package_idle < 1 and draw_now.lid_closed:
        found.append("the processor package never reached its deepest idle state")
    return found


class Attributor:
    """Keeps the previous counters so each reading is a rate, not a total."""

    def __init__(self) -> None:
        self.when: float | None = None
        self.processes: dict[int, tuple[str, int, int]] = {}
        self.interrupts: dict[str, int] = {}
        self.devices: dict[str, int] = {}
        self.idle: dict[str, int] = {}
        self.package_idle: int = 0
        self.gpu_idle: int = 0

    def due(self, now: float) -> bool:
        return self.when is None or now - self.when >= ATTRIBUTION_SECONDS

    def read(self, now: float) -> Attribution | None:
        processes = _process_counters()
        interrupts = _interrupt_counters()
        devices = _wakeup_counters()
        idle = _idle_counters()
        package_idle = _int(CPUIDLE / "low_power_idle_cpu_residency_us")
        gpu_idle, gpu_mhz = _graphics()
        previous, self.when = self.when, now
        before = (self.processes, self.interrupts, self.devices, self.idle, self.package_idle, self.gpu_idle)
        self.processes, self.interrupts = processes, interrupts
        self.devices, self.idle = devices, idle
        self.package_idle, self.gpu_idle = package_idle, gpu_idle
        if previous is None:
            return None  # the first reading is only a baseline
        elapsed = max(now - previous, 1e-3)
        old_processes, old_interrupts, old_devices, old_idle, old_package, old_gpu = before

        wakeups: dict[str, float] = {}
        cpu: dict[str, float] = {}
        for pid, (name, nanoseconds, scheduled) in processes.items():
            was = old_processes.get(pid)
            if was is None or was[0] != name:
                continue
            woke = (scheduled - was[2]) / elapsed
            if woke >= WAKEUP_FLOOR:
                wakeups[name] = round(wakeups.get(name, 0.0) + woke, 1)
            busy = (nanoseconds - was[1]) / 1e7 / elapsed
            if busy >= 0.1:
                cpu[name] = round(cpu.get(name, 0.0) + busy, 2)

        rates = {name: round((count - old_interrupts[name]) / elapsed, 1)
                 for name, count in interrupts.items()
                 if name in old_interrupts and count > old_interrupts[name]}
        device_rates = {name: round((count - old_devices[name]) / elapsed, 2)
                        for name, count in devices.items()
                        if name in old_devices and count > old_devices[name]}
        processors = max(os.cpu_count() or 1, 1)
        residency = {name: round(100 * (micro - old_idle.get(name, 0)) / (elapsed * 1e6 * processors), 2)
                     for name, micro in idle.items()}
        package = round(100 * (package_idle - old_package) / (elapsed * 1e6), 3) if package_idle else None
        graphics = round(100 * (gpu_idle - old_gpu) / (elapsed * 1e6), 2) if gpu_idle else None
        stuck = _runtime_pm_off()
        return Attribution(
            wakeups=dict(sorted(wakeups.items(), key=lambda item: -item[1])[:20]),
            cpu=dict(sorted(cpu.items(), key=lambda item: -item[1])[:20]),
            interrupts=dict(sorted(rates.items(), key=lambda item: -item[1])[:15]),
            devices=dict(sorted(device_rates.items(), key=lambda item: -item[1])[:10]),
            idle_residency=residency,
            package_idle_percent=package,
            gpu_idle_percent=graphics,
            gpu_mhz=gpu_mhz,
            blockers=_blockers(draw(), package, graphics, rates, stuck),
        )


def sample(attributor: Attributor, now: float | None = None) -> PowerSample:
    """One reading. On mains power it stops after the battery's own status."""
    started = time.monotonic()
    now = time.time() if now is None else now
    present = draw()
    if not present.on_battery:
        return PowerSample(time=now, draw=present, cost_ms=round((time.monotonic() - started) * 1000, 3))
    attribution = attributor.read(now) if attributor.due(now) else None
    return PowerSample(time=now, draw=present, domains=domains(), attribution=attribution,
                       cost_ms=round((time.monotonic() - started) * 1000, 3))
