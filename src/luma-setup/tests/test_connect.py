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

    def test_qr_has_its_finder_patterns(self):
        rows = connect.qr_modules("https://connect.example/device?code=WDJBMJHT")
        if rows is None:
            self.skipTest("libqrencode is not installed")
        self.assertTrue(all(rows[0][:7]) and all(rows[6][:7]))    # top-left finder, first and last rows
        self.assertTrue(len(rows) >= 21 and all(len(r) == len(rows) for r in rows))


if __name__ == "__main__":
    unittest.main()
