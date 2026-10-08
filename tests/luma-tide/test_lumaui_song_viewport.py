# SPDX-License-Identifier: Apache-2.0
import unittest
from luma_tide.presentation import SONG_ROW_HEIGHT, song_viewport


class SongViewportTests(unittest.TestCase):
    def test_viewport_covers_partial_rows_and_stays_inside_the_library(self):
        for offset in (-100, 0, 1, 100000, 919200, 1000000):
            first, last = song_viewport(20000, offset, 740)
            self.assertLessEqual(0, first)
            self.assertLessEqual(first, last)
            self.assertLessEqual(last, 20000)
            self.assertLessEqual(last - first, 34)
            for index in range(20000):
                if index * SONG_ROW_HEIGHT < offset + 740 and (index + 1) * SONG_ROW_HEIGHT > offset:
                    self.assertLessEqual(first, index)
                    self.assertGreater(last, index)
