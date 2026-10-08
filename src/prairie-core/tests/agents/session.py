# SPDX-License-Identifier: Apache-2.0
"""Shared pieces of the ADR-033 agent session tests.

These run as an ordinary user inside a real systemd user session (a Fedora 44
container booted with systemd, the user lingering): the user manager, its
D-Bus broker, xdg-desktop-portal and xdg autostart are the real ones. Only the
notification server/screen lock (notification_stub.py) and, for a simulated
suspend, logind's PrepareForSleep (fake_login1.py via sudo) are stand-ins.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

HERE = Path(__file__).resolve().parent
AGENT_INTERFACE = "org.projectluma.BackgroundAgent1"
AGENT_PATH = "/org/projectluma/BackgroundAgent1"


class Failure(AssertionError):
    pass


ESSENTIAL_CATEGORIES = {"communication", "calendar", "alarms"}


class Session:
    def __init__(self, evidence: Path) -> None:
        # With luma-background installed the service starts agents in the units
        # it generates; without it the autostart entries are the fallback.
        self.managed = Path("/usr/libexec/luma-background-service").exists()
        self.evidence = evidence
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.notifications = evidence / "notifications.jsonl"
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.results: list[dict] = []
        self.log = (evidence / "steps.log").open("a")

    # -- recording -----------------------------------------------------------
    def step(self, name: str, ok: bool, **details) -> None:
        entry = {"step": name, "ok": bool(ok), "time": round(time.time(), 3), **details}
        self.results.append(entry)
        self.log.write(json.dumps(entry) + "\n")
        self.log.flush()
        print(("PASS " if ok else "FAIL ") + name + (f" {json.dumps(details)}" if details else ""), flush=True)
        if not ok:
            raise Failure(f"{name}: {details}")

    def note(self, text: str) -> None:
        self.log.write(json.dumps({"note": text, "time": round(time.time(), 3)}) + "\n")
        self.log.flush()
        print("     " + text, flush=True)

    # -- agents ------------------------------------------------------------------
    def agent_unit(self, app_id: str) -> str:
        return f"app-{app_id}-agent.service" if self.managed else f"app-{app_id}.Agent@autostart.service"

    @staticmethod
    def agent_slice(category: str) -> str:
        return "luma-background-essential.slice" if category in ESSENTIAL_CATEGORIES else "luma-background-deferrable.slice"

    def limits_step(self, unit: str, category: str, memory_max_mib: int = 128) -> None:
        if not self.managed:
            self.note(f"unmanaged fallback: {unit} runs without luma-background's limits")
            return
        environment = self.unit_property(unit, "Environment")
        self.step("runs under luma-background's generated unit and limits",
                  self.unit_property(unit, "Slice") == self.agent_slice(category)
                  and self.unit_property(unit, "MemoryMax") == str(memory_max_mib * 1048576)
                  and self.unit_property(unit, "CPUWeight") == "20" and "LUMA_BACKGROUND_MANAGED=1" in environment,
                  unit_file=self.unit_property(unit, "FragmentPath"), **self.memory(unit))

    def launch_window(self, app_id: str, name: str, *argv: str, env: dict | None = None) -> str:
        """Start an app's window the way the shell does, in a unit named for the app (luma-background identifies it)."""
        unit = f"app-luma-{app_id}@{name}.service"
        self.systemctl("stop", unit, check=False)
        settings = [f"--setenv={key}={value}" for key, value in (env or {}).items()]
        self.run("systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}", *settings,
                 "xvfb-run", "-a", "-s", "-screen 0 1280x800x24", *argv)
        return unit

    # -- processes and units ---------------------------------------------------
    def run(self, *command: str, check: bool = True, timeout: float = 60, env: dict | None = None,
            input: str | None = None) -> subprocess.CompletedProcess:
        result = subprocess.run(list(command), capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, **(env or {})}, input=input)
        if check and result.returncode != 0:
            raise Failure(f"{' '.join(command)} exited {result.returncode}: {result.stderr.strip()[-800:]}")
        return result

    def systemctl(self, *args: str, check: bool = True) -> str:
        return self.run("systemctl", "--user", *args, check=check).stdout.strip()

    def unit_property(self, unit: str, name: str) -> str:
        return self.systemctl("show", "-p", name, "--value", unit, check=False)

    def unit_active(self, unit: str) -> bool:
        return self.systemctl("is-active", unit, check=False) == "active"

    def unit_pids(self, unit: str) -> list[int]:
        group = self.unit_property(unit, "ControlGroup")
        if not group:
            return []
        try:
            return [int(pid) for pid in Path(f"/sys/fs/cgroup{group}/cgroup.procs").read_text().split()]
        except OSError:
            return []

    def memory(self, unit: str) -> dict:
        def number(name):
            value = self.unit_property(unit, name)
            return int(value) if value.isdigit() else None
        return {"current": number("MemoryCurrent"), "peak": number("MemoryPeak"),
                "high": self.unit_property(unit, "MemoryHigh"), "max": self.unit_property(unit, "MemoryMax"),
                "slice": self.unit_property(unit, "Slice")}

    @staticmethod
    def rss(pid: int) -> int:
        try:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except OSError:
            pass
        return 0

    @staticmethod
    def smaps(pid: int) -> dict:
        """Resident, proportional (shared libraries split between processes) and private anonymous memory, MiB."""
        values = {}
        try:
            for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines()[1:]:
                key, _, rest = line.partition(":")
                values[key.strip()] = int(rest.split()[0]) * 1024
        except (OSError, ValueError, IndexError):
            return {}
        return {"rss_mib": mib(values.get("Rss")), "pss_mib": mib(values.get("Pss")),
                "anonymous_mib": mib(values.get("Anonymous"))}

    @staticmethod
    def cmdline(pid: int) -> str:
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode().strip()
        except OSError:
            return ""

    @staticmethod
    def loads_gtk(pid: int) -> bool:
        try:
            maps = Path(f"/proc/{pid}/maps").read_text()
        except OSError:
            return False
        return "libgtk-4" in maps or "libgtk-3" in maps or "libadwaita" in maps

    def processes(self, needle: str) -> list[int]:
        found = []
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit() and needle in self.cmdline(int(entry.name)):
                found.append(int(entry.name))
        return sorted(found)

    # -- D-Bus -----------------------------------------------------------------
    def has_owner(self, name: str) -> bool:
        return bool(self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                       "NameHasOwner", GLib.Variant("(s)", (name,)), GLib.VariantType("(b)"),
                                       Gio.DBusCallFlags.NONE, 3000, None).unpack()[0])

    def owner(self, name: str) -> str:
        try:
            return self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                      "GetNameOwner", GLib.Variant("(s)", (name,)), GLib.VariantType("(s)"),
                                      Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        except GLib.Error:
            return ""

    def call(self, name: str, path: str, interface: str, method: str, parameters: GLib.Variant | None = None,
             timeout: int = 10000):
        result = self.bus.call_sync(name, path, interface, method, parameters, None, Gio.DBusCallFlags.NONE,
                                    timeout, None)
        return result.unpack() if result is not None else ()

    def agent_property(self, name: str, prop: str):
        return self.call(name, AGENT_PATH, "org.freedesktop.DBus.Properties", "Get",
                         GLib.Variant("(ss)", (AGENT_INTERFACE, prop)))[0]

    def values(self, name: str) -> dict:
        """An agent's published values (org.projectluma.BackgroundAgent1.Values)."""
        try:
            return dict(self.agent_property(name, "Values"))
        except GLib.Error:
            return {}

    def journal_grep(self, unit: str, text: str) -> list[str]:
        output = self.run("journalctl", "--user", "--no-pager", "-o", "cat", "-u", unit, check=False).stdout
        return [line for line in output.splitlines() if text in line][-3:]

    def invoke(self, notification_id: int, action: str) -> None:
        self.call("org.projectluma.Test", "/org/projectluma/Test", "org.projectluma.Test.Notifications", "Invoke",
                  GLib.Variant("(us)", (notification_id, action)))

    def lock(self, locked: bool) -> None:
        self.call("org.projectluma.Test", "/org/projectluma/Test", "org.projectluma.Test.Notifications", "Lock",
                  GLib.Variant("(b)", (locked,)))

    # -- notifications -----------------------------------------------------------
    def notification_records(self) -> list[dict]:
        if not self.notifications.exists():
            return []
        return [json.loads(line) for line in self.notifications.read_text().splitlines() if line.strip()]

    def wait_notification(self, predicate, what: str, timeout: float = 30, after: int = 0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for index, record in enumerate(self.notification_records()):
                if index >= after and record.get("event") == "notify" and predicate(record):
                    return record
            time.sleep(0.2)
        raise Failure(f"no notification: {what}")

    def mark(self) -> int:
        return len(self.notification_records())

    # -- waiting -----------------------------------------------------------------
    @staticmethod
    def wait(predicate, what: str, timeout: float = 30, interval: float = 0.2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(interval)
        raise Failure(f"timed out: {what}")

    # -- session events -------------------------------------------------------------
    SESSION_TARGET = "luma-test-session.target"

    def login(self) -> None:
        """What a GNOME/Phosh login does: the session target binds graphical-session.target and
        wants xdg-desktop-autostart.target, whose generated units start every autostart entry."""
        units = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user"
        units.mkdir(parents=True, exist_ok=True)
        (units / self.SESSION_TARGET).write_text(
            "[Unit]\nDescription=Test graphical login\nBindsTo=graphical-session.target\n"
            "Wants=graphical-session-pre.target graphical-session.target xdg-desktop-autostart.target\n")
        self.systemctl("daemon-reload")
        self.logout()
        self.systemctl("start", self.SESSION_TARGET)

    def logout(self) -> None:
        self.systemctl("stop", self.SESSION_TARGET, "graphical-session.target", "xdg-desktop-autostart.target", check=False)

    def suspend(self) -> None:
        """PrepareForSleep(true) from the name org.freedesktop.login1, with the real logind held off."""
        self.run("sudo", "-n", "systemctl", "mask", "--runtime", "--now", "systemd-logind.service")
        self.run("sudo", "-n", sys.executable, str(HERE / "fake_login1.py"), "sleep")

    def restore_logind(self) -> None:
        self.run("sudo", "-n", "systemctl", "unmask", "--runtime", "systemd-logind.service", check=False)
        self.run("sudo", "-n", "systemctl", "start", "systemd-logind.service", check=False)

    def resume(self) -> None:
        self.run("sudo", "-n", sys.executable, str(HERE / "fake_login1.py"), "resume")

    def failed(self, failure: Exception) -> None:
        self.results.append({"step": "unexpected failure", "ok": False, "error": str(failure)[:2000]})
        print(f"FAIL {failure}", flush=True)

    def summary(self, name: str, extra: dict | None = None) -> dict:
        summary = {"suite": name, "passed": all(item["ok"] for item in self.results), "steps": self.results,
                   **(extra or {})}
        (self.evidence / "summary.json").write_text(json.dumps(summary, indent=2))
        return summary


def mib(value: int | None) -> float | None:
    return None if value is None else round(value / 1048576, 1)
