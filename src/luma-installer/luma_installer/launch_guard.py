# SPDX-License-Identifier: Apache-2.0
"""An application that cannot open says so, instead of leaving a spinner.

Every installed application starts through luma-capsule-launch, which is the
parent of the application's process (bubblewrap for an AppImage or an
application folder, podman for a DEB or RPM capsule). When that process ends
unsuccessfully while the application is still opening — a system library it
needs is missing, the dynamic loader refuses it, it crashes before its first
window — nobody sees the error: it goes to the journal, and the Shell keeps
showing the launch as in progress until its startup timeout.

The launcher watches for exactly that and, when it happens:

* ends the launch's startup sequence at once (gtk_shell1.set_startup_id with the
  activation token the launch was given, the request GTK used before
  xdg-activation), so the spinner stops;
* shows one plain notification naming the application;
* writes one structured journal entry (MESSAGE_ID below) that Luma Vitals
  turns into an event with the specifics.

An application that exits on purpose (status 0, or stopped by SIGINT, SIGTERM,
SIGHUP or SIGKILL) is never reported.
"""
from __future__ import annotations

import collections
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

#: journalctl MESSAGE_ID=... finds every launch failure; Luma Vitals reads it.
MESSAGE_ID = "5b3c0f1e9d7a4c2b8e6f41a07d93c2e5"
#: Its own id, so "what has Luma stopped doing, and to what?" is one query.
BACKOFF_MESSAGE_ID = "9a41d6c05f8e4b37a2c9e18b47d50f63"
#: Mutter ends a startup sequence after 15 s; an exit after that is not "opening".
STARTUP_SECONDS = 15.0
STDERR_LINES = 40
#: Deliberate stops: an interrupt, a terminate at logout, a hang-up, a kill.
DELIBERATE = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGKILL}

MISSING_LIBRARY = re.compile(r"error while loading shared libraries: ([^:\s]+): cannot open shared object file")
MISSING_VERSION = re.compile(r"version [`']([A-Za-z0-9_.]+)' not found \(required by")


@dataclass
class Failure:
    kind: str                    # missing-library | library-version | crashed | exited | launcher
    status: int                  # exit status, or -signal
    seconds: float
    library: str = ""
    stderr: list[str] = field(default_factory=list)

    @property
    def signal_name(self) -> str:
        if self.status >= 0:
            return ""
        try:
            return signal.Signals(-self.status).name
        except ValueError:
            return f"signal {-self.status}"


def normalise_status(status: int) -> int:
    """Exit status as the application saw it: -N for signal N.

    subprocess reports a signalled child as -N. Podman and bubblewrap report an
    application that died of signal N as 128 + N, like a shell.
    """
    if 128 < status < 128 + 65:
        return -(status - 128)
    return status


def classify(status: int, seconds: float, stderr: list[str]) -> Failure | None:
    """A launch failure, or None when the application ended normally."""
    status = normalise_status(status)
    text = "\n".join(stderr)
    missing = MISSING_LIBRARY.search(text)
    # The dynamic loader stops before main(): it is a failure to open at any age.
    if status != 0 and missing:
        return Failure("missing-library", status, seconds, missing.group(1), list(stderr))
    version = MISSING_VERSION.search(text)
    if status != 0 and version:
        return Failure("library-version", status, seconds, version.group(1), list(stderr))
    if status == 0 or seconds > STARTUP_SECONDS:
        return None
    if status < 0:
        if -status in {int(s) for s in DELIBERATE}:
            return None
        return Failure("crashed", status, seconds, stderr=list(stderr))
    return Failure("exited", status, seconds, stderr=list(stderr))


def message(name: str, failure: Failure) -> tuple[str, str]:
    """The notification's title and body."""
    title = f"{name} couldn't open"
    if failure.kind == "missing-library":
        body = "A system library it needs is missing. Details in Luma Vitals."
    elif failure.kind == "library-version":
        body = "It needs a newer version of a system library than this system has. Details in Luma Vitals."
    elif failure.kind == "crashed":
        body = "It quit unexpectedly while opening. Details in Luma Vitals."
    elif failure.kind == "launcher":
        body = "Luma couldn't start it. Details in Luma Vitals."
    else:
        body = "It stopped while opening. Details in Luma Vitals."
    return title, body


