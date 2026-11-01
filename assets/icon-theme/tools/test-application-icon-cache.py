#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise real SVG rendering, alias preservation and installed theme checks."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "render_application_icons", Path(__file__).with_name("render-application-icons.py"))
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


@unittest.skipUnless(shutil.which("rsvg-convert"), "librsvg2-tools is required")
class ApplicationIconCache(unittest.TestCase):
    def test_artwork_aliases_vector_fallback_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apps = root / "scalable/apps"
            apps.mkdir(parents=True)
            artwork = b'<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128"><rect x="8" y="8" width="112" height="112" rx="24" fill="#20a050"/></svg>'
            for name in ("org.example.App", "app-alias"):
                (apps / (name + ".svg")).write_bytes(artwork)
            (root / "index.theme").write_text(
                "[Icon Theme]\nName=Test\nInherits=Adwaita,hicolor\nDirectories=scalable/apps\n"
                "\n[scalable/apps]\nContext=Applications\nSize=128\nType=Scalable\nMinSize=8\nMaxSize=512\n")
            count = renderer.prepare(root)
            self.assertEqual(count, 2 * len(renderer.VARIANTS))
            self.assertEqual(renderer.prepare(root, check=True), count)
            for size, scale in renderer.VARIANTS:
                baked = root / renderer.icon_directory(size, scale) / "org.example.App.png"
                self.assertEqual(renderer.png_size(baked), (size * scale, size * scale))
                self.assertEqual(baked.read_bytes(), baked.with_name("app-alias.png").read_bytes())
            self.assertEqual((apps / "org.example.App.svg").read_bytes(), artwork)
            self.assertIn("Inherits=Adwaita,hicolor", (root / "index.theme").read_text())
            # Use GTK's real lookup, including how it enumerates window-icon
            # sizes. A nominal 96px PNG does not cover a 96 logical-pixel icon
            # on a 2x display unless its Scale metadata is correct.
            import gi
            if hasattr(gi, "disable_legacy_autoinit"):
                gi.disable_legacy_autoinit()
            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk
            Gtk.init_check()  # Register GTK's fallback resources, also without a display.
            theme = Gtk.IconTheme.new()
            theme.set_search_path([str(root.parent), *theme.get_search_path()])
            theme.set_theme_name(root.name)
            for scale in (1, 2, 3, 4):
                for logical in theme.get_icon_sizes("org.example.App"):
                    icon = theme.lookup_icon("org.example.App", None, 48 if logical < 0 else logical,
                                             scale, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.NONE)
                    self.assertTrue(icon.get_file().get_path().endswith(".png"), (logical, scale))
            baked.write_bytes(b"invalid PNG")
            with self.assertRaises(ValueError):
                renderer.prepare(root, check=True)


if __name__ == "__main__":
    unittest.main()
