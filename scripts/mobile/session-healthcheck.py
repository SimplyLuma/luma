#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Check update requirements and the selected phone graphical session."""
import argparse
from pathlib import Path
import subprocess
import time


SESSIONS = {"gdm.service": ("gnome-shell",), "greetd.service": ("phoc", "phosh")}


def graphical_session(run=subprocess.run):
    manager = run(
        ["systemctl", "show", "-P", "Id", "display-manager.service"],
        capture_output=True, text=True, timeout=5,
    )
    name = manager.stdout.strip()
    processes = SESSIONS.get(name)
    if manager.returncode or processes is None:
        return False
    active = run(["systemctl", "-q", "is-active", name], timeout=5)
    return active.returncode == 0 and all(
        run(["pgrep", "-x", process], stdout=subprocess.DEVNULL, timeout=5).returncode == 0
        for process in processes
    )


def required_checks(directories, run=subprocess.run):
    passed = True
    for directory in directories:
        for script in sorted(Path(directory).glob("*.sh")):
            result = run(["bash", str(script)], timeout=120)
            print(f"mobile-health: {'ok' if result.returncode == 0 else 'failed'}: {script.name}", flush=True)
            passed = result.returncode == 0 and passed
    return passed


def check(directories, timeout=120, run=subprocess.run):
    # Keep required update checks authoritative even when the greeter works.
    required = required_checks(directories, run)
    deadline = time.monotonic() + timeout
    while True:
        if graphical_session(run):
            print("mobile-health: selected graphical session is running", flush=True)
            return required
        if time.monotonic() >= deadline:
            print("mobile-health: selected graphical session did not start", flush=True)
            return False
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    directories = ("/usr/lib/greenboot/check/required.d", "/etc/greenboot/check/required.d") if Path("/run/ostree-booted").exists() else ()
    try:
        return 0 if check(directories, max(0, args.timeout)) else 1
    except (OSError, subprocess.SubprocessError) as error:
        print(f"mobile-health: check failed: {type(error).__name__}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
