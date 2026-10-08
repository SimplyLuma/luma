# SPDX-License-Identifier: Apache-2.0
"""What counts as misbehaviour (ADR-026 §3). Each event explains itself with its numbers."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from .sampler import MachineSample

GIB = 1 << 30
RUNAWAY_CPU_PERCENT = 50.0
RUNAWAY_SECONDS = 180
GROWTH_BYTES = GIB
GROWTH_SECONDS = 600
IDLE_HOG_BYTES = GIB
IDLE_HOG_CPU_PERCENT = 2.0
IDLE_HOG_SECONDS = 600
PRESSURE_MEMORY_SOME = 10.0
SWAP_FULL_RATIO = 0.8
HOT_CELSIUS = 90.0
HOT_SECONDS = 120
REPEAT_SECONDS = 3600
HISTORY_SECONDS = 900


@dataclass(frozen=True)
class Event:
    kind: str
    unit: str
    summary: str
    details: dict


def friendly(unit: str) -> str:
    """app-gnome-org.mozilla.firefox-1234.scope -> org.mozilla.firefox"""
    name = unit.rsplit(".", 1)[0]
    if unit == "waydroid.lxc":
        return "Android apps"
    if unit.endswith(".container") and name.startswith("luma-run-"):
        # luma-run-rpm-chatgpt-25ec6b75b803 -> chatgpt
        _kind, _, rest = name[len("luma-run-"):].partition("-")
        head, _, tail = rest.rpartition("-")
        return head if head and len(tail) == 12 else rest
    for prefix in ("app-gnome-", "app-flatpak-", "app-"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            # Application scopes end in an instance number or id; services don't.
            head, _, tail = name.rpartition("-")
            if head and (tail.isdigit() or (len(tail) >= 8 and all(c in "0123456789abcdef" for c in tail))):
                name = head
            break
    return name


class Detectors:
    def __init__(self) -> None:
        self.history: dict[str, deque] = defaultdict(deque)  # unit -> (time, cpu%, memory)
        self.previous: dict[str, int] = {}
        self.previous_time: float | None = None
        self.hot_since: float | None = None
        self.last_fired: dict[tuple[str, str], float] = {}

    def cpu_percentages(self, sample: MachineSample) -> dict[str, float]:
        result = {}
        if self.previous_time is not None:
            elapsed = max(sample.time - self.previous_time, 1e-3)
            for unit in sample.units:
                before = self.previous.get(unit.unit)
                if before is not None and unit.cpu_usec >= before:
                    result[unit.unit] = (unit.cpu_usec - before) / 1e6 / elapsed * 100
        self.previous = {u.unit: u.cpu_usec for u in sample.units}
        self.previous_time = sample.time
        return result

    def _fire(self, events: list, now: float, kind: str, unit: str, summary: str, **details) -> None:
        key = (kind, unit)
        if now - self.last_fired.get(key, -1e12) < REPEAT_SECONDS:
            return
        self.last_fired[key] = now
        events.append(Event(kind, unit, summary, details))

    def observe(self, sample: MachineSample) -> tuple[list[Event], dict[str, float]]:
        now = sample.time
        cpu = self.cpu_percentages(sample)
        events: list[Event] = []
        present = set()
        for unit in sample.units:
            present.add(unit.unit)
            history = self.history[unit.unit]
            history.append((now, cpu.get(unit.unit, 0.0), unit.memory))
            while history and now - history[0][0] > HISTORY_SECONDS:
                history.popleft()
            name = friendly(unit.unit)

            recent = [h for h in history if now - h[0] <= RUNAWAY_SECONDS]
            if recent and now - recent[0][0] >= RUNAWAY_SECONDS * 0.9 and \
                    min(h[1] for h in recent[1:] or recent) > RUNAWAY_CPU_PERCENT:
                average = sum(h[1] for h in recent) / len(recent)
                self._fire(events, now, "runaway-cpu", unit.unit,
                           f"{name} has used {average:.0f}% of a core for {RUNAWAY_SECONDS // 60} minutes",
                           cpu_percent=round(average, 1), memory=unit.memory)

            window = [h for h in history if now - h[0] <= GROWTH_SECONDS]
            if window and now - window[0][0] >= GROWTH_SECONDS * 0.9:
                grown = unit.memory - window[0][2]
                if grown > GROWTH_BYTES:
                    self._fire(events, now, "memory-growth", unit.unit,
                               f"{name} grew by {grown / GIB:.1f} GB in {GROWTH_SECONDS // 60} minutes "
                               f"to {unit.memory / GIB:.1f} GB", grown=grown, memory=unit.memory)
            if sample.memory_total and unit.memory > sample.memory_total / 4:
                self._fire(events, now, "memory-share", unit.unit,
                           f"{name} holds {unit.memory / GIB:.1f} GB, a quarter of this computer's memory",
                           memory=unit.memory)

            idle = [h for h in history if now - h[0] <= IDLE_HOG_SECONDS]
            if idle and now - idle[0][0] >= IDLE_HOG_SECONDS * 0.9 and unit.memory > IDLE_HOG_BYTES and \
                    max(h[1] for h in idle) < IDLE_HOG_CPU_PERCENT:
                self._fire(events, now, "idle-hog", unit.unit,
                           f"{name} has held {unit.memory / GIB:.1f} GB for {IDLE_HOG_SECONDS // 60} minutes "
                           f"without doing anything", memory=unit.memory)

        for gone in set(self.history) - present:
            del self.history[gone]

        swap_used = sample.swap_total - sample.swap_free
        memory_some = sample.pressure.get("memory", {}).get("some", 0.0)
        if memory_some > PRESSURE_MEMORY_SOME or (sample.swap_total and swap_used > sample.swap_total * SWAP_FULL_RATIO):
            top = sorted(sample.units, key=lambda u: u.memory + u.swap, reverse=True)[:3]
            self._fire(events, now, "memory-pressure", "machine",
                       f"Memory is short ({sample.memory_available / GIB:.1f} GB free, swap "
                       f"{swap_used / GIB:.1f} of {sample.swap_total / GIB:.1f} GB); most held by "
                       + ", ".join(f"{friendly(u.unit)} {(u.memory + u.swap) / GIB:.1f} GB" for u in top),
                       available=sample.memory_available, swap_used=swap_used, stall_percent=memory_some,
                       top=[(u.unit, u.memory, u.swap) for u in top])

        if sample.temperature_c is not None and sample.temperature_c >= HOT_CELSIUS:
            self.hot_since = self.hot_since or now
            if now - self.hot_since >= HOT_SECONDS:
                top = sorted(cpu.items(), key=lambda item: item[1], reverse=True)[:3]
                self._fire(events, now, "heat", "machine",
                           f"Running at {sample.temperature_c:.0f}°C for {HOT_SECONDS // 60} minutes; busiest: "
                           + ", ".join(f"{friendly(u)} {p:.0f}%" for u, p in top),
                           celsius=sample.temperature_c, top=top)
        else:
            self.hot_since = None
        return events, cpu


#: A process waking the machine this often is doing something no desktop needs.
WAKEUP_STORM_PER_SECOND = 1500.0
#: Below this, a person should be told what is emptying the battery.
SHORT_RUNTIME_HOURS = 1.5


class PowerDetectors:
    """What is worth saying out loud about a machine running on battery.

    Nothing here fires on mains power, and nothing fires twice in an hour: a
    laptop that tells you about its battery every minute is worse than one
    that says nothing.
    """

    def __init__(self) -> None:
        self.last_fired: dict[tuple[str, str], float] = {}

    def _fire(self, events: list, now: float, kind: str, unit: str, summary: str, **details) -> None:
        key = (kind, unit)
        if now - self.last_fired.get(key, -1e12) < REPEAT_SECONDS:
            return
        self.last_fired[key] = now
        events.append(Event(kind, unit, summary, details))

    def observe(self, power, runtime_hours: float | None = None) -> list[Event]:
        events: list[Event] = []
        if not power.draw.on_battery:
            return events
        now = power.time
        attribution = power.attribution
        if attribution is None:
            return events

        for blocker in attribution.blockers:
            self._fire(events, now, "power-blocker", "machine", blocker.capitalize(),
                       watts=power.draw.watts, domains=power.domains)

        for name, rate in attribution.wakeups.items():
            if rate >= WAKEUP_STORM_PER_SECOND:
                self._fire(events, now, "wakeup-storm", name,
                           f"{name} woke the machine {rate:.0f} times a second on battery",
                           wakeups_per_second=rate)
                break

        if runtime_hours is not None and 0 < runtime_hours < SHORT_RUNTIME_HOURS and power.draw.watts:
            busiest = ", ".join(f"{name} {percent:.0f}%"
                                for name, percent in list(attribution.cpu.items())[:3])
            self._fire(events, now, "battery-short", "machine",
                       f"About {runtime_hours:.1f} h of battery left at {power.draw.watts:.0f} W"
                       + (f"; busiest: {busiest}" if busiest else ""),
                       watts=power.draw.watts, runtime_hours=runtime_hours, top=busiest)
        return events
