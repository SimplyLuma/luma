# SPDX-License-Identifier: Apache-2.0
"""Act on a background agent that keeps growing (ADR-026 §6).

Vitals only watched. On 2026-09-16 Messages' background part grew to 8.3 GB
overnight; Vitals said so every hour, nothing acted, memory and swap filled
and every app the person had open stalled. Now, when a background agent
passes a size no agent should reach and is still growing, Vitals restarts
that agent gracefully and tells the person once, in plain words.

Only background agents are ever touched, never an app the person is using:

- a unit luma-background runs in its ``luma-background*.slice`` (ADR-033);
- an autostarted program whose desktop entry declares
  ``X-Luma-Background-Agent``, the windowless-agent contract.

Anything else (an open app, a browser, a container) is only reported.
"""
from __future__ import annotations

import configparser
import os
import re
import subprocess
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from .detectors import GIB, Event
from .sampler import MachineSample, UnitSample

MIB = 1 << 20
#: A background agent above this share of memory, or this many bytes, whichever is smaller...
SHARE_OF_MEMORY = 0.15
LARGEST_BYTES = 2 * GIB
#: ...that grew by at least this much over the window is restarted.
GROWTH_WINDOW_SECONDS = 600
GROWTH_MIN_BYTES = 256 * MIB
GROWTH_MIN_RATIO = 0.10
#: One restart an hour per agent; if it is back that fast, say so and leave it to the memory limits.
RESTART_COOLDOWN_SECONDS = 3600

AUTOSTART_KEY = "X-Luma-Background-Agent"
BACKGROUND_SLICE = re.compile(r"(^|/)luma-background[^/]*\.slice(/|$)")
AUTOSTART_UNIT = re.compile(r"^app-gnome-(?P<id>.+)-(?P<instance>[0-9]+)\.(scope|service)$")


def unescape(name: str) -> str:
    r"""systemd unit-name escaping: org.projectluma.Messages\x2dbackground -> org.projectluma.Messages-background"""
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda match: chr(int(match.group(1), 16)), name)


def escape_dashes(name: str) -> str:
    return name.replace("-", "\\x2d")


def _config_dirs(kind: str) -> list[Path]:
    if kind == "autostart":
        home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        system = os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg"
        return [home / "autostart", *(Path(item) / "autostart" for item in system.split(":") if item)]
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [home / "applications", *(Path(item) / "applications" for item in system.split(":") if item)]


def desktop_entry(desktop_id: str, kind: str) -> dict[str, str] | None:
    """The [Desktop Entry] group of the first matching file, or None."""
    if "/" in desktop_id or not desktop_id:
        return None
    for directory in _config_dirs(kind):
        path = directory / f"{desktop_id}.desktop"
        if not path.is_file():
            continue
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        try:
            parser.read(path, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeDecodeError):
            return None
        return dict(parser["Desktop Entry"]) if parser.has_section("Desktop Entry") else None
    return None


def exec_argv(command: str) -> list[str]:
    """A desktop entry's Exec, without field codes (the agents' entries take no files or URLs)."""
    import shlex
    try:
        words = shlex.split(command)
    except ValueError:
        return []
    return [word for word in words if not re.fullmatch(r"%[fFuUdDnNickvm]", word)]


@dataclass(frozen=True)
class Agent:
    unit: str
    kind: str          # "managed" (luma-background) or "autostart"
    app_id: str
    name: str
    argv: tuple[str, ...] = ()
    desktop_id: str = ""


