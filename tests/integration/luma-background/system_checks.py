#!/usr/bin/python3
# SPDX-License-Identifier: MPL-2.0
"""System-side checks of ADR-033 that need the real system manager, polkit and systemd-oomd.

Run as root inside a disposable Fedora 44 container booted with systemd, after
the luma-background RPM is installed and user "luma" (uid 1000) lingers.
Never run this on a machine someone uses: it deliberately creates memory
pressure (bounded by the test unit's own MemoryMax) and restarts the user's
manager.

    system_checks.py [--oomd] [--wake] [--output FILE]

--oomd  luma-background.slice's own systemd-oomd policy: an agent-slice unit
        stalled at its MemoryHigh is killed by systemd-oomd for the slice's
        pressure, and a program elsewhere in the session is not.
--wake  org.projectluma.BackgroundWake1: polkit decides, wake-ups are transient
        WakeSystem timers named per user, replaced without a gap, limited,
        cleared, gone with the user manager, and the helper exits when idle.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

UID, GUEST_UID = 1000, 1001
RUNTIME = f"/run/user/{UID}"
RESULTS: list[dict] = []
WAKE = ("org.projectluma.BackgroundWake1", "/org/projectluma/BackgroundWake1", "org.projectluma.BackgroundWake1")


def run(*command: str, check: bool = False, timeout: int = 60, user: str | None = None) -> subprocess.CompletedProcess:
    if user:
        uid = UID if user == "luma" else GUEST_UID
        command = ("runuser", "-u", user, "--", "env", f"XDG_RUNTIME_DIR=/run/user/{uid}",
                   f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus", *command)
    result = subprocess.run(list(command), capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(command)}: {result.stderr.strip()}")
    return result


def record(name: str, ok: bool, evidence) -> bool:
    RESULTS.append({"check": name, "pass": bool(ok), "evidence": evidence})
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n      {json.dumps(evidence, default=str)[:900]}", flush=True)
    return ok


def wait(predicate, seconds: float, step: float = 0.5):
    deadline = time.monotonic() + seconds
    value = None
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return value


# -- systemd-oomd ------------------------------------------------------------------

HOG = """
import time
chunks = []
while True:
    chunks.append(bytearray(4 * 1024 * 1024))   # touched: bytearray zero-fills
    time.sleep(0.02)
