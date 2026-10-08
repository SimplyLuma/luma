# SPDX-License-Identifier: Apache-2.0
"""After staging, the staged release brings GRUB's hidden menu up to date; that
never stops an update."""

from pathlib import Path
import json
import subprocess
import tempfile
import unittest

import fakes
from fakes import commit, graph_doc, release
from luma_update import bootmenu


class Command(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root)

    def carry(self, *paths):
        for path in paths:
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text("x\n")

    def test_release_without_the_helper_or_piece_is_left_to_itself(self):
        self.assertIsNone(bootmenu.command(self.root))
        self.carry(bootmenu.HELPER)
        self.assertIsNone(bootmenu.command(self.root))
        self.assertIsNone(bootmenu.refresh(self.root, run=lambda *a, **k: self.fail("ran")))

    def test_staged_release_helper_runs_in_a_private_mount_namespace(self):
        self.carry(bootmenu.HELPER, bootmenu.PIECE)
        argv = bootmenu.command(self.root)
        self.assertEqual(argv[:2], ["systemd-run", "--wait"])
        self.assertIn("MountFlags=slave", argv)
        self.assertIn(str(self.root / bootmenu.HELPER), argv)
        self.assertEqual(argv[argv.index("--source") + 1], str(self.root / bootmenu.PIECE))

    def test_helper_failure_is_reported(self):
        self.carry(bootmenu.HELPER, bootmenu.PIECE)
        done = subprocess.CompletedProcess([], 1, "luma-boot-hidden-menu: could not change x\n", "")
        with self.assertRaisesRegex(RuntimeError, "exited 1: luma-boot-hidden-menu: could not change x"):
            bootmenu.refresh(self.root, run=lambda *a, **k: done)
        ok = subprocess.CompletedProcess([], 0, "luma-boot-hidden-menu: /boot/grub2/grub.cfg is current\n", "")
        self.assertEqual(bootmenu.refresh(self.root, run=lambda *a, **k: ok),
                         "luma-boot-hidden-menu: /boot/grub2/grub.cfg is current")


class Staging(unittest.TestCase):
    def setUp(self):
        self.rig = fakes.Rig()
        self.backend = self.rig.backend
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))

    def tearDown(self):
        self.rig.close()

    def test_staged_release_refreshes_the_menu(self):
        engine = self.rig.engine()
        engine.automatic()
        self.assertEqual(self.backend.boot_menu_calls, [commit(2)])
        self.assertEqual(engine.status().state, "staged")

    def test_a_failed_refresh_never_stops_the_update(self):
        self.backend.boot_menu_fail_with = RuntimeError("luma-boot-hidden-menu exited 1")
        engine = self.rig.engine()
        with self.assertLogs("luma-update", level="WARNING") as logs:
            engine.automatic()
        status = engine.status()
        self.assertEqual((status.state, status.staged_commit), ("staged", commit(2)))
        self.assertNotIn(("cleanup",), self.backend.calls)
        self.assertTrue(any("retries at boot" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
