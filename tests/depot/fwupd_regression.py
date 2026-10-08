# SPDX-License-Identifier: Apache-2.0
"""Depot's firmware section against a real fwupd daemon: it never closes Depot.

Run by tests/depot/fwupd-container.sh as root inside a Fedora container with a
system D-Bus running. This script starts, stops, pauses and restarts fwupd
itself. Regression for "g_mutex_clear() called on uninitialised or locked mutex"
(libfwupd 2.1.7 finalizing a FwupdClient from a queued notification while the
Depot window's main loop was busy drawing).

  PYTHONPATH=src/luma-depot:src/luma-installer python3 tests/depot/fwupd_regression.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time

from gi.repository import Gio, GLib

from luma_depot import system_updates as su

FWUPD = "/usr/libexec/fwupd/fwupd"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)
    if not ok:
        failures.append(name)


def fwupd_owned() -> bool:
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "NameHasOwner", GLib.Variant("(s)", ("org.freedesktop.fwupd",)),
                          GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 2000, None)
    return bool(reply.unpack()[0])


class Daemon:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None

    def start(self, wait: bool = True) -> None:
        self.process = subprocess.Popen([FWUPD], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 60
        while wait and not fwupd_owned() and time.monotonic() < deadline:
            time.sleep(0.2)
        if wait and not fwupd_owned():
            raise SystemExit("fwupd did not start")

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            self.process.wait(timeout=30)
            self.process = None
        deadline = time.monotonic() + 10
        while fwupd_owned() and time.monotonic() < deadline:
            time.sleep(0.1)

    def pause(self, paused: bool) -> None:
        os.kill(self.process.pid, signal.SIGSTOP if paused else signal.SIGCONT)


def pump(until, seconds: float) -> bool:
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while not until() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)
    return until()


def busy_check(firmware: su.Firmware, busy_seconds: float = 3.0, wait: float = 120.0) -> bool:
    """The Depot window's first draw: the main loop is busy while the check runs."""
    firmware.loaded = False
    firmware.load()
    time.sleep(busy_seconds)
    return pump(lambda: firmware.loaded and not firmware.checking, wait)


def control_old_pattern() -> None:
    """The pre-fix pattern, in a child: record whether this libfwupd still aborts on it."""
    code = textwrap.dedent("""
        import threading, time, gi
        gi.require_version("Fwupd", "2.0")
        from gi.repository import Fwupd, GLib
        def work():
            client = Fwupd.Client.new()
            client.get_devices(None)
        GLib.idle_add(lambda: (threading.Thread(target=work).start(), time.sleep(4), False)[2])
        loop = GLib.MainLoop()
        GLib.timeout_add(7000, loop.quit)
        loop.run()
    """)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    aborted = result.returncode == -signal.SIGABRT and "g_mutex_clear" in result.stderr
    print(f"INFO control: a worker-thread FwupdClient under a busy main loop "
          f"{'still aborts (g_mutex_clear)' if aborted else f'exited {result.returncode}'}", flush=True)


def main() -> int:
    daemon = Daemon()
    daemon.start()
    control_old_pattern()

    changes = []
    firmware = su.Firmware(lambda: changes.append(firmware.problem))

    # 1. fwupd running, the window busy while the check finishes — the crash that shipped.
    for attempt in range(5):
        ok = busy_check(firmware)
        check(f"running fwupd, busy main loop #{attempt + 1}",
              ok and firmware.available is True and firmware.problem == "",
              f"available={firmware.available} updates={len(firmware.updates)} problem={firmware.problem!r}")

    # 2. The probe itself exits cleanly, never by a signal, many times over.
    signalled = []
    for _ in range(15):
        run = subprocess.run([sys.executable, "-P", "-m", "luma_depot.firmware_probe", "list"],
                             capture_output=True, text=True, timeout=120)
        if run.returncode != 0 or su._probe_result(run.stdout) is None:
            signalled.append(run.returncode)
    check("probe exits cleanly against a running fwupd (15 runs)", not signalled, f"{signalled}")

    run = subprocess.run([sys.executable, "-P", "-m", "luma_depot.firmware_probe", "install", "no-such-device"],
                         capture_output=True, text=True, timeout=120)
    answer = su._probe_result(run.stdout) or {}
    check("install probe refuses a missing device calmly", run.returncode == 0 and answer.get("code") == "gone",
          f"exit={run.returncode} answer={answer}")

    # 3. fwupd stopped (and not activatable in this container).
    daemon.stop()
    ok = busy_check(firmware, busy_seconds=0.5)
    check("stopped fwupd shows the calm problem", ok and firmware.problem == su.FIRMWARE_CHECK_FAILED,
          f"problem={firmware.problem!r}")

    # 4. fwupd back: the problem clears on the next check.
    daemon.start()
    ok = busy_check(firmware, busy_seconds=0.5)
    check("restarted fwupd clears the problem", ok and firmware.problem == "", f"problem={firmware.problem!r}")

    # 5. fwupd slow: paused past the check timeout, then resumed.
    slow = su.Firmware(lambda: None, check_seconds=4)
    daemon.pause(True)
    started = time.monotonic()
    ok = busy_check(slow, busy_seconds=0.5, wait=30)
    elapsed = time.monotonic() - started
    daemon.pause(False)
    check("slow fwupd is given up on calmly", ok and slow.problem == su.FIRMWARE_CHECK_FAILED and elapsed < 20,
          f"after {elapsed:.1f}s")
    time.sleep(1)
    ok = busy_check(slow, busy_seconds=0.5)
    check("fwupd answering again after a pause", ok and slow.problem == "", f"problem={slow.problem!r}")

    # 6. fwupd restarting while checks run, again and again.
    firmware.loaded = False
    for _ in range(6):
        firmware.load()
        pump(lambda: False, 0.3)
        daemon.stop()
        daemon.start(wait=False)
        pump(lambda: False, 1.0)
    daemon.stop()
    daemon.start()
    ok = pump(lambda: firmware.loaded and not firmware.checking, 180)
    ok = ok and busy_check(firmware, busy_seconds=0.5)
    check("fwupd restarting under repeated checks", ok and firmware.problem == "", f"problem={firmware.problem!r}")

    # 7. This process never loaded libfwupd.
    check("libfwupd never loaded into the window's process", "gi.repository.Fwupd" not in sys.modules)

    daemon.stop()
    print(f"{'FAILED' if failures else 'OK'}: {len(failures)} failure(s)", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