"""


def check_oomd() -> None:
    run("systemctl", "enable", "--now", "systemd-oomd.service", check=True)
    record("systemd-oomd runs in the test system", run("systemctl", "is-active", "systemd-oomd").stdout.strip() == "active",
           run("systemctl", "status", "--no-pager", "-n", "0", "systemd-oomd").stdout[-600:])
    slice_path = f"/user.slice/user-{UID}.slice/user@{UID}.service/luma-background.slice"
    run("systemctl", "--user", "start", "luma-background-essential.slice", user="luma")
    monitored = wait(lambda: slice_path in run("oomctl", "dump").stdout, 30)
    dump = run("oomctl", "dump").stdout
    record("the user manager hands luma-background.slice's pressure policy to systemd-oomd", bool(monitored),
           [line for line in dump.splitlines() if "luma-background" in line or "Pressure Limit" in line][:12])
    bystander = "bga-oomd-bystander.service"
    hog = "app-org.example.Hog-agent.service"
    run("systemctl", "--user", "stop", hog, bystander, user="luma")
    run("systemctl", "--user", "reset-failed", hog, bystander, user="luma")
    # The bystander is an ordinary program in the person's session holding some memory.
    run("systemd-run", "--user", f"--unit={bystander}", "--slice=app.slice", "python3", "-c",
        "import time; x = bytearray(64 * 1024 * 1024); time.sleep(3600)", user="luma", check=True)
    started = time.monotonic()
    # Shaped exactly like a generated agent unit: in the agents' slice, at the
    # category's limits. It allocates past MemoryHigh and keeps allocating, so
    # the kernel throttles it: that stall is the pressure the policy judges.
    run("systemd-run", "--user", f"--unit={hog}", "--slice=luma-background-deferrable.slice",
        "-p", "MemoryHigh=64M", "-p", "MemoryMax=128M", "-p", "MemorySwapMax=64M",
        "python3", "-c", HOG, user="luma", check=True)

    def killed():
        state = run("systemctl", "--user", "show", "-P", "ActiveState", hog, user="luma").stdout.strip()
        result = run("systemctl", "--user", "show", "-P", "Result", hog, user="luma").stdout.strip()
        return (state, result) if state in {"failed", "inactive"} else None

    outcome = wait(killed, 120, 1)
    elapsed = round(time.monotonic() - started, 1)
    journal = [line for line in run("journalctl", "-u", "systemd-oomd", "--no-pager", "-o", "cat",
                                    "--since", "-5min").stdout.splitlines() if "Killed" in line or "luma" in line]
    by_oomd = any("Org.example.Hog" in line or "org.example.Hog" in line for line in journal)
    record("systemd-oomd kills the stalled agent for luma-background.slice's pressure",
           bool(outcome) and by_oomd,
           {"state/result": outcome, "seconds": elapsed, "oomd journal": journal[-4:],
            "memory.events": Path(f"/sys/fs/cgroup{slice_path}/luma-background-deferrable.slice/memory.events").read_text()
            if Path(f"/sys/fs/cgroup{slice_path}/luma-background-deferrable.slice/memory.events").exists() else None})
    alive = run("systemctl", "--user", "is-active", bystander, user="luma").stdout.strip()
    record("the program in front of the person is left alone", alive == "active", {"bystander": alive})
    run("systemctl", "--user", "stop", bystander, user="luma")
    run("systemctl", "--user", "reset-failed", hog, user="luma")


# -- Wake-ups --------------------------------------------------------------------------

def wake_call(user: str, method: str, signature: str = "", *args: str) -> subprocess.CompletedProcess:
    return run("busctl", "--system", "--json=short", "call", *WAKE, method, *([signature] if signature else []), *args,
               user=user, timeout=30)


def timers(uid: int) -> list[str]:
    # Without --all: the timers still waiting, not ones stopped and not yet collected.
    listing = run("systemctl", "list-units", "--plain", "--no-legend", "--type=timer", f"luma-wake-u{uid}-*").stdout
    return [line.split()[0] for line in listing.splitlines() if line.strip()]


def check_wake() -> None:
    now = int(time.time())
    first, second = now + 600, now + 900
    reply = wake_call("luma", "SetWakeup", "sx", "org.projectluma.Clock", str(first))
    unit = f"luma-wake-u{UID}-org.projectluma.Clock-{first}.timer"
    properties = run("systemctl", "show", unit, "-p", "WakeSystem", "-p", "RemainAfterElapse", "-p", "TimersCalendar",
                     "-p", "PartOf", "-p", "NextElapseUSecRealtime", "-p", "Triggers").stdout
    record("an authorized session's wake-up is a transient WakeSystem timer that elapses then",
           reply.returncode == 0 and "WakeSystem=yes" in properties and "RemainAfterElapse=no" in properties
           and f"PartOf=user@{UID}.service" in properties and unit in timers(UID),
           {"reply": reply.stderr.strip() or reply.stdout.strip(), "unit": properties.splitlines(),
            "fragment": run("systemctl", "show", "-P", "FragmentPath", unit).stdout.strip()})
    manager_log = [line for line in run("journalctl", "_PID=1", "--no-pager", "-o", "cat", "--since", "-2min").stdout.splitlines()
                   if "ALARM" in line or "wake" in line.lower()]
    RESULTS.append({"note": "PID 1 about the RTC alarm clock in this container", "evidence": manager_log[-5:]})

    wake_call("luma", "SetWakeup", "sx", "org.projectluma.Clock", str(second))
    record("moving a wake-up leaves exactly the new one", timers(UID) == [f"luma-wake-u{UID}-org.projectluma.Clock-{second}.timer"],
           timers(UID))

    # busctl prints the error's message, not its D-Bus name, so the message is what is checked.
    refused = wake_call("guest", "SetWakeup", "sx", "org.projectluma.Clock", str(first))
    listed = wake_call("guest", "ListWakeups")
    record("a user polkit does not authorize is refused, and sees nothing",
           refused.returncode != 0 and "active local session" in refused.stderr and timers(GUEST_UID) == []
           and listed.returncode != 0 and "active local session" in listed.stderr,
           {"set": refused.stderr.strip(), "list": listed.stderr.strip()})

    bad = [(wake_call("luma", "SetWakeup", "sx", name, str(at)), expected) for name, at, expected in
           (("org.projectluma.Clock", now - 10, "has passed"),
            ("../../evil", first, "not a valid wake-up name"),
            ("org.projectluma.Clock", now + 40 * 86400, "31 days"))]
    record("past, malformed and far-future wake-ups are refused",
           all(item.returncode != 0 and expected in item.stderr for item, expected in bad),
           [item.stderr.strip() for item, _expected in bad])

    for index in range(7):
        wake_call("luma", "SetWakeup", "sx", f"org.example.App{index}", str(first + index))
    over = wake_call("luma", "SetWakeup", "sx", "org.example.Ninth", str(first))
    record("eight wake-ups per person at most", over.returncode != 0 and "at most" in over.stderr
           and len(timers(UID)) == 8, {"count": len(timers(UID)), "ninth": over.stderr.strip()})
    listing = wake_call("luma", "ListWakeups")
    cleared = wake_call("luma", "ClearWakeup", "s", "org.example.App0")
    record("list and clear only the caller's own", listing.returncode == 0 and cleared.returncode == 0
           and len(timers(UID)) == 7, {"list": listing.stdout.strip()[:300], "clear": cleared.stdout.strip()})

    security = run("systemd-analyze", "security", "--no-pager", "luma-background-wake.service").stdout.splitlines()
    RESULTS.append({"note": "systemd-analyze security luma-background-wake.service", "evidence": security[-1:]})
    idle = wait(lambda: run("systemctl", "is-active", "luma-background-wake.service").stdout.strip() != "active", 60, 2)
    record("the helper exits when idle; its wake-ups stay with systemd", bool(idle) and len(timers(UID)) == 7,
           {"helper": run("systemctl", "is-active", "luma-background-wake.service").stdout.strip(), "timers": len(timers(UID))})

    run("systemctl", "stop", f"user@{UID}.service", timeout=120)
    gone = wait(lambda: timers(UID) == [] or None, 30)
    record("wake-ups end with the person's user manager", bool(gone), timers(UID))
    run("systemctl", "start", f"user@{UID}.service", timeout=120)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oomd", action="store_true")
    parser.add_argument("--wake", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("/tmp/bga-system-checks.json"))
    options = parser.parse_args()
    if not Path("/run/.containerenv").exists():
        sys.exit("refusing to run outside a container")
    if options.wake:
        check_wake()
    if options.oomd:
        check_oomd()
    checks = [item for item in RESULTS if "check" in item]
    passed = sum(item["pass"] for item in checks)
    options.output.write_text(json.dumps({"passed": passed, "total": len(checks), "results": RESULTS}, indent=2, default=str))
    print(f"\n{passed}/{len(checks)} checks passed; record in {options.output}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
