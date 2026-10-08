# SPDX-License-Identifier: Apache-2.0
"""Leaf supplies sample artwork and delegates ordinary covers to the kit."""
import json
from pathlib import Path
import unittest


FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
if not FIXTURES.exists():
    FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class CoverArtwork(unittest.TestCase):
    def test_every_sample_edition_has_valid_png_artwork(self):
        document = json.loads((FIXTURES / "leaf-v70.json").read_text(encoding="utf-8"))
        for book in document["books"]:
            with self.subTest(book=book["id"]):
                picture = FIXTURES / "leaf-covers" / f"{book['id']}.png"
                data = picture.read_bytes()
                self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_contents_miniature_has_its_own_natural_size(self):
        from gi.repository import Gtk
        from luma_leaf.covers import book_cover

        picture = FIXTURES / "leaf-covers" / "totc.png"
        cover = book_cover(title="A Tale of Two Cities", author="Charles Dickens", image=None,
                           width=34, edition={"art": str(picture)})
        self.assertEqual(cover.measure(Gtk.Orientation.HORIZONTAL, -1).natural, 34)
        self.assertEqual(cover.measure(Gtk.Orientation.VERTICAL, 34).natural, 51)


if __name__ == "__main__":
    unittest.main()
