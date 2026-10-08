"""A capsule reaps the processes its application orphans.

Every package capsule runs with an init at PID 1 (podman --init: catatonit).
Without one the application is PID 1 and inherits every orphan in the capsule;
Electron never reaps them, so zombies filled the capsule's process limit and
nothing inside it could start another process.

The unit tests run everywhere. The capsule test runs a real capsule and needs
podman and an image with /bin/sh:

    LUMA_CAPSULE_TEST_IMAGE=registry.fedoraproject.org/fedora-minimal:44 \\
    LUMA_CAPSULE_TEST_LABEL=1 \\
        python3 -m unittest tests.test_capsule_reaper

LUMA_CAPSULE_TEST_LABEL=1 also applies the capsule's own SELinux domain
(luma_application.process), which needs Luma's policy module installed.

    python3 tests/test_capsule_reaper.py [--no-init]

prints the zombie count for the same workload, with or without the init, to
reproduce the fault and its fix.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from luma_installer import capsule_runtime as runtime_policy  # noqa: E402
from luma_installer.launcher import _capsule_level  # noqa: E402

ORPHANS = 100

# Start ORPHANS processes whose parent exits at once (a double fork), so each
# is reparented to the capsule's PID 1. Then become a process that never waits
# for anything, as Electron's main process does.
WORKLOAD = f"i=0; while [ $i -lt {ORPHANS} ]; do (sleep 0.3 &); i=$((i+1)); done; exec sleep 600"

COUNT_ZOMBIES = "n=0; for s in /proc/[0-9]*/stat; do case \"$(cat $s 2>/dev/null)\" in *') Z '*) n=$((n+1));; esac; done; echo $n"


def capsule_arguments(init: bool = True) -> list[str]:
    arguments = runtime_policy.launch_arguments({})
    if not init:
        arguments = [a for a in arguments if a != "--init"]
    if os.environ.get("LUMA_CAPSULE_TEST_LABEL") == "1":
        arguments += ["--security-opt", "label=type:luma_application.process",
                      "--security-opt", f"label=level:{_capsule_level('capsule-reaper-test')}"]
    return arguments


def measure(image: str, init: bool = True) -> tuple[int, int]:
    """(zombies, processes) in a throwaway capsule a few seconds after the workload."""
    name = f"luma-reaper-test-{os.getpid()}-{int(init)}"
    subprocess.run(["podman", "run", "-d", "--rm", "--name", name, "--userns=keep-id",
                    *capsule_arguments(init), image, "sh", "-c", WORKLOAD],
                   check=True, capture_output=True, text=True)
    try:
        time.sleep(3)
        zombies = subprocess.run(["podman", "exec", name, "sh", "-c", COUNT_ZOMBIES],
                                 check=True, capture_output=True, text=True).stdout.strip()
        processes = subprocess.run(["podman", "exec", name, "sh", "-c", "ls -d /proc/[0-9]* | wc -l"],
                                   check=True, capture_output=True, text=True).stdout.strip()
        return int(zombies), int(processes)
    finally:
        subprocess.run(["podman", "rm", "-f", "--time=0", name], capture_output=True, check=False)


class CapsuleInit(unittest.TestCase):
    def test_every_capsule_starts_under_an_init(self):
        self.assertIn("--init", runtime_policy.launch_arguments({}))
        self.assertIn("--init", runtime_policy.launch_arguments({"chromium": True, "format": "rpm"}))

    def test_argument_probe_skips_the_init(self):
        # catatonit's own command line carries the application's, two words
        # later; counting from it would pad every handoff wrongly.
        flag = runtime_policy.CHROMIUM_FLAGS[-1]
        with tempfile.TemporaryDirectory() as proc:
            for pid, argv in (("1", ["/run/podman-init", "--", "chatgpt", *runtime_policy.CHROMIUM_FLAGS]),
                              ("2", ["chatgpt", *runtime_policy.CHROMIUM_FLAGS, "chatgpt://x"]),
                              ("30", ["chatgpt", "--type=renderer", flag])):
                Path(proc, pid).mkdir()
                Path(proc, pid, "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
            script = runtime_policy.PRIMARY_ARGUMENTS_SCRIPT.replace("/proc/", proc + "/")
            result = subprocess.run(["sh", "-c", script, "sh", flag], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), str(1 + len(runtime_policy.CHROMIUM_FLAGS)))


@unittest.skipUnless(os.environ.get("LUMA_CAPSULE_TEST_IMAGE") and shutil.which("podman"),
                     "needs podman and LUMA_CAPSULE_TEST_IMAGE")
class CapsuleReapsOrphans(unittest.TestCase):
    def test_orphans_do_not_linger_as_zombies(self):
        zombies, processes = measure(os.environ["LUMA_CAPSULE_TEST_IMAGE"])
        self.assertLessEqual(zombies, 2, f"{zombies} zombies of {ORPHANS} orphans")
        self.assertLess(processes, 10)


if __name__ == "__main__":
    image = os.environ.get("LUMA_CAPSULE_TEST_IMAGE")
    if not image:
        sys.exit("set LUMA_CAPSULE_TEST_IMAGE")
    init = "--no-init" not in sys.argv
    zombies, processes = measure(image, init)
    reaper = "--init" in capsule_arguments(init)
    print(f"init={'catatonit' if reaper else 'none (application is PID 1)'} orphans={ORPHANS} "
          f"zombies={zombies} processes={processes}")
