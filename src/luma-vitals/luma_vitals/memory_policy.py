# SPDX-License-Identifier: Apache-2.0
"""Luma's memory policy for apps (ADR-026 §7): warn, contain, and only then close.

On 2026-11-07 a 32 GB laptop filled its 8 GB of zram while 9 GB of RAM was
still reclaimable, and systemd-oomd, which ranks apps by swap alone, stopped
the universal-package container holding the most swap and four minutes later
the browser in use. This acts first, with what systemd-oomd cannot know:

- which app the person is using (the Shell says which has focus, when it
  answers Luma Vitals), and which they used in the last ten minutes;
- which app is growing, and how fast, from RAM and swap together.

What it does, in order:

1. Warn. An app that holds a quarter of memory, or keeps growing fast, is named
   in a notification that offers to close it ("Close <App>", "Keep Open"),
   updated in place only when it grows by half again.
2. Contain. A growing app the person is not using gets a soft ceiling at its
   current size, so its growth is reclaimed into swap instead of taking memory
   from every other app. Only while focus is known: otherwise the growing app
   may be the one in use.
3. Close. Only when memory and swap are both nearly full (before
   systemd-oomd's 90% limit) or the machine stalls on memory, the app that is
   running away, or else costs most (an idle app counting double), is named in
   a last warning with the same two buttons and, CLOSE_NOTICE_SECONDS later,
   stopped gracefully; the person is told once, by the app's name. Never the
   focused or recently used app, never an app the person chose to keep open,
   and never while there is headroom. Without focus only idle apps qualify.

systemd-oomd stays as the last resort. The focused app is marked
ManagedOOMPreference=avoid, which systemd-oomd honours for user units only in
its memory-pressure policy (systemd.resource-control(5)), not its swap policy:
acting before the swap limit is this module's job.
"""
from __future__ import annotations

import subprocess
from collections import defaultdict, deque
from dataclasses import dataclass, field

from .detectors import Event
from .names import AppName, describe
from .sampler import MachineSample, UnitSample

GIB = 1 << 30
MIB = 1 << 20

#: An app holding this share of memory (RAM and swap together) is worth a word.
WARN_SHARE = 0.25
WARN_MIN_BYTES = 2 * GIB
#: ...as is one that grew this much within GROWTH_SECONDS while holding GROWING_SHARE.
GROWTH_SECONDS = 600
GROWTH_SHARE = 0.05
GROWTH_MIN_BYTES = GIB
GROWING_SHARE = 0.10
#: Told again only after it grows by half since the last notice.
WARN_REGROWTH = 1.5
#: Before closing an app, the last warning stays up this long...
CLOSE_NOTICE_SECONDS = 15
#: ...and a warning older than this does not count.
CLOSE_NOTICE_FRESH_SECONDS = 600

#: Close something only when both are true (systemd-oomd acts at 90% and 90%)...
TIGHT_AVAILABLE_SHARE = 0.10
TIGHT_SWAP_SHARE = 0.80
#: ...or the machine is stalled on memory (PSI "full", avg10) whatever swap says,
STALL_FULL_PERCENT = 20.0
#: for this many consecutive samples.
TIGHT_SAMPLES = 2
#: Below these, sample every WATCH_INTERVAL seconds instead of every 15.
WATCH_AVAILABLE_SHARE = 0.20
WATCH_SWAP_SHARE = 0.60
WATCH_INTERVAL = 5.0
#: Give memory time to settle after closing something.
CLOSE_COOLDOWN_SECONDS = 30

#: Focused within this long counts as in use.
RECENT_SECONDS = 600
#: Apps smaller than this are never worth closing.
SMALLEST_BYTES = 256 * MIB
#: An app this quiet (share of one core) over IDLE_SECONDS counts as idle.
IDLE_CPU_PERCENT = 1.0
IDLE_SECONDS = 300
#: A ceiling never goes below this share of memory.
CONTAIN_FLOOR_SHARE = 0.10
#: What the image's drop-in gives every app (40-luma-app-memory.conf).
DEFAULT_CEILING = "60%"


def human(size: float) -> str:
    return f"{size / GIB:.0f} GB" if size >= 10 * GIB else f"{size / GIB:.1f} GB" if size >= GIB \
        else f"{int(size // MIB)} MB"


