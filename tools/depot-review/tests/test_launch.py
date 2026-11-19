# SPDX-License-Identifier: Apache-2.0
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from depot_review import vm  # noqa: E402

W, H = 160, 100


def frame(boxes=()):
    """A dark frame with white boxes (x0, y0, x1, y1) in fractions of the frame."""
    px = bytearray(W * H * 3)
    for x0, y0, x1, y1 in boxes:
        for y in range(int(y0 * H), int(y1 * H)):
            for x in range(int(x0 * W), int(x1 * W)):
                i = (y * W + x) * 3
                px[i:i + 3] = b"\xff\xff\xff"
    return bytes(px)


class Launch(unittest.TestCase):
    def test_dock_and_top_bar_changes_are_not_a_window(self):
        base = frame()
        shell = frame([(0.1, 0.9, 0.9, 1.0), (0.4, 0.0, 0.6, 0.04)])
        self.assertLess(vm.changed_fraction(base, shell, W, H), vm.WINDOW_FRACTION)
        self.assertGreater(vm.changed_fraction(base, shell), vm.WINDOW_FRACTION)

    def test_window_in_the_app_area_counts(self):
        self.assertGreater(vm.changed_fraction(frame(), frame([(0.2, 0.15, 0.8, 0.75)]), W, H), 0.2)

    def test_blank_screen_is_detected(self):
        self.assertTrue(vm.is_blank(frame()))
        self.assertFalse(vm.is_blank(frame([(0.0, 0.0, 1.0, 0.5)])))

    def test_window_must_leave_when_the_app_stops(self):
        self.assertTrue(vm.window_verdict(0.35, 0.0))
        self.assertTrue(vm.window_verdict(0.35, 0.005))
        self.assertFalse(vm.window_verdict(0.35, 0.34))  # something else changed the screen
        self.assertFalse(vm.window_verdict(0.01, 0.0))   # nothing really appeared


if __name__ == "__main__":
    unittest.main()