def summary(name: str, failure: Failure) -> str:
    """One line for the journal and Vitals, with the specifics."""
    if failure.kind == "missing-library":
        return f"{name} couldn't open: the system library {failure.library} is missing"
    if failure.kind == "library-version":
        return f"{name} couldn't open: it needs {failure.library}, which this system's libraries don't provide"
    if failure.kind == "launcher":
        return f"{name} couldn't open: {failure.stderr[-1] if failure.stderr else 'Luma could not start it'}"
    if failure.kind == "crashed":
        return f"{name} couldn't open: it crashed ({failure.signal_name}) {failure.seconds:.1f} s after starting"
    return f"{name} couldn't open: it exited with status {failure.status} {failure.seconds:.1f} s after starting"


# -- running ------------------------------------------------------------------

def run(arguments: list[str], *, env: dict[str, str] | None = None, capture: bool = True,
        popen=subprocess.Popen, clock=time.monotonic) -> tuple[int, float, list[str]]:
    """Run the application; pass its error output through, keeping the last lines."""
    started = clock()
    if not capture:
        process = popen(arguments, env=env)
        return process.wait(), clock() - started, []
    tail: collections.deque[str] = collections.deque(maxlen=STDERR_LINES)
    process = popen(arguments, env=env, stderr=subprocess.PIPE)
    output = [getattr(sys.stderr, "buffer", None)]

    def pump() -> None:
        for raw in iter(process.stderr.readline, b""):
            if output[0] is not None:
                try:
                    output[0].write(raw)
                    output[0].flush()
                except (OSError, ValueError):
                    # Keep reading, so the application never blocks on a full pipe.
                    output[0] = None
            tail.append(raw.decode("utf-8", "replace").rstrip("\n"))
        process.stderr.close()

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        status = process.wait()
    except KeyboardInterrupt:
        process.send_signal(signal.SIGINT)
        status = process.wait()
    # The pipe can outlive the application when it left a child holding it.
    reader.join(timeout=2)
    return status, clock() - started, list(tail)


def should_capture() -> bool:
    """A terminal gets the application's own output untouched."""
    try:
        return not os.isatty(2)
    except OSError:
        return True


def report(name: str, application_id: str, icon: str, failure: Failure,
           environ: dict[str, str] | None = None) -> None:
    environ = dict(os.environ if environ is None else environ)
    end_startup(environ)
    title, body = message(name, failure)
    notify(title, body, icon)
    journal(name, application_id, failure)


# -- the startup sequence -------------------------------------------------------

def _wl_string(value: str) -> bytes:
    data = value.encode() + b"\0"
    return struct.pack("=I", len(data)) + data + b"\0" * (-len(data) % 4)


def _wl_message(object_id: int, opcode: int, payload: bytes) -> bytes:
    return struct.pack("=II", object_id, ((8 + len(payload)) << 16) | opcode) + payload


def _wl_events(connection: socket.socket, buffer: bytearray):
    while True:
        while len(buffer) >= 8:
            object_id, word = struct.unpack_from("=II", buffer)
            size, opcode = word >> 16, word & 0xFFFF
            if size < 8 or len(buffer) < size:
                break
            payload = bytes(buffer[8:size])
            del buffer[:size]
            yield object_id, opcode, payload
        chunk = connection.recv(65536)
        if not chunk:
            return
        buffer.extend(chunk)


def _wl_roundtrip(connection: socket.socket, buffer: bytearray, callback: int, globals_: dict | None) -> bool:
    for object_id, opcode, payload in _wl_events(connection, buffer):
        if object_id == 2 and opcode == 0 and globals_ is not None:
            name, length = struct.unpack_from("=II", payload)
            interface = payload[8:8 + length - 1].decode("ascii", "replace")
            globals_[interface] = name
        elif object_id == callback and opcode == 0:
            return True
        elif object_id == 1 and opcode == 0:  # wl_display.error
            return False
    return False


