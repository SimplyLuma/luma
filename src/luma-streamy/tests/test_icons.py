# SPDX-License-Identifier: Apache-2.0
"""Every LumaUI glyph Streamy names exists in the kit's icon set (a missing one draws as a blank box)."""
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACTIONS = next((p / "assets/icon-theme/Prairie/symbolic/actions" for p in HERE.parents
                if (p / "assets/icon-theme/Prairie/symbolic/actions").is_dir()), None)
#: Names that are not LumaUI glyphs (system icons resolved by the theme) or not icons at all.
NOT_GLYPHS = {"org.projectluma.Streamy"}


def names() -> set[str]:
    found = set()
    for path in (HERE.parent / "luma_streamy").glob("*.py"):
        text = path.read_text()
        found |= set(re.findall(r'icon="([a-z0-9-]+)"', text))
        found |= set(re.findall(r'(?:BarAction|RowAction|IconOnlyButton|BarTile|PanelSwitch)\(\s*"([a-z0-9-]+)"', text))
        found |= set(re.findall(r'DockTile\("[a-z]+", "[^"]+", "([a-z0-9-]+)"', text))
    from luma_streamy.model import KINDS

    found |= {icon for _name, icon, _p, _s in KINDS.values()}
    # Look presets: ("key": ("Label", "icon", {...})) in window.py.
    window = (HERE.parent / "luma_streamy/window.py").read_text()
    found |= set(re.findall(r'"[a-z]+": \("[A-Z][a-z]+", "([a-z0-9-]+)", dict\(', window))
    return found - NOT_GLYPHS


@unittest.skipIf(ACTIONS is None, "no icon theme in this tree")
class IconsTest(unittest.TestCase):
    def test_every_glyph_exists(self):
        missing = sorted(n for n in names() if not (ACTIONS / f"lumaui-{n}-symbolic.svg").is_file())
        self.assertEqual(missing, [], f"glyphs Streamy uses that the kit doesn't have: {missing}")


if __name__ == "__main__":
    unittest.main()
