# SPDX-License-Identifier: Apache-2.0
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("LUMA_SETUP_FIXTURE", "1")

try:
    from luma_setup import welcome
except (ImportError, ValueError) as error:  # no GTK 4 on this machine
    welcome = None
    REASON = str(error)


@unittest.skipIf(welcome is None, "GTK 4 is not available")
class Welcome(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.system = Path(tempfile.mkdtemp())
        patches = [mock.patch.object(welcome, "_state_dir", lambda: self.state / "luma-setup"),
                   mock.patch.object(welcome, "SYSTEM_WELCOME", self.system)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_only_for_an_account_first_boot_made_and_only_once(self):
        self.assertIsNone(welcome.pending("nick"))
        (self.system / "nick").write_text(json.dumps({"accessibility": {"zoom": True}}))
        self.assertEqual(welcome.pending("nick")["accessibility"], {"zoom": True})
        welcome.mark_done("left")
        self.assertIsNone(welcome.pending("nick"))

    def test_presets_name_real_shelf_values(self):
        for keys in welcome.DASH_PRESETS.values():
            self.assertIn(keys["shelf-group-layout"], ("centered-combined", "split-ends", "luma"))
            self.assertIn(keys["shelf-surface-mode"], ("separate", "connected"))
            self.assertIn(keys["shelf-edge-mode"], ("floating", "protruding"))
        self.assertTrue(welcome.DASH_PRESETS["redmond"]["shelf-span-full"])


if __name__ == "__main__":
    unittest.main()
