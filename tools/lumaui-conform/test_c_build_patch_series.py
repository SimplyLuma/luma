"""Tests for the patch selection contract used by the native conform builder."""

import json
import pathlib
import subprocess
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("c_build.sh")


class PatchSeriesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.patches = self.root / "patches"
        self.patches.mkdir()
        for name in ("0000-spec.patch", "0023-privacy.patch", "0024-users.patch",
                     "0025-users.patch", "0027-style.patch"):
            (self.patches / name).write_text(name)
        self.scenario = self.root / "scenario.json"

    def select(self, series_marker=None):
        build = {"patched_upstream": "gnome-control-center", "patches": "patches"}
        if series_marker is not None:
            build["patch_series"] = series_marker
        self.scenario.write_text(json.dumps({"gtk": {"build": build}}))
        return subprocess.run(
            ["bash", str(SCRIPT), "--list-worktree-patches", str(self.scenario),
             str(self.patches)], capture_output=True, check=False)

    @staticmethod
    def names(result):
        return [pathlib.Path(p.decode()).name for p in result.stdout.split(b"\0") if p]

    def test_manifest_selects_only_active_patches_in_declared_order(self):
        result = self.select(["0025-users.patch", "0023-privacy.patch", "0027-style.patch"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names(result),
                         ["0025-users.patch", "0023-privacy.patch", "0027-style.patch"])

    def test_legacy_directory_order_remains_available(self):
        result = self.select()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names(result), ["0023-privacy.patch", "0024-users.patch",
                                              "0025-users.patch", "0027-style.patch"])

    def test_missing_listed_patch_fails(self):
        result = self.select(["0026-missing.patch"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"listed patch does not exist", result.stderr)
        self.assertEqual(result.stdout, b"")

    def test_duplicate_and_invalid_entries_fail(self):
        for series in (["0025-users.patch", "0025-users.patch"],
                       ["0000-spec.patch"], ["../0025-users.patch"],
                       "0025-users.patch"):
            with self.subTest(series=series):
                result = self.select(series)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")


if __name__ == "__main__":
    unittest.main()
