# SPDX-License-Identifier: Apache-2.0
"""Crash evidence (ADR-026 §6): a boot that ended without shutting down, a kernel
crash record left in pstore, and a process of this person's that dumped core.

Everything here is read from what the system already keeps — the journal,
systemd-coredump's journal entries and systemd-pstore's archive — so the
evidence exists whether or not Vitals was running when it happened. Vitals
only notices it, names it and keeps it with its other events.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .detectors import Event, friendly

#: SD_MESSAGE_SHUTDOWN, "System is rebooting." / "System is powering down.".
#: systemd-logind logs it when a shutdown is requested through it (every
#: desktop, `systemctl reboot`, `shutdown -r`); PID 1 logs it only for a
#: shutdown that bypasses logind, and usually after journald has stopped. So
#: it is matched from any sender, never from PID 1 alone.
SHUTDOWN_MESSAGE_ID = "98268866d1d54a499c4e98921d93bc40"
#: logind's "The system will reboot now!" is not used: it carries the same
#: MESSAGE_ID as "The system will suspend now!".
#: PID 1 lines that only an orderly shutdown transaction produces. The journal
#: stops recording once /var is unmounted, so the final steps (systemd-shutdown
#: itself, "Reached target reboot.target") are often missing; what is left is
#: PID 1 stopping the early-boot targets and the boot/shutdown recorder, which
#: follow ostree-finalize-staged. Stopping ostree-finalize-staged alone is not
#: evidence: discarding a staged update stops it too.
PID1_SHUTDOWN_MARKERS = (
    "Reached target shutdown.target", "Reached target reboot.target", "Reached target poweroff.target",
    "Reached target halt.target", "Reached target kexec.target", "Reached target final.target",
    "Reached target soft-reboot.target", "Stopped target sysinit.target", "Stopped target basic.target",
    "Stopped target local-fs.target", "Stopped systemd-update-utmp.service", "systemd-shutdown",
)
#: How many of PID 1's last records are searched for those lines.
PID1_TAIL = 400
#: The last records of a boot that went to sleep and never woke: a battery
#: that ran flat, or power lost while suspended.
SLEEP_MARKERS = ("PM: suspend entry", "PM: hibernation entry", "Entering sleep state",
                 "Performing sleep operation", "Reached target sleep.target")
#: The system's login accounting file; systemd-update-utmp writes a
#: "shutdown" record into it when an orderly shutdown stops the service.
WTMP = Path("/var/log/wtmp")
UTMP_RECORD = 384        # sizeof(struct utmp) on 64-bit Linux (x86_64 and AArch64)
UTMP_RUN_LVL = 1
UTMP_BOOT_TIME = 2
#: systemd-coredump's "Process N (name) of user N dumped core." entry.
COREDUMP_MESSAGE_ID = "fc2e22bc6ee647b6b90729ab34a250b1"
PSTORE_ARCHIVE = Path("/var/lib/systemd/pstore")
#: Where luma-crash-evidence.service keeps the firmware's boot error record.
FIRMWARE_ARCHIVE = Path("/var/lib/luma/crash")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
#: A crash loop must not become a flood: past this many in one check, the rest
#: are counted in one summary event.
MAX_CRASH_EVENTS = 5
#: luma-capsule-launch's "<application> couldn't open" entry (luma_installer.launch_guard).
LAUNCH_FAILURE_MESSAGE_ID = "5b3c0f1e9d7a4c2b8e6f41a07d93c2e5"
LAUNCH_FAILURE_FIELDS = ("LUMA_APPLICATION_ID", "LUMA_APPLICATION_NAME", "LUMA_LAUNCH_FAILURE", "LUMA_MISSING",
                         "LUMA_EXIT_STATUS", "LUMA_SIGNAL", "LUMA_SECONDS", "LUMA_STDERR")
CHECK_SECONDS = 120

Runner = Callable[[list[str]], str]


def run(arguments: list[str]) -> str:
    """Run a read-only query; an unavailable tool or journal reads as no output."""
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def _json_lines(text: str) -> list[dict]:
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            entries.append(value)
        elif isinstance(value, list):
            entries.extend(item for item in value if isinstance(item, dict))
    return entries


def _field(entry: dict, name: str) -> str:
    value = entry.get(name)
    if isinstance(value, list):  # journald repeats a field as a list
        value = value[0] if value else ""
    return value if isinstance(value, str) else ""


def signal_name(value: str) -> str:
    if value.isdigit():
        try:
            return signal.Signals(int(value)).name
        except ValueError:
            return f"signal {value}"
    return value or "a signal"


@dataclass(frozen=True)
class BootVerdict:
    boot_id: str
    clean: bool | None           # None: this person cannot read the system journal
    last_entry_usec: int
    last_sources: tuple[str, ...]
    markers: tuple[str, ...] = ()    # the evidence of an orderly shutdown that was found
    asleep: bool = False             # the boot's last records are it going to sleep


def wtmp_records(path: Path = WTMP) -> list[tuple[str, float]]:
    """The boot ("reboot") and orderly-shutdown ("shutdown") records in wtmp, in order."""
    try:
        data = path.read_bytes()
    except OSError:
        return []
    found = []
    for offset in range(0, len(data) - UTMP_RECORD + 1, UTMP_RECORD):
        record = data[offset:offset + UTMP_RECORD]
        kind = int.from_bytes(record[0:2], "little", signed=True)
        user = record[44:76].split(b"\0", 1)[0]
        if (kind, user) in ((UTMP_BOOT_TIME, b"reboot"), (UTMP_RUN_LVL, b"shutdown")):
            seconds = int.from_bytes(record[340:344], "little", signed=True)
            micros = int.from_bytes(record[344:348], "little", signed=True)
            found.append((user.decode(), seconds + micros / 1e6))
    return found


def wtmp_shutdown(last_entry: float, path: Path = WTMP) -> float | None:
    """When the boot whose last journal record is at ``last_entry`` recorded its
    orderly shutdown in wtmp, or None.

    Like `last -x`, the shutdown record is paired with the boot record before
    it, so one from an older boot never vouches for this one.
    """
    records = wtmp_records(path)
    boots = [i for i, (kind, t) in enumerate(records) if kind == "reboot" and t <= last_entry]
    if not boots:
        return None
    for kind, t in records[boots[-1] + 1:]:
        if kind == "reboot":
            return None
        if kind == "shutdown":
            return t
    return None


def _shutdown_markers(entries: list[dict]) -> list[str]:
    found = []
    for entry in entries:
        message_id = _field(entry, "MESSAGE_ID")
        message = _field(entry, "MESSAGE")
        if message_id == SHUTDOWN_MESSAGE_ID:
            found.append(message or message_id)
        elif _field(entry, "_PID") == "1" and any(m in message for m in PID1_SHUTDOWN_MARKERS):
            found.append(message)
    return found


def previous_boot(runner: Runner = run, wtmp: Path = WTMP) -> BootVerdict | None:
    """How the boot before this one ended, or None when there was none.

    It ended in an orderly way when any of the system's own shutdown records is
    there: logind's (or PID 1's) shutdown message, PID 1 stopping the early-boot
    targets, or systemd-update-utmp's shutdown record in wtmp. Only a boot with
    none of them stopped without shutting down: power was lost, the machine
    was reset or forced off, or the kernel hung or panicked.
    """
    boots = _json_lines(runner(["journalctl", "--list-boots", "-o", "json", "--no-pager"]))
    if len(boots) < 2:
        return None
    previous = boots[-2]
    boot_id = str(previous.get("boot_id") or "")
    if not boot_id:
        return None
    last_entry = int(previous.get("last_entry") or 0)
    fields = "--output-fields=MESSAGE_ID,MESSAGE,_PID,SYSLOG_IDENTIFIER,_COMM"
    tail = _json_lines(runner(["journalctl", "-b", boot_id, "-n", "20", "-o", "json", "--no-pager", fields]))
    sources = tuple(_field(e, "SYSLOG_IDENTIFIER") or _field(e, "_COMM") or "?" for e in tail[-3:])
    asleep = any(m in _field(e, "MESSAGE") for e in tail[-8:] for m in SLEEP_MARKERS)
    manager = _json_lines(runner(["journalctl", "-b", boot_id, "_PID=1", "-n", str(PID1_TAIL), "-o", "json",
                                  "--no-pager", fields]))
    if not manager:
        # Without read access to the system's records a missing shutdown
        # message proves nothing, so no verdict is given.
        return BootVerdict(boot_id, None, last_entry, sources)
    requested = _json_lines(runner(["journalctl", "-b", boot_id, f"MESSAGE_ID={SHUTDOWN_MESSAGE_ID}",
                                    "-o", "json", "--no-pager", fields]))
    markers = _shutdown_markers(requested) + _shutdown_markers(manager)
    recorded = wtmp_shutdown(last_entry / 1e6, wtmp) if last_entry else None
    if recorded is not None:
        markers.append(f"wtmp shutdown record at {int(recorded)}")
    unique = tuple(dict.fromkeys(markers))
    return BootVerdict(boot_id, bool(unique), last_entry, sources, unique[:6], asleep and not unique)


def pstore_records(since: float, root: Path = PSTORE_ARCHIVE) -> list[str]:
    """Names of kernel crash records systemd-pstore archived after ``since``."""
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    found = []
    for child in children:
        try:
            if child.stat().st_mtime >= since:
                found.append(child.name)
        except OSError:
            continue
    return sorted(found)


class CrashWatch:
    """Turns the system's own crash evidence into Vitals events, each exactly once."""

    def __init__(self, state, runner: Runner = run, uid: int | None = None,
                 pstore_root: Path = PSTORE_ARCHIVE, firmware_root: Path = FIRMWARE_ARCHIVE,
                 boot_id: str | None = None, wtmp: Path = WTMP) -> None:
        self.state = state          # get(key) -> str | None, set(key, value)
        self.runner = runner
        self.uid = os.getuid() if uid is None else uid
        self.pstore_root = pstore_root
        self.firmware_root = firmware_root
        self.wtmp = wtmp
        if boot_id is None:
            try:
                boot_id = BOOT_ID.read_text().strip().replace("-", "")
            except OSError:
                boot_id = ""
        self.boot_id = boot_id

    def firmware_events(self) -> list[Event]:
        """The firmware's own record of a hardware error, captured at this boot."""
        if not self.boot_id or self.state.get("firmware-record-checked") == self.boot_id:
            return []
        summary_path = self.firmware_root / self.boot_id / "summary.json"
        try:
            summary = json.loads(summary_path.read_text())
        except (OSError, ValueError):
            # The capture runs early in boot; look again at the next check.
            return []
        self.state.set("firmware-record-checked", self.boot_id)
        if not isinstance(summary, dict) or summary.get("new") is not True:
            return []
        details = {"boot_id": self.boot_id, "sha256": summary.get("sha256"), "bytes": summary.get("data_bytes")}
        crashlog = self._crashlog(summary_path.parent, summary.get("sha256"))
        if crashlog:
            tags = [t for t in crashlog.get("triage", []) if isinstance(t, str)]
            named = [p for p in crashlog.get("instruction_pointers", []) if isinstance(p, dict) and p.get("module")]
            text = "The computer restarted unexpectedly: the processor's Crash Log reports "
            text += ", ".join(tags) if tags else "no cause iclg recognises"
            if named:
                text += f"; last instruction in {named[0]['module']}+{named[0].get('offset', '?')}"
            details.update({"triage": tags, "instruction_pointers": crashlog.get("instruction_pointers", [])})
            return [Event("firmware-crash-record", "machine", f"{text}; kept in {summary_path.parent}", details)]
        return [Event(
            "firmware-crash-record", "machine",
            "The firmware recorded a hardware error before this boot, which is what a machine that "
            f"resets itself leaves behind; kept in {summary_path.parent}", details)]

    def _firmware_record(self) -> bool:
        """Whether the firmware kept a new hardware error record at this boot."""
        try:
            summary = json.loads((self.firmware_root / self.boot_id / "summary.json").read_text())
        except (OSError, ValueError):
            return False
        return isinstance(summary, dict) and summary.get("new") is True

    @staticmethod
    def _crashlog(directory: Path, sha256) -> dict:
        """The Intel Crash Log decode luma-crash-evidence-intel kept for this record, if any."""
        try:
            crashlog = json.loads((directory / "crashlog.json").read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(crashlog, dict) or crashlog.get("decoded") is not True:
            return {}
        if sha256 and crashlog.get("sha256") != sha256:
            return {}
        return crashlog

    def previous_boot_events(self) -> list[Event]:
        verdict = previous_boot(self.runner, self.wtmp)
        if verdict is None or verdict.clean is None:
            return []
        if self.state.get("previous-boot-checked") == verdict.boot_id:
            return []
        self.state.set("previous-boot-checked", verdict.boot_id)
        events = []
        records = pstore_records(verdict.last_entry_usec / 1e6 - 600, self.pstore_root)
        if not verdict.clean:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(verdict.last_entry_usec / 1e6))
            if records or self._firmware_record():
                cause, why = "crash", "the kernel or firmware recorded an error, so it crashed or was reset"
            elif verdict.asleep:
                cause, why = "power-lost-asleep", "it was asleep, so the battery ran out or power was lost"
            else:
                cause, why = "power-or-reset", "power was lost, or it was reset or held off"
            events.append(Event(
                "unclean-shutdown", "machine",
                f"The computer stopped without shutting down ({why}); the last record was written at {stamp}",
                {"boot_id": verdict.boot_id, "last_entry_usec": verdict.last_entry_usec,
                 "last_sources": list(verdict.last_sources), "cause": cause}))
        if records:
            events.append(Event(
                "kernel-crash-record", "machine",
                f"The kernel left {len(records)} crash record(s) from the previous boot in "
                f"{self.pstore_root}", {"boot_id": verdict.boot_id, "records": records}))
        return events

    def crash_events(self) -> list[Event]:
        cursor = self.state.get("coredump-cursor")
        query = ["journalctl", f"MESSAGE_ID={COREDUMP_MESSAGE_ID}", "-o", "json", "--no-pager",
                 "--output-fields=COREDUMP_UID,COREDUMP_EXE,COREDUMP_COMM,COREDUMP_SIGNAL,"
                 "COREDUMP_SIGNAL_NAME,COREDUMP_UNIT,COREDUMP_USER_UNIT"]
        # The first look covers this boot only, so a first login does not
        # replay every crash the machine has ever kept.
        query += [f"--after-cursor={cursor}"] if cursor else ["-b", "0"]
        entries = _json_lines(self.runner(query))
        if not entries:
            return []
        self.state.set("coredump-cursor", _field(entries[-1], "__CURSOR") or cursor or "")
        mine = [e for e in entries if _field(e, "COREDUMP_UID") == str(self.uid)]
        events: list[Event] = []
        for entry in mine[:MAX_CRASH_EVENTS]:
            events.append(self._crash(entry))
        if len(mine) > MAX_CRASH_EVENTS:
            names = sorted({self._name(e) for e in mine[MAX_CRASH_EVENTS:]})
            events.append(Event(
                "crash", "machine",
                f"{len(mine) - MAX_CRASH_EVENTS} more processes crashed: " + ", ".join(names[:8]),
                {"count": len(mine) - MAX_CRASH_EVENTS, "names": names}))
        return events

    def launch_failure_events(self) -> list[Event]:
        """Installed applications that failed while opening, and exactly why."""
        cursor = self.state.get("launch-failure-cursor")
        query = ["journalctl", f"MESSAGE_ID={LAUNCH_FAILURE_MESSAGE_ID}", f"_UID={self.uid}", "-o", "json",
                 "--no-pager", "--output-fields=MESSAGE," + ",".join(LAUNCH_FAILURE_FIELDS)]
        query += [f"--after-cursor={cursor}"] if cursor else ["-b", "0"]
        entries = _json_lines(self.runner(query))
        if not entries:
            return []
        self.state.set("launch-failure-cursor", _field(entries[-1], "__CURSOR") or cursor or "")
        events = []
        for entry in entries[-MAX_CRASH_EVENTS:]:
            application = _field(entry, "LUMA_APPLICATION_ID") or "application"
            details = {name.removeprefix("LUMA_").lower(): _field(entry, name) for name in LAUNCH_FAILURE_FIELDS}
            details["stderr"] = details["stderr"].splitlines()[-40:]
            name = _field(entry, "LUMA_APPLICATION_NAME") or application
            events.append(Event("launch-failure", application,
                                _field(entry, "MESSAGE") or f"{name} couldn't open", details))
        return events

    @staticmethod
    def _name(entry: dict) -> str:
        unit = _field(entry, "COREDUMP_USER_UNIT") or _field(entry, "COREDUMP_UNIT")
        if unit and not unit.startswith(("user@", "session-")):
            return friendly(unit)
        # Only the executable's name: its path can be inside a person's home.
        return Path(_field(entry, "COREDUMP_EXE")).name or _field(entry, "COREDUMP_COMM") or "a process"

    def _crash(self, entry: dict) -> Event:
        name = self._name(entry)
        cause = signal_name(_field(entry, "COREDUMP_SIGNAL_NAME") or _field(entry, "COREDUMP_SIGNAL"))
        unit = _field(entry, "COREDUMP_USER_UNIT") or _field(entry, "COREDUMP_UNIT") or name
        return Event("crash", unit, f"{name} crashed ({cause}); `coredumpctl info` has the backtrace",
                     {"executable": Path(_field(entry, "COREDUMP_EXE")).name, "signal": cause,
                      "cursor": _field(entry, "__CURSOR")})
