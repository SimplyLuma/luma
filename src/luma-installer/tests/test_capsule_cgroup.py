"""Application capsules belong to the app slice monitored by the memory guard.

The optional runtime check starts two disposable, idle rootless containers:
one with the production policy and one reproducing the former user.slice
placement. Run it on a host or VM with a systemd user session:

    LUMA_CAPSULE_TEST_IMAGE=LOCAL_IMAGE PYTHONPATH=. \
        python3 -m unittest discover -s tests -p test_capsule_cgroup.py -v

No allocation stress or OOM workload runs on the person's machine.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from luma_installer import capsule_runtime, launcher


def assert_application_cgroup(test: unittest.TestCase, text: str, uid: int) -> None:
    paths = [line.partition("0::")[2] for line in text.splitlines() if line.startswith("0::")]
    test.assertEqual(len(paths), 1, "expected one unified host cgroup path")
    prefix = f"/user.slice/user-{uid}.slice/user@{uid}.service/app.slice/libpod-"
    test.assertTrue(paths[0].startswith(prefix), f"capsule escaped app.slice: {paths[0]}")


class CapsuleCgroupPolicy(unittest.TestCase):
    def test_new_capsules_join_monitored_app_slice(self):
        for kind in ("rpm", "deb", "portable"):
            with self.subTest(format=kind), tempfile.TemporaryDirectory() as temporary:
                record = {"format": kind, "sha256": "fixture", "application_id": "fixture",
                          "image": "localhost/fixture", "command": "/usr/bin/true",
                          "root": str(Path(temporary) / "AppDir")}
                with patch("pathlib.Path.home", return_value=Path(temporary)), \
                     patch.dict(os.environ, {"XDG_RUNTIME_DIR": temporary}, clear=True), \
                     patch.object(launcher, "_running_capsule", return_value=False), \
                     patch.object(launcher, "_start_bus_proxy", return_value=(None, None)), \
                     patch.object(launcher, "_person_folders", return_value=[]), \
                     patch.object(launcher, "_guarded", return_value=0) as run:
                    self.assertEqual(launcher._launch_deb(record), 0)
                arguments = run.call_args.args[1]
                self.assertEqual(arguments[:2], ["podman", "run"])
                parents = [argument for argument in arguments if argument.startswith("--cgroup-parent=")]
                self.assertEqual(parents, ["--cgroup-parent=app.slice"])
                self.assertLess(arguments.index(parents[0]), arguments.index(record["image"]))
                self.assertIn("--init", arguments)
                self.assertIn("label=type:luma_application.process", arguments)
                self.assertNotIn("--privileged", arguments)

    def test_existing_capsule_handoff_does_not_restart_the_application(self):
        record = {"sha256": "fixture", "application_id": "fixture", "command": "/usr/bin/true"}
        with patch.object(launcher, "_running_capsule", return_value=True), \
             patch.object(launcher, "_guarded", return_value=0) as run:
            self.assertEqual(launcher._launch_deb(record, ["callback://fixture"]), 0)
        arguments = run.call_args.args[1]
        self.assertEqual(arguments[:3], ["podman", "exec", "luma-run-fixture"])
        self.assertIn("callback://fixture", arguments)
        self.assertFalse(any(argument.startswith("--cgroup-parent=") for argument in arguments))

    def test_path_check_rejects_unmonitored_and_session_placements(self):
        prefix = "/user.slice/user-1000.slice/user@1000.service/"
        assert_application_cgroup(self, f"0::{prefix}app.slice/libpod-fixture.scope/container\n", 1000)
        for path in (prefix + "user.slice/libpod-fixture.scope/container",
                     prefix + "session.slice/libpod-fixture.scope/container",
                     "/system.slice/libpod-fixture.scope/container", "/"):
            with self.subTest(path=path), self.assertRaises(AssertionError):
                assert_application_cgroup(self, f"0::{path}\n", 1000)
        with self.assertRaises(AssertionError):
            assert_application_cgroup(self, "", 1000)


@unittest.skipUnless(os.environ.get("LUMA_CAPSULE_TEST_IMAGE"), "needs opt-in rootless host/VM image")
class CapsuleCgroupRuntime(unittest.TestCase):
    def test_real_capsule_placement_and_legacy_negative(self):
        self.assertNotEqual(os.getuid(), 0, "run this check in a systemd user session as a non-root user")
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                name = f"luma-cgroup-test-{os.getpid()}-{int(legacy)}"
                options = capsule_runtime.launch_arguments({})
                if legacy:
                    options = [argument for argument in options if not argument.startswith("--cgroup-parent=")]
                    options.append("--cgroup-parent=user.slice")
                subprocess.run(["podman", "run", "--pull=never", "--detach", "--rm", "--name", name,
                                "--userns=keep-id", "--network=none", *options,
                                os.environ["LUMA_CAPSULE_TEST_IMAGE"], "sleep", "120"],
                               check=True, capture_output=True, text=True, timeout=30)
                try:
                    result = subprocess.run(["podman", "inspect", "--format", "{{.State.Pid}}", name],
                                            check=True, capture_output=True, text=True, timeout=10)
                    pid = int(result.stdout.strip())
                    self.assertGreater(pid, 0)
                    cgroup = Path(f"/proc/{pid}/cgroup").read_text()
                    if legacy:
                        with self.assertRaises(AssertionError):
                            assert_application_cgroup(self, cgroup, os.getuid())
                    else:
                        assert_application_cgroup(self, cgroup, os.getuid())
                    print(f"capsule legacy={legacy} host cgroup: {cgroup.strip()}", flush=True)
                finally:
                    subprocess.run(["podman", "rm", "--force", "--time=0", name],
                                   check=True, capture_output=True, text=True, timeout=15)


if __name__ == "__main__":
    unittest.main()
