#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""luma_appkit.svg_icons: an app icon's SVG drawn at the size shown, cached, in this process."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/luma-platform/appkit"))
os.environ.setdefault("GSETTINGS_BACKEND", "memory")

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    from luma_appkit import svg_icons
    READY = Gtk.init_check() and svg_icons.Rsvg is not None
except (ImportError, ValueError):
    READY = False

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">'
       '<filter id="b"><feGaussianBlur stdDeviation="20"/></filter>'
       '<rect x="64" y="64" width="896" height="896" rx="200" fill="#2582dc" filter="url(#b)"/></svg>')


@unittest.skipUnless(READY, "needs GTK 4 and librsvg's typelib")
class SvgIcons(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        apps = Path(self.dir.name, "hicolor/scalable/apps")
        apps.mkdir(parents=True)
        (apps / "org.example.Fixture.svg").write_text(SVG)
        Path(self.dir.name, "hicolor/index.theme").write_text(
            "[Icon Theme]\nName=hicolor\nDirectories=scalable/apps\n\n"
            "[scalable/apps]\nSize=128\nMinSize=8\nMaxSize=1024\nType=Scalable\n")
        theme = Gtk.IconTheme.get_for_display(Gtk.Window().get_display())
        theme.add_search_path(self.dir.name)
        svg_icons._CACHE.clear()

    def tearDown(self):
        self.dir.cleanup()

    def test_svg_icon_at_twice_its_size_and_cached(self):
        paintable = svg_icons.app_icon_paintable("org.example.Fixture", 48)
        self.assertIsInstance(paintable, svg_icons.SvgIcon)
        self.assertEqual(paintable.get_intrinsic_width(), 48)
        self.assertEqual([key[2] for key in svg_icons._CACHE], [96])   # rendered at 96, not the file's 1024
        texture = next(iter(svg_icons._CACHE.values()))
        self.assertEqual((texture.get_width(), texture.get_height()), (96, 96))
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, 48, 48)
        self.assertIsNotNone(snapshot.to_node())
        svg_icons.app_icon_paintable("org.example.Fixture", 48)
        self.assertEqual(len(svg_icons._CACHE), 1)                     # a second page reuses it

    def test_unknown_app_is_none(self):
        self.assertIsNone(svg_icons.app_icon_paintable("org.example.Missing", 48))


if __name__ == "__main__":
    unittest.main()
