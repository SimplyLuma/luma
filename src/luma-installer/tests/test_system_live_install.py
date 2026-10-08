# SPDX-License-Identifier: Apache-2.0
"""ADR-038: Depot applies system RPM installs and removals live when it can."""

import json
import subprocess
import unittest
from unittest import mock

from luma_installer import system_helper


def completed(argv, code=0, out=""):
    return subprocess.CompletedProcess(argv, code, out, "")


def status(staged_base=None):
    deployments = [{"booted": True, "checksum": "a", "base-checksum": "base1"}]
    if staged_base:
        deployments.insert(0, {"staged": True, "checksum": "b", "base-checksum": staged_base})
    return json.dumps({"deployments": deployments})


class LiveInstall(unittest.TestCase):
    def run_with(self, *, staged_base, apply_code=0):
        calls = []

        def fake(argv, **kwargs):
            calls.append(list(argv))
            if argv[:2] == ["/usr/bin/rpm-ostree", "status"]:
                return completed(argv, 0, status(staged_base))
            if argv[:2] == ["/usr/bin/rpm-ostree", "apply-live"]:
                return completed(argv, apply_code)
            return completed(argv)
        with mock.patch.object(system_helper.subprocess, "run", side_effect=fake), \
                mock.patch.dict("sys.modules", {"luma_install_commands": None}):
            restart = system_helper.run_backend("rpm", system_helper.Path("/tmp/x.rpm"))
        return restart, calls

    def test_layered_package_applied_live(self):
        restart, calls = self.run_with(staged_base="base1")
        self.assertFalse(restart)
        self.assertIn(["/usr/bin/rpm-ostree", "apply-live"], calls)

    def test_waiting_update_means_restart(self):
        restart, calls = self.run_with(staged_base="base2")
        self.assertTrue(restart)
        self.assertNotIn(["/usr/bin/rpm-ostree", "apply-live"], calls)

    def test_failed_live_apply_means_restart(self):
        restart, _ = self.run_with(staged_base="base1", apply_code=1)
        self.assertTrue(restart)


if __name__ == "__main__":
    unittest.main()