class Systemd:
    """The person's service manager, through systemctl, so every call is plain and logged."""

    def __init__(self, run=subprocess.run) -> None:
        self.run = run

    def _systemctl(self, *arguments: str) -> bool:
        try:
            # Waits for the stop, so a restarted agent never meets its old self
            # holding its D-Bus name; scopes and agents stop within 90 s.
            result = self.run(["systemctl", "--user", "--quiet", *arguments], capture_output=True, text=True,
                              timeout=120)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def environment(self, unit: str) -> dict[str, str]:
        try:
            result = self.run(["systemctl", "--user", "show", "--property=Environment", "--value", unit],
                              capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return {}
        values = {}
        for item in result.stdout.split():
            key, _, value = item.partition("=")
            values[key] = value
        return values

    def restart(self, agent: Agent) -> bool:
        if agent.kind == "managed":
            # Stop (SIGTERM, then the unit's own stop timeout) and start again.
            return self._systemctl("restart", agent.unit)
        if not agent.argv or not agent.desktop_id:
            return False
        # An autostarted agent has no unit to restart: end its scope the same
        # graceful way and start its entry's command again as a service of its own.
        stopped = self._systemctl("stop", agent.unit)
        # Named like the scope it replaces, so it is still recognised as this agent.
        unit = f"app-gnome-{escape_dashes(agent.desktop_id)}-{int(time.time())}.service"
        try:
            result = self.run(["systemd-run", "--user", "--quiet", "--collect", "--slice=app.slice",
                               f"--unit={unit}", "--property=Restart=on-failure", "--", *agent.argv],
                              capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        return stopped and result.returncode == 0


class Notifier:
    """One desktop notification through org.freedesktop.Notifications, with gdbus from GLib."""

    def __init__(self, run=subprocess.run) -> None:
        self.run = run

    def notify(self, summary: str, body: str) -> bool:
        try:
            result = self.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
                               "--object-path", "/org/freedesktop/Notifications",
                               "--method", "org.freedesktop.Notifications.Notify",
                               "Luma Vitals", "0", "utilities-system-monitor-symbolic", summary, body,
                               "[]", "{'urgency': <byte 1>}", "10000"],
                              capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0


def minutes(seconds: float) -> str:
    count = max(1, int(seconds // 60))
    return f"{count} minute" if count == 1 else f"{count} minutes"


def human_size(size: int) -> str:
    return f"{size / GIB:.1f} GB" if size >= GIB else f"{size // MIB} MB"


class RunawayAgents:
    def __init__(self, systemd: Systemd | None = None, notifier: Notifier | None = None) -> None:
        self.systemd = systemd or Systemd()
        self.notifier = notifier or Notifier()
        self.history: dict[str, deque] = defaultdict(deque)   # unit -> (time, footprint)
        self.restarted: dict[str, float] = {}                 # app id -> when
        self.told: set[str] = set()                           # app ids the person was told about
        self.reported: dict[str, float] = {}

    def identify(self, unit: UnitSample) -> Agent | None:
        if BACKGROUND_SLICE.search(unit.slice) and unit.unit.endswith(".service"):
            environment = self.systemd.environment(unit.unit)
            app_id = environment.get("LUMA_BACKGROUND_APP_ID") or unit.unit.rsplit(".", 1)[0]
            return Agent(unit.unit, "managed", app_id, self.app_name(app_id))
        match = AUTOSTART_UNIT.match(unit.unit)
        if not match:
            return None
        desktop_id = unescape(match.group("id"))
        entry = desktop_entry(desktop_id, "autostart")
        if not entry or not entry.get(AUTOSTART_KEY) or entry.get("Hidden", "").lower() == "true":
            return None
        argv = tuple(exec_argv(entry.get("Exec", "")))
        app_id = desktop_id.removesuffix(".Agent").removesuffix("-background")
        return Agent(unit.unit, "autostart", app_id, entry.get("Name") or self.app_name(app_id), argv, desktop_id)

    @staticmethod
    def app_name(app_id: str) -> str:
        entry = desktop_entry(app_id, "applications")
        return (entry or {}).get("Name") or app_id.rsplit(".", 1)[-1]

    @staticmethod
    def threshold(memory_total: int) -> int:
        return int(min(LARGEST_BYTES, memory_total * SHARE_OF_MEMORY)) if memory_total else LARGEST_BYTES

    def observe(self, sample: MachineSample) -> list[Event]:
        now, events = sample.time, []
        limit = self.threshold(sample.memory_total)
        present = set()
        for unit in sample.units:
            present.add(unit.unit)
            footprint = unit.memory + unit.swap
            history = self.history[unit.unit]
            history.append((now, footprint))
            while history and now - history[0][0] > GROWTH_WINDOW_SECONDS:
                history.popleft()
            if footprint < limit or now - history[0][0] < GROWTH_WINDOW_SECONDS * 0.9:
                continue
            grown = footprint - history[0][1]
            if grown < max(GROWTH_MIN_BYTES, footprint * GROWTH_MIN_RATIO):
                continue
            agent = self.identify(unit)
            if agent is None:
                continue
            events.extend(self._act(agent, unit, footprint, grown, now))
        for gone in set(self.history) - present:
            del self.history[gone]
        return events

    def _act(self, agent: Agent, unit: UnitSample, footprint: int, grown: int, now: float) -> list[Event]:
        details = {"app_id": agent.app_id, "kind": agent.kind, "memory": unit.memory, "swap": unit.swap,
                   "grown": grown, "window_seconds": GROWTH_WINDOW_SECONDS}
        last = self.restarted.get(agent.app_id)
        if last is not None and now - last < RESTART_COOLDOWN_SECONDS:
            if now - self.reported.get(agent.app_id, -1e12) < RESTART_COOLDOWN_SECONDS:
                return []
            self.reported[agent.app_id] = now
            return [Event("agent-still-growing", agent.unit,
                          f"{agent.name}'s background agent is growing again ({human_size(footprint)}) "
                          f"{minutes(now - last)} after it was restarted; left to its memory limit",
                          details)]
        restarted = self.systemd.restart(agent)
        self.restarted[agent.app_id] = now
        self.history.pop(agent.unit, None)
        summary = (f"Restarted {agent.name}'s background agent: it held {human_size(footprint)} "
                   f"and grew {human_size(grown)} in {minutes(GROWTH_WINDOW_SECONDS)}"
                   if restarted else
                   f"Could not restart {agent.name}'s background agent holding {human_size(footprint)}")
        events = [Event("agent-restarted" if restarted else "agent-restart-failed", agent.unit, summary,
                        {**details, "restarted": restarted})]
        if restarted and agent.app_id not in self.told:
            self.told.add(agent.app_id)
            self.notifier.notify(
                f"{agent.name} was restarted",
                f"{agent.name} was using {human_size(footprint)} of memory in the background and kept growing, "
                f"so Luma restarted it to keep this computer responsive. It's running again.")
        return events

