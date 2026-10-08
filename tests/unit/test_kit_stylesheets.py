# SPDX-License-Identifier: Apache-2.0
"""No application loads its style sheet behind the kit's back.

`add_style_sheet()` reloads a sheet when the surface treatment changes. A
provider the application makes itself does not, so the application keeps the
palette it started with while the windows beside it follow — one of the ways
two windows on one Glass desktop ended up different colours.

The checker is `tools/check-kit-stylesheets.py`. This runs it, and runs its
self-test first, because a checker that has never been seen to go red is
indistinguishable from one that does nothing.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "tools/check-kit-stylesheets.py"


class KitStylesheetTests(unittest.TestCase):
    def test_checker_still_detects_its_motivating_case(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER), "--self-test"],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0,
                         f"{result.stdout}\n{result.stderr}")

    def test_no_application_owns_its_own_style_provider(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER)],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout)
        # A scan that read nothing passes for the wrong reason.
        self.assertIn("read ", result.stderr)
        read = int(result.stderr.split("read ", 1)[1].split(" ", 1)[0])
        self.assertGreater(read, 100, f"the checker only read {read} files")

    def test_every_exclusion_gives_a_reason(self):
        allow = REPO_ROOT / "tools/private-css-providers.txt"
        entries = [line for line in allow.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.strip().startswith("#")]
        self.assertTrue(entries, "the allow list should name its exclusions")
        for entry in entries:
            path, _, reason = entry.partition(":")
            with self.subTest(path=path.strip()):
                self.assertTrue(reason.strip(), "an exclusion with no reason")
                self.assertTrue((REPO_ROOT / path.strip()).exists(),
                                f"{path.strip()} no longer exists; drop the entry")


if __name__ == "__main__":
    unittest.main()
