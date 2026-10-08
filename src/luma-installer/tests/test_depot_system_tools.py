# SPDX-License-Identifier: Apache-2.0
"""My apps: "System tools you installed" from rpm-ostree's record (ADR-038)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "luma-depot"))

from luma_depot import system_tools  # noqa: E402


def status(booted, pending=None):
    deployments = []
    if pending is not None:
        deployments.append({"staged": True, "booted": False, "requested-packages": pending})
    deployments.append({"staged": False, "booted": True, "requested-packages": booted})
    return {"deployments": deployments}


class SystemToolsTest(unittest.TestCase):
    def query(self, running):
        return lambda name: ("3.4.1-3.fc44", f"{name} summary") if name in running else None

    def test_live_added_package_is_installed(self):
        rows = system_tools.parse(status([], ["htop"]), self.query({"htop"}))
        self.assertEqual([(row.name, row.state) for row in rows], [("htop", "installed")])
        self.assertEqual(rows[0].detail, "htop summary")

    def test_added_but_not_running_waits_for_restart(self):
        rows = system_tools.parse(status([], ["htop"]), self.query(set()))
        self.assertEqual(rows[0].state, "after-restart")

    def test_removed_but_still_running(self):
        rows = system_tools.parse(status(["htop"], []), self.query({"htop"}))
        self.assertEqual(rows[0].state, "removal-pending")

    def test_removed_live_is_gone(self):
        self.assertEqual(system_tools.parse(status(["htop"], []), self.query(set())), [])

    def test_local_package_files_by_name(self):
        data = {"deployments": [{"booted": True, "requested-local-packages": ["foo-1.0-1.fc44.x86_64"]}]}
        rows = system_tools.parse(data, self.query({"foo"}))
        self.assertEqual(rows[0].name, "foo")

    def test_base_packages_never_listed(self):
        self.assertEqual(system_tools.parse(status([]), self.query({"firefox"})), [])


if __name__ == "__main__":
    unittest.main()
