# SPDX-License-Identifier: Apache-2.0
"""The Recents menu must anchor at the click, not the row's origin."""

import unittest

from luma_viewer import application


class PointRectangleTests(unittest.TestCase):
    def test_click_coordinates_are_set_on_the_boxed_rectangle(self):
        rectangle = application._point_rectangle(73.8, 21.9)
        self.assertEqual(
            (rectangle.x, rectangle.y, rectangle.width, rectangle.height),
            (73, 21, 1, 1),
        )


if __name__ == "__main__":
    unittest.main()
