# SPDX-License-Identifier: Apache-2.0
"""Circular application icons get Luma's rounded-square silhouette."""
import math
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src/luma-installer"))

from luma_installer import icon_plates  # noqa: E402
from luma_installer.backends import _best_icon_path  # noqa: E402


def disc(size, colour_at, glyph=None):
    """RGBA bytes of a disc whose colour at row y is colour_at(y)."""
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            inside = math.hypot(x + .5 - size / 2, y + .5 - size / 2) <= size / 2 - .5
            colour = glyph(x, y) if glyph and inside and glyph(x, y) else colour_at(y)
            pixels += bytes((*colour, 255 if inside else 0))
    return bytes(pixels)


class IconPlates(unittest.TestCase):
    def test_a_gradient_disc_gets_a_plate_of_its_own_gradient(self):
        size = 64
        top, bottom = (6, 24, 55), (19, 131, 180)
        colour = lambda y: tuple(round(a + (b - a) * y / (size - 1)) for a, b in zip(top, bottom))
        # A white arm reaching the rim, as Steam's does, is set aside.
        arm = lambda x, y: (240, 240, 240) if 28 <= y <= 36 and x < 30 else None
        rows = icon_plates.gradient_rows(disc(size, colour, arm), size, size, size * 4, 4)
        self.assertIsNotNone(rows)
        self.assertLess(max(abs(a - b) for a, b in zip(rows[0], top)), 16)
        self.assertLess(max(abs(a - b) for a, b in zip(rows[-1], bottom)), 16)

    def test_a_square_icon_and_a_multicoloured_disc_are_left_alone(self):
        size = 64
        square = bytes((10, 20, 30, 255)) * (size * size)
        self.assertIsNone(icon_plates.gradient_rows(square, size, size, size * 4, 4))
        noisy = disc(size, lambda y: ((y * 37) % 255, (y * 91) % 255, (y * 13) % 255))
        self.assertIsNone(icon_plates.gradient_rows(noisy, size, size, size * 4, 4))

    def test_luma_applications_are_never_plated(self):
        self.assertTrue("org.projectluma.Leaf".startswith(icon_plates.LUMA_ICON_PREFIXES))

    def test_the_sharpest_copy_of_an_icon_is_chosen(self):
        self.assertEqual(_best_icon_path(["/i/hicolor/64x64/apps/a.png", "/i/hicolor/512x512/apps/a.png"]),
                         "/i/hicolor/512x512/apps/a.png")
        self.assertEqual(_best_icon_path(["/i/hicolor/512x512/apps/a.png", "/i/hicolor/scalable/apps/a.svg"]),
                         "/i/hicolor/scalable/apps/a.svg")
        self.assertEqual(_best_icon_path([]), "")


if __name__ == "__main__":
    unittest.main()
