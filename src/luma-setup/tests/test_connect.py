# SPDX-License-Identifier: Apache-2.0
import unittest

try:
    from luma_setup import connect
except (ImportError, ValueError):  # no GLib
    connect = None


@unittest.skipIf(connect is None, "GLib is not available")
class Code(unittest.TestCase):
    def test_groups_of_four_upper_case(self):
        self.assertEqual(connect.show_code("bcdf ghjk"), "BCDF-GHJK")
        self.assertEqual(connect.show_code("WDJB-MJHT"), "WDJB-MJHT")

    def test_the_clear_alphabet_has_nothing_that_looks_alike(self):
        for ch in "AEIOUY0123456789":  # no vowels (I, O), no digits (0, 1)
            self.assertNotIn(ch, connect.CLEAR)
        self.assertEqual(len(connect.CLEAR), 20)


if __name__ == "__main__":
    unittest.main()
