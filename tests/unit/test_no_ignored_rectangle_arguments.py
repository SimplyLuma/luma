# SPDX-License-Identifier: Apache-2.0
"""PyGObject ignores arguments to boxed types, so Gdk.Rectangle(x, y, 1, 1) is a
rectangle at 0,0 -- popovers built with it open in their parent's corner."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATTERN = re.compile(r"Gdk\.Rectangle\(\s*[^)\s]")


class IgnoredRectangleArguments(unittest.TestCase):
    def test_no_source_passes_arguments_to_gdk_rectangle(self):
        offenders = []
        for path in (ROOT / "src").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if PATTERN.search(line) and "`" not in line and "silently" not in line and not line.lstrip().startswith("#"):
                    offenders.append(f"{path.relative_to(ROOT)}:{number}")
        self.assertEqual(offenders, [], "use Gdk.Rectangle() and set its fields, or luma_appkit.point_rectangle")


if __name__ == "__main__":
    unittest.main()
