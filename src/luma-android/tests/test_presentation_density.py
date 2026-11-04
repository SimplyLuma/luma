import unittest
from unittest import mock

from luma_android import presentation


class PresentationDensity(unittest.TestCase):
    def test_touch_presentations_take_the_output_density(self):
        self.assertEqual(presentation.presentation_density("mobile", "420", "3.0"), 420)
        self.assertEqual(presentation.presentation_density("tablet", "280", "2.0"), 280)

    def test_desktop_takes_180_per_scale_step(self):
        self.assertEqual(presentation.presentation_density("desktop", "420", "3.0"), 540)
        self.assertEqual(presentation.presentation_density("desktop", "0", "1.25"), 225)

    def test_unknown_values_keep_the_boot_density(self):
        for args in (("mobile", "", "3"), ("mobile", "0", "3"), ("tablet", "x", "2"),
                     ("desktop", "420", ""), ("desktop", "420", "0"), ("desktop", "420", "40")):
            with self.subTest(args=args):
                self.assertEqual(presentation.presentation_density(*args), 0)

    def test_without_shell_state_the_device_class_decides(self):
        with mock.patch("gi.repository.Gio.bus_get_sync", side_effect=RuntimeError):
            self.assertEqual(presentation.current_presentation("handheld"), "mobile")
            self.assertEqual(presentation.current_presentation("tablet"), "tablet")
            self.assertEqual(presentation.current_presentation("desktop"), "desktop")


if __name__ == "__main__":
    unittest.main()
