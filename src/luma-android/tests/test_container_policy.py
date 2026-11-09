# SPDX-License-Identifier: Apache-2.0
"""Android's process cgroups, the container's limits and Binder admission."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest import TestCase

from luma_android.waydroid_hardware import (
    container_defines, install_container_policy, profile_limits,
)

DATA = Path(__file__).parents[1] / "data"
UPSTREAM_START = ["lxc-start", "-P", "/var/lib/waydroid/lxc", "-F", "-n", "waydroid",
                  "--", "/init"]
# Directives a user manager can only apply inside a user namespace that maps
# this user alone (systemd.exec(5), PrivateUsers=).
NAMESPACED = ("PrivateTmp", "PrivateUsers", "PrivateDevices", "PrivateIPC", "PrivateMounts",
              "ProtectSystem", "ProtectHome", "ProtectKernelTunables", "ProtectKernelModules",
              "ProtectKernelLogs", "ProtectControlGroups", "ProtectClock", "ProtectHostname",
              "ProtectProc", "ProcSubset", "ReadWritePaths", "ReadOnlyPaths",
              "InaccessiblePaths", "TemporaryFileSystem", "BindPaths", "BindReadOnlyPaths",
              "RootDirectory", "RootImage", "MountAPIVFS", "PrivateNetwork")


class ContainerPolicy(TestCase):
    def test_android_gets_a_writable_cgroup_namespace(self):
        # Read-only, libprocessgroup made no uid_N/pid_N groups: a framework
        # restart killed only zygote and every app of the old generation lived.
        defines = container_defines({})
        self.assertEqual(defines, ["-s", "lxc.mount.auto=cgroup:rw:force"])

    def test_profile_limits_follow_android_into_its_payload(self):
        defines = container_defines({"memory.high": "100", "memory.max": "200",
                                     "pids.max": "300"})
        self.assertEqual(defines[2:], ["-s", "lxc.cgroup2.memory.high=100",
                                       "-s", "lxc.cgroup2.memory.max=200",
                                       "-s", "lxc.cgroup2.pids.max=300"])

    def test_profile_percentages_become_payload_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            meminfo = Path(directory) / "meminfo"
            meminfo.write_text("MemTotal:        6046824 kB\nMemFree:  100 kB\n")
            environ = {"LUMA_ANDROID_MEMORY_HIGH_PERCENT": "60",
                       "LUMA_ANDROID_MEMORY_MAX_PERCENT": "75",
                       "LUMA_ANDROID_TASKS_MAX": "4096"}
            limits = profile_limits(environ, meminfo)
            self.assertEqual(limits["pids.max"], "4096")
            # systemd rounds MemoryHigh=60% of 6046824 kB to 3715162112.
            self.assertLessEqual(abs(int(limits["memory.high"]) - 3715162112), 8192)
            self.assertEqual(int(limits["memory.max"]) % 4096, 0)
            self.assertEqual(profile_limits({}, meminfo), {})
            self.assertEqual(profile_limits({"LUMA_ANDROID_MEMORY_MAX_PERCENT": "0",
                                             "LUMA_ANDROID_TASKS_MAX": "-1"}, meminfo), {})

    def test_only_the_provider_container_start_is_extended(self):
        calls = []
        run = SimpleNamespace(user=lambda args, command, *rest, **kwargs: calls.append(command))
        defines = container_defines({"memory.max": "200"})
        install_container_policy(run, defines)
        run.user(None, list(UPSTREAM_START), output="background")
        run.user(None, ["lxc-info", "-P", "/var/lib/waydroid/lxc", "-n", "waydroid", "-sH"])
        self.assertEqual(calls[0][:6], UPSTREAM_START[:6])
        self.assertEqual(calls[0][6:-2], defines)
        self.assertEqual(calls[0][-2:], ["--", "/init"])
        self.assertEqual(calls[1][0], "lxc-info")
        install_container_policy(run, defines)
        with self.assertRaises(RuntimeError):
            install_container_policy(run, container_defines({}))

    def test_binder_session_sees_kernel_credentials(self):
        # The session admits system_server by Binder's sender UID. Inside a
        # user manager's self-only user namespace that UID reads as 65534, so
        # every notification was refused and system_server crashed on it.
        unit = (DATA / "luma-android-session.service").read_text()
        keys = {line.split("=", 1)[0] for line in unit.splitlines()
                if "=" in line and not line.lstrip().startswith("#")}
        self.assertEqual(sorted(keys.intersection(NAMESPACED)), [])
        self.assertIn("NoNewPrivileges", keys)