def end_startup(environ: dict[str, str], *, timeout: float = 2.0) -> bool:
    """Complete the launch's startup sequence so the Shell stops showing it."""
    token = environ.get("XDG_ACTIVATION_TOKEN") or environ.get("DESKTOP_STARTUP_ID") or ""
    display = environ.get("WAYLAND_DISPLAY") or ""
    if not token or not display:
        return False
    path = display if display.startswith("/") else str(Path(environ.get("XDG_RUNTIME_DIR", "")) / display)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(path)
            buffer = bytearray()
            found: dict[str, int] = {}
            connection.sendall(_wl_message(1, 1, struct.pack("=I", 2))   # get_registry -> 2
                               + _wl_message(1, 0, struct.pack("=I", 3)))  # sync -> 3
            if not _wl_roundtrip(connection, buffer, 3, found) or "gtk_shell1" not in found:
                return False
            bind = struct.pack("=I", found["gtk_shell1"]) + _wl_string("gtk_shell1") + struct.pack("=II", 1, 4)
            connection.sendall(_wl_message(2, 0, bind)                    # bind gtk_shell1 v1 -> 4
                               + _wl_message(4, 1, _wl_string(token))     # set_startup_id
                               + _wl_message(1, 0, struct.pack("=I", 5)))  # sync -> 5
            return _wl_roundtrip(connection, buffer, 5, None)
    except (OSError, struct.error):
        return False


# -- telling the person, and Vitals --------------------------------------------

def notify(title: str, body: str, icon: str) -> bool:
    try:
        import gi
        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # No desktop-entry: clicking must not start the application that just failed again.
        hints = {"urgency": GLib.Variant("y", 1)}
        bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      GLib.Variant("(susssasa{sv}i)", ("Luma", 0, icon or "dialog-warning-symbolic",
                                                       title, body, [], hints, -1)),
                      None, Gio.DBusCallFlags.NONE, 3000, None)
        return True
    except Exception:  # noqa: BLE001 - no session bus or no notification server: the journal still has it
        return False


def journal_backoff(name: str, application_id: str, flags: list[str],
                    failure: Failure) -> None:
    """Record that Luma stopped improving an application, and why.

    This is the only trace of a decision the person never had to make. It has
    to exist -- otherwise "Luma quietly changed how your application starts"
    is unfalsifiable -- and it has to be readable, which is why the message is
    a sentence rather than a flag dump. Vitals and `luma-install
    --energy-overrides` both read it back.
    """
    line = (f"{name} did not start with Luma's display and video settings, "
            f"so it will be started the way its publisher does.")
    fields = {
        "MESSAGE_ID": BACKOFF_MESSAGE_ID,
        "PRIORITY": "5",
        "SYSLOG_IDENTIFIER": "luma-capsule-launch",
        "LUMA_APPLICATION_ID": application_id,
        "LUMA_APPLICATION_NAME": name,
        "LUMA_ENERGY_FLAGS_WITHDRAWN": " ".join(flags),
        "LUMA_LAUNCH_FAILURE": failure.kind,
        "LUMA_EXIT_STATUS": str(failure.status),
    }
    try:
        from systemd import journal as systemd_journal
        systemd_journal.send(line, **fields)
    except Exception:  # noqa: BLE001 - python3-systemd missing or no journal socket
        print(f"luma-capsule-launch: {line}", file=sys.stderr)


def journal(name: str, application_id: str, failure: Failure) -> None:
    line = summary(name, failure)
    fields = {
        "MESSAGE_ID": MESSAGE_ID,
        "PRIORITY": "3",
        "SYSLOG_IDENTIFIER": "luma-capsule-launch",
        "LUMA_APPLICATION_ID": application_id,
        "LUMA_APPLICATION_NAME": name,
        "LUMA_LAUNCH_FAILURE": failure.kind,
        "LUMA_EXIT_STATUS": str(failure.status),
        "LUMA_SECONDS": f"{failure.seconds:.1f}",
        "LUMA_MISSING": failure.library,
        "LUMA_SIGNAL": failure.signal_name,
        "LUMA_STDERR": "\n".join(failure.stderr[-STDERR_LINES:]),
    }
    try:
        from systemd import journal as systemd_journal
        systemd_journal.send(line, **fields)
    except Exception:  # noqa: BLE001 - python3-systemd missing or no journal socket
        print(f"luma-capsule-launch: {line}", file=sys.stderr)
