# SPDX-License-Identifier: Apache-2.0
"""Viewer's resize detector uses the same percent as the visible kit control."""
from types import SimpleNamespace
import unittest

from luma_viewer.composition import ViewerUI


class ZoomChangeDetectionTests(unittest.TestCase):
    def test_half_percent_tracks_the_visible_zoom_control(self):
        window = SimpleNamespace(pixbuf=object(), marks_area=object(),
                                 _viewport=lambda: SimpleNamespace(scale=0.425))
        self.assertEqual(ViewerUI._zoom_text(window), "43%")


if __name__ == "__main__":
    unittest.main()