class Systemd:
    """The person's service manager. Every call is plain systemctl, logged by systemd itself."""

    def __init__(self, run=subprocess.run) -> None:
        self.run = run

    def _systemctl(self, *arguments: str, timeout: int = 30) -> bool:
        try:
            result = self.run(["systemctl", "--user", "--quiet", *arguments], capture_output=True, text=True,
                              timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def stop(self, unit: str) -> bool:
        # SIGTERM to every process, then the unit's own stop timeout: an app
        # closes the way it does at logout. --no-block: never hold the loop.
        return self._systemctl("--no-block", "stop", unit)

    def set_property(self, unit: str, **properties: str) -> bool:
        return self._systemctl("set-property", "--runtime", unit,
                               *(f"{key}={value}" for key, value in properties.items()))


@dataclass
class Tracked:
    unit: str              # the systemd unit (libpod-<id>.scope for a container)
    label: str             # the unit as sampled (<name>.container for a container)
    app: AppName
    history: deque = field(default_factory=deque)   # (time, footprint, cpu_usec)
    last_focused: float | None = None
    warned_at: float | None = None
    #: When the person was first told in this run of notices (they may close it from then on).
    first_warned_at: float = 0.0
    warned_size: int = 0
    notice: int = 0
    contained: int = 0
    kept: bool = False
    avoid: bool = False
    closed: bool = False


class MemoryPolicy:
    def __init__(self, bus, systemd: Systemd | None = None, describe_unit=describe) -> None:
        self.bus = bus
        self.systemd = systemd or Systemd()
        self.describe = describe_unit
        self.apps: dict[str, Tracked] = {}
        self.tight_count = 0
        self.last_close = -1e12
        self.focus_known = False

    # -- what is an app --------------------------------------------------

    @staticmethod
    def is_app(unit: UnitSample) -> bool:
        top = unit.slice.split("/", 1)[0] if unit.slice else ""
        if top != "app.slice":
            return False
        name = unit.unit
        return name.startswith(("app-", "dbus-:")) or name.endswith(".container") or \
            unit.systemd_unit.startswith("libpod-") and not unit.systemd_unit.startswith("libpod-conmon-")

    # -- the loop --------------------------------------------------------

    def interval(self, sample: MachineSample, normal: float) -> float:
        total = sample.memory_total or 1
        swap_share = (sample.swap_total - sample.swap_free) / sample.swap_total if sample.swap_total else 0.0
        if sample.memory_available < total * WATCH_AVAILABLE_SHARE or swap_share > WATCH_SWAP_SHARE:
            return WATCH_INTERVAL
        return normal

    def observe(self, sample: MachineSample) -> list[Event]:
        now = sample.time
        focused = self.bus.focused_apps()
        self.focus_known = focused is not None
        present = set()
        events: list[Event] = []
        for unit in sample.units:
            if not self.is_app(unit):
                continue
            present.add(unit.unit)
            tracked = self.apps.get(unit.unit)
            if tracked is None:
                tracked = self.apps[unit.unit] = Tracked(unit.systemd_unit, unit.unit, self.describe(unit.unit))
            footprint = unit.memory + unit.swap
            tracked.history.append((now, footprint, unit.cpu_usec))
            while tracked.history and now - tracked.history[0][0] > GROWTH_SECONDS:
                tracked.history.popleft()
            if focused and tracked.app.app_id and tracked.app.app_id in focused:
                tracked.last_focused = now
            self._protect(tracked, now)
            events += self._warn(tracked, unit, sample, now)
        for gone in set(self.apps) - present:
            tracked = self.apps.pop(gone)
            # A warning about an app that is gone says nothing any more; the
            # notice that it was closed stays until the person reads it.
            if tracked.notice and not tracked.closed:
                self.bus.withdraw(tracked.notice)
        events += self._reclaim(sample, now)
        return events

    # -- protect what the person is using ---------------------------------

    def recent(self, tracked: Tracked, now: float) -> bool:
        return tracked.last_focused is not None and now - tracked.last_focused <= RECENT_SECONDS

    def _protect(self, tracked: Tracked, now: float) -> None:
        want = self.recent(tracked, now)
        if want != tracked.avoid:
            # Once per change: a unit that refuses is not asked every sample.
            self.systemd.set_property(tracked.unit, ManagedOOMPreference="avoid" if want else "none")
            tracked.avoid = want

    # -- warn and contain ---------------------------------------------------

    def growth(self, tracked: Tracked) -> int:
        if len(tracked.history) < 2:
            return 0
        return max(0, tracked.history[-1][1] - min(entry[1] for entry in tracked.history))

    def cpu_percent(self, tracked: Tracked, now: float, seconds: float = IDLE_SECONDS) -> float | None:
        window = [entry for entry in tracked.history if now - entry[0] <= seconds]
        if len(window) < 2 or window[-1][0] - window[0][0] < seconds * 0.5:
            return None
        used = window[-1][2] - window[0][2]
        return max(0, used) / 1e6 / (window[-1][0] - window[0][0]) * 100

    def _warn(self, tracked: Tracked, unit: UnitSample, sample: MachineSample, now: float) -> list[Event]:
        total = sample.memory_total or 1
        footprint = unit.memory + unit.swap
        grown = self.growth(tracked)
        large = footprint >= max(WARN_MIN_BYTES, total * WARN_SHARE)
        growing = grown >= max(GROWTH_MIN_BYTES, total * GROWTH_SHARE) and footprint >= total * GROWING_SHARE
        if not (large or growing) or tracked.kept:
            return []
        if tracked.warned_at is not None and footprint < tracked.warned_size * WARN_REGROWTH:
            return self._contain(tracked, unit, sample, now, growing)
        name = tracked.app.name
        body = (f"{name} is using {human(footprint)} of memory and still growing. "
                if growing else f"{name} is using {human(footprint)} of memory. ")
        body += "Closing it frees that memory for your other apps."
        tracked.notice = self.bus.notify(f"{name} is using a lot of memory", body, icon=tracked.app.icon,
                                         actions=("close", f"Close {name}", "keep", "Keep Open"),
                                         replaces=tracked.notice)
        self._told(tracked, footprint, now)
        events = [Event("memory-warning", tracked.label,
                        f"{name} holds {human(footprint)}" + (f", grew {human(grown)} in "
                                                              f"{GROWTH_SECONDS // 60} minutes" if growing else ""),
                        {"unit": tracked.unit, "memory": unit.memory, "swap": unit.swap, "grown": grown,
                         "focused": self.recent(tracked, now)})]
        return events + self._contain(tracked, unit, sample, now, growing)

    @staticmethod
    def _told(tracked: Tracked, footprint: int, now: float) -> None:
        if tracked.warned_at is None or now - tracked.warned_at > CLOSE_NOTICE_FRESH_SECONDS:
            tracked.first_warned_at = now
        tracked.warned_at, tracked.warned_size = now, footprint

    def _contain(self, tracked: Tracked, unit: UnitSample, sample: MachineSample, now: float,
                 growing: bool) -> list[Event]:
        if not growing or tracked.contained or not self.focus_known or self.recent(tracked, now):
            return []
        ceiling = max(unit.memory, int(sample.memory_total * CONTAIN_FLOOR_SHARE))
        ceiling = -(-ceiling // (64 * MIB)) * 64 * MIB
        if not self.systemd.set_property(tracked.unit, MemoryHigh=str(ceiling)):
            return []
        tracked.contained = ceiling
        return [Event("memory-contained", tracked.label,
                      f"{tracked.app.name} held to {human(ceiling)} of RAM while it keeps growing",
                      {"unit": tracked.unit, "memory_high": ceiling})]

    # -- the person's answer -----------------------------------------------

    def answer(self, notice: int, key: str) -> list[Event]:
        tracked = next((t for t in self.apps.values() if notice and t.notice == notice), None)
        if tracked is None:
            return []
        if key == "closed":
            tracked.notice = 0
            return []
        if key == "close":
            tracked.notice, tracked.closed = 0, True
            stopped = self.systemd.stop(tracked.unit)
            return [Event("memory-closed-by-person", tracked.label,
                          f"{tracked.app.name} closed from the memory warning",
                          {"unit": tracked.unit, "stopped": stopped})]
        if key == "keep":
            tracked.kept, tracked.notice = True, 0
            if tracked.contained and self.systemd.set_property(tracked.unit, MemoryHigh=DEFAULT_CEILING):
                tracked.contained = 0
            return [Event("memory-kept", tracked.label, f"{tracked.app.name} kept open from the memory warning",
                          {"unit": tracked.unit})]
        return []

    # -- close, as a last resort before systemd-oomd ----------------------

    def tight(self, sample: MachineSample) -> bool:
        total = sample.memory_total or 1
        swap_share = (sample.swap_total - sample.swap_free) / sample.swap_total if sample.swap_total else 1.0
        stalled = sample.pressure.get("memory", {}).get("full", 0.0) >= STALL_FULL_PERCENT
        return stalled or (sample.memory_available < total * TIGHT_AVAILABLE_SHARE and swap_share > TIGHT_SWAP_SHARE)

    def runaway(self, tracked: Tracked, total: int) -> bool:
        return bool(tracked.contained) or self.growth(tracked) >= max(GROWTH_MIN_BYTES, total * GROWTH_SHARE)

    def idle(self, tracked: Tracked, now: float) -> bool:
        cpu = self.cpu_percent(tracked, now)
        return cpu is not None and cpu < IDLE_CPU_PERCENT

    def rank(self, tracked: Tracked, now: float, total: int) -> tuple:
        """The runaway first, then the largest (RAM, swap and growth), an idle app counting double."""
        footprint = tracked.history[-1][1] if tracked.history else 0
        cost = footprint + 2 * self.growth(tracked)
        idle = self.idle(tracked, now)
        return (self.runaway(tracked, total), cost * 2 if idle else cost, idle)

    def candidates(self, now: float, total: int) -> list[Tracked]:
        result = []
        for tracked in self.apps.values():
            footprint = tracked.history[-1][1] if tracked.history else 0
            if footprint < SMALLEST_BYTES or tracked.kept or self.recent(tracked, now):
                continue
            if not self.focus_known and not self.idle(tracked, now):
                # Without the Shell, an app busy in the last five minutes may
                # be the one in use: only quiet apps are candidates.
                continue
            result.append(tracked)
        return sorted(result, key=lambda tracked: self.rank(tracked, now, total), reverse=True)

    def _reclaim(self, sample: MachineSample, now: float) -> list[Event]:
        self.tight_count = self.tight_count + 1 if self.tight(sample) else 0
        if self.tight_count < TIGHT_SAMPLES or now - self.last_close < CLOSE_COOLDOWN_SECONDS:
            return []
        for tracked in self.candidates(now, sample.memory_total):
            footprint = tracked.history[-1][1]
            warned = tracked.warned_at is not None and now - tracked.warned_at <= CLOSE_NOTICE_FRESH_SECONDS
            if not warned:
                return self._last_warning(tracked, footprint, now)
            if now - tracked.first_warned_at < CLOSE_NOTICE_SECONDS:
                return []
            if not self.systemd.stop(tracked.unit):
                continue
            self.last_close, self.tight_count = now, 0
            tracked.closed = True
            name = tracked.app.name
            tracked.notice = self.bus.notify(
                f"{name} was closed to free memory",
                f"It was using {human(footprint)} while you weren’t using it, and memory was full. "
                "You can open it again when you’re ready.",
                icon=tracked.app.icon, replaces=tracked.notice)
            swap_used = sample.swap_total - sample.swap_free
            return [Event("memory-reclaimed", tracked.label,
                          f"Closed {name} to free memory: it held {human(footprint)}; "
                          f"{human(sample.memory_available)} available, swap {human(swap_used)} of "
                          f"{human(sample.swap_total)}",
                          {"unit": tracked.unit, "footprint": footprint, "grown": self.growth(tracked),
                           "available": sample.memory_available, "swap_used": swap_used,
                           "rank": list(self.rank(tracked, now, sample.memory_total))})]
        return []

    def _last_warning(self, tracked: Tracked, footprint: int, now: float) -> list[Event]:
        """Name the app about to be closed, with time to keep it open."""
        name = tracked.app.name
        tracked.notice = self.bus.notify(
            f"{name} will be closed to free memory",
            f"Memory is almost full and {name} is using {human(footprint)}. "
            f"Keep it open if you’re still using it.",
            icon=tracked.app.icon, actions=("close", f"Close {name}", "keep", "Keep Open"),
            replaces=tracked.notice)
        self._told(tracked, footprint, now)
        return [Event("memory-warning", tracked.label, f"{name} holds {human(footprint)}; closing it in "
                      f"{CLOSE_NOTICE_SECONDS} s unless kept open",
                      {"unit": tracked.unit, "footprint": footprint, "last": True})]
