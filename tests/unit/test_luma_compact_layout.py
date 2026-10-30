"""Compact action surfaces follow physical phone geometry, never app identity."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/luma-platform/appkit'))
from luma_appkit.compact_layout import bottom_attached, minimum_size


class CompactLayout(unittest.TestCase):
    def test_short_phone_and_rotation_attach(self):
        for width, height in ((360, 400), (400, 360), (480, 480), (360, 599)):
            self.assertTrue(bottom_attached(width, height, True))

    def test_taller_phones_and_tablets_keep_existing_presentation(self):
        for width, height in ((390, 844), (430, 932), (600, 360), (840, 1200)):
            self.assertFalse(bottom_attached(width, height, True))

    def test_narrow_desktop_and_unknown_monitor_do_not_attach(self):
        self.assertFalse(bottom_attached(360, 400, False))
        self.assertFalse(bottom_attached(0, 0, True))

    def test_fullscreen_minimum_cannot_exceed_monitor(self):
        self.assertEqual(minimum_size(460, 480, 360, 400, True), (360, 400))
        self.assertEqual(minimum_size(360, 420, 390, 844, True), (360, 420))
        self.assertEqual(minimum_size(280, 240, 360, 400, True), (280, 240))

    def test_desktop_and_unknown_monitor_keep_app_minimum(self):
        self.assertEqual(minimum_size(460, 480, 360, 400, False), (460, 480))
        self.assertEqual(minimum_size(460, 480, 0, 0, True), (460, 480))


if __name__ == '__main__':
    unittest.main()
