#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check that a running, unused Android stays quiet on a Luma machine.

Run as root on a booted Luma VM or computer with Android started and nobody
using it:

    python3 tests/android/idle-runtime-check.py --minutes 30 --notify

--notify first posts one ordinary notification from Android's shell user, the
path every app takes. It fails when, during the window:

- Android's framework restarts (zygote is killed and started again);
- more than one com.android.settings process exists;
- an app process outlives the zygote that started it (an orphan of init);
- the system journal grows faster than --max-lines-per-hour;
- Android's payload cgroup has no memory ceiling.

Each failure was seen on nightly 20261108.10: a refused notification crashed
system_server about every 15 s, every restart left the previous generation of
apps running (44 Settings processes in 45 minutes) and the journal took about
16,000 lines an hour.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

LXC = ["/usr/bin/lxc-attach", "-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "--clear-env"]
PAYLOAD = Path("/sys/fs/cgroup/lxc.payload.waydroid")
ANDROID_PATH = "export PATH=/system/bin; "


def android_processes(proc: Path = Path("/proc")) -> list[tuple[int, int, str, str]]:
    """(pid, parent pid, argv[0], executable) of each process in Android's payload."""
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cgroup = (entry / "cgroup").read_text().strip()
            if cgroup != "0::/lxc.payload.waydroid" and \
                    not cgroup.startswith("0::/lxc.payload.waydroid/"):
                continue
            argv = (entry / "cmdline").read_bytes().split(b"\0")[0].decode(errors="replace")
            parent = int((entry / "stat").read_text().rsplit(")", 1)[1].split()[1])
            executable = Path(os.readlink(entry / "exe")).name
        except (OSError, ValueError, IndexError):
            continue
        found.append((int(entry.name), parent, argv, executable))
    return found


def container_init() -> int | None:
    result = subprocess.run(["/usr/bin/lxc-info", "-P", "/var/lib/waydroid/lxc", "-n", "waydroid",
                             "-pH"], capture_output=True, text=True, check=False, timeout=15)
    value = result.stdout.strip()
    return int(value) if value.isdigit() else None


def orphaned_apps(processes, init_pid) -> list[str]:
    # Apps run app_process forked by a zygote; native services are init's
    # children by design. An app whose parent is init outlived its zygote.
    return sorted(argv for _pid, parent, argv, executable in processes
                  if parent == init_pid and executable.startswith("app_process")
                  and not argv.startswith("zygote"))


def journal_lines(since: str) -> int:
    result = subprocess.run(["journalctl", "-q", "--no-pager", "-o", "cat", "--since", since],
                            capture_output=True, check=False, timeout=120)
    return result.stdout.count(b"\n")


def zygote_restarts(since: str) -> int:
    result = subprocess.run(["journalctl", "-q", "--no-pager", "-o", "cat", "--since", since,
                             "--grep", "Service 'zygote' .* received signal"],
                            capture_output=True, check=False, timeout=120)
    return result.stdout.count(b"\n")


def notify() -> None:
    subprocess.run([*LXC, "-u", "2000", "-g", "2000", "--", "/system/bin/sh", "-c",
                    ANDROID_PATH + "cmd notification post -S bigtext luma-idle-check "
                    "'Idle check' 'Android idle runtime check'"],
                   capture_output=True, check=True, timeout=60)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--max-lines-per-hour", type=int, default=1200)
    options = parser.parse_args()

    init_pid = container_init()
    if init_pid is None:
        print("FAIL Android is not running")
        return 1
    since = time.strftime("%Y-%m-%d %H:%M:%S")
    started = time.monotonic()
    if options.notify:
        notify()
    most_settings = 0
    orphans: list[str] = []
    while True:
        processes = android_processes()
        most_settings = max(most_settings, sum(argv == "com.android.settings"
                                               for _pid, _parent, argv, _exe in processes))
        orphans = orphaned_apps(processes, container_init() or init_pid)
        elapsed = time.monotonic() - started
        if elapsed >= options.minutes * 60:
            break
        time.sleep(min(60, options.minutes * 60 - elapsed))

    hours = max(elapsed / 3600, 1 / 60)
    rate = journal_lines(since) / hours
    restarts = zygote_restarts(since)
    try:
        memory_max = (PAYLOAD / "memory.max").read_text().strip()
        memory = int((PAYLOAD / "memory.current").read_text()) // (1 << 20)
    except (OSError, ValueError):
        memory_max, memory = "unknown", -1
    checks = [
        ("framework restarts", restarts == 0, str(restarts)),
        ("most Settings processes", most_settings <= 1, str(most_settings)),
        ("orphaned app processes", not orphans, ", ".join(orphans) or "0"),
        ("journal lines per hour", rate <= options.max_lines_per_hour, f"{rate:.0f}"),
        ("Android memory ceiling", memory_max.isdigit(), memory_max),
    ]
    for name, passed, value in checks:
        print(f"{'PASS' if passed else 'FAIL'} {name}: {value}")
    print(f"INFO Android memory: {memory} MiB after {elapsed / 60:.0f} min")
    return 0 if all(passed for _name, passed, _value in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
