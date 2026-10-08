# SPDX-License-Identifier: MPL-2.0
from pathlib import Path
import shutil
import tempfile
import unittest

import _paths  # noqa: F401
from luma_audio_devices.migration import TEMPORARY_OVERRIDE_NAME, remove_temporary_airplay_override


class TemporaryOverride(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name) / "config"
        self.dropins = self.home / "pipewire" / "pipewire.conf.d"
        self.dropins.mkdir(parents=True)
        self.system = Path(self.directory.name) / "40-luma-network-audio-opt-in.conf"
        self.system.write_text("context.properties = { module.raop = false }\n", encoding="utf-8")
        self.original = _paths.FIXTURES / TEMPORARY_OVERRIDE_NAME

    def tearDown(self):
        self.directory.cleanup()

    def test_identical_copy_is_removed(self):
        shutil.copy(self.original, self.dropins / TEMPORARY_OVERRIDE_NAME)
        self.assertEqual(remove_temporary_airplay_override(self.home, self.system), "removed")
        self.assertFalse((self.dropins / TEMPORARY_OVERRIDE_NAME).exists())

    def test_other_dropins_are_untouched(self):
        shutil.copy(self.original, self.dropins / TEMPORARY_OVERRIDE_NAME)
        (self.dropins / "20-mine.conf").write_text("# mine\n", encoding="utf-8")
        remove_temporary_airplay_override(self.home, self.system)
        self.assertTrue((self.dropins / "20-mine.conf").exists())

    def test_edited_copy_stays(self):
        data = self.original.read_bytes() + b"# my change\n"
        (self.dropins / TEMPORARY_OVERRIDE_NAME).write_bytes(data)
        self.assertEqual(remove_temporary_airplay_override(self.home, self.system), "kept-modified")
        self.assertEqual((self.dropins / TEMPORARY_OVERRIDE_NAME).read_bytes(), data)

    def test_not_removed_without_the_system_fragment(self):
        shutil.copy(self.original, self.dropins / TEMPORARY_OVERRIDE_NAME)
        self.system.unlink()
        self.assertEqual(remove_temporary_airplay_override(self.home, self.system), "kept-no-system-fragment")
        self.assertTrue((self.dropins / TEMPORARY_OVERRIDE_NAME).exists())

    def test_absent(self):
        self.assertEqual(remove_temporary_airplay_override(self.home, self.system), "absent")


if __name__ == "__main__":
    unittest.main()
