"""Terminal on a TV: the saved scripts it may run, and what reads as a failure.

GTK-free, except the last class, which needs GTK 4 and VTE and skips without them.
"""

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "luma-platform" / "appkit"))

from luma_terminal import tv_scripts  # noqa: E402


class SavedScripts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "luma-terminal" / "tv-scripts.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_the_first_time_luma_s_monitors_are_written(self):
        scripts = tv_scripts.load_scripts(self.path)
        self.assertEqual([s["name"] for s in scripts], ["System log", "Updates", "Disk and memory"])
        self.assertEqual(json.loads(self.path.read_text()), scripts)

    def test_an_unreadable_file_is_left_alone(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{ not json")
        self.assertEqual(len(tv_scripts.load_scripts(self.path)), 3)
        self.assertEqual(self.path.read_text(), "{ not json")

    def test_only_complete_scripts_are_offered(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps([
            {"name": "Build", "command": "make -C ~/src/app"},
            {"name": "", "command": "true"}, {"name": "No command"}, "make", {"name": 3, "command": "x"}]))
        self.assertEqual(tv_scripts.load_scripts(self.path),
                         [{"name": "Build", "command": "make -C ~/src/app", "cwd": "~"}])

    def test_saving_from_a_computer(self):
        home = str(Path.home())
        saved = tv_scripts.save_script("  make check  ", os.path.join(home, "src", "app"), self.path)
        self.assertEqual(saved, {"name": "make check", "command": "make check", "cwd": "~/src/app"})
        tv_scripts.save_script("journalctl -f", "/var/log", self.path)
        # Saving one already there moves it to the end; it is never listed twice.
        tv_scripts.save_script("make check", home, self.path)
        commands = [s["command"] for s in tv_scripts.load_scripts(self.path)]
        self.assertEqual(commands[-2:], ["journalctl -f", "make check"])
        self.assertEqual(commands.count("make check"), 1)
        with self.assertRaises(ValueError):
            tv_scripts.save_script("   ", home, self.path)

    def test_a_long_command_is_named_short(self):
        name = tv_scripts.script_name("rsync -av --delete " + "x" * 80)
        self.assertEqual(len(name), tv_scripts.NAME_LENGTH)
        self.assertTrue(name.endswith("…"))

    def test_the_config_folder_is_honoured(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.temp.name}):
            self.assertEqual(tv_scripts.scripts_path(), self.path)


try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Vte", "3.91")
    from gi.repository import Gtk, Vte  # noqa: F401
    HAS_GTK = Gtk.init_check() if hasattr(Gtk, "init_check") else True
except (ImportError, ValueError):
    HAS_GTK = False


@unittest.skipUnless(HAS_GTK, "needs GTK 4, VTE and a display")
class Failures(unittest.TestCase):
    """Left / Right jump between lines that read like a failure: a raw session has no verbs."""

    def test_what_reads_as_a_failure(self):
        from luma_terminal.ui_tv import FAILURE
        failure = re.compile(FAILURE)
        for line in ("error: linker failed", "FAILED tests/test_x.py", "Traceback (most recent call last):",
                     "Permission denied", "✗ check", "kernel panic"):
            self.assertTrue(failure.search(line), line)
        for line in ("errorless", "0 failures expected", "terror", "Built in 3.2s", "deny-list updated"):
            self.assertFalse(failure.search(line), line)


if __name__ == "__main__":
    unittest.main()
