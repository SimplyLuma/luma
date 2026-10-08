# SPDX-License-Identifier: Apache-2.0
"""The Shell's glass veil is the platform's.

Quick Options and an application's context menu are one material only while
the Shell theme's veil equals `surface_treatments.*.surface_veil` in
config/shared/design-tokens.json. The checker is `tools/check-shell-veil.py`;
this runs its self-test first, because a checker that has never been seen to
go red is indistinguishable from one that does nothing, and then reads the veil
out of the Shell patch series. The Shell build runs the same checker over the
packaged theme sheets.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "tools/check-shell-veil.py"


class ShellVeilTests(unittest.TestCase):
    def run_checker(self, *args):
        return subprocess.run([sys.executable, str(CHECKER), *args],
                              capture_output=True, text=True, check=False)

    def test_checker_still_detects_drift(self):
        result = self.run_checker("--self-test")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("self-test: 0 of", result.stdout)

    def test_shell_veil_equals_the_platform_tokens(self):
        result = self.run_checker("--patches", str(REPO_ROOT / "patches/gnome-shell"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # A series in which no patch adds the veil fails; this pins that the
        # pass came from reading it.
        self.assertIn("veil from 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
