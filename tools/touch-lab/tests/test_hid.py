# SPDX-License-Identifier: Apache-2.0
import unittest

import _path  # noqa: F401
from touchlab import hid, synth


class Descriptor(unittest.TestCase):
    def setUp(self):
        self.desc = hid.parse_descriptor(synth.load_descriptor())
        self.layout = hid.TouchpadLayout(self.desc)

    def test_touchpad_report(self):
        lo = self.layout
        self.assertEqual(lo.report_id, 3)
        self.assertEqual(len(lo.slots), 5)
        self.assertEqual(sorted(lo.slots[0]), ["confidence", "contact_id", "pressure", "tip", "x", "y"])
        self.assertEqual(lo.x_range, (0, 4723))
        self.assertEqual(lo.y_range, (0, 2298))
        self.assertAlmostEqual(lo.x_mm, 149.6, places=3)
        self.assertAlmostEqual(lo.y_mm, 73.8, places=3)
        self.assertEqual(lo.pressure_range, (0, 32767))
        self.assertAlmostEqual(lo.scan_time_unit(), 1e-4)
        self.assertEqual(self.desc.report_bytes("input", 3), 40)

    def test_vendor_report_9(self):
        rows = {(r["kind"], r["report_id"]): r for r in self.desc.summary()}
        for kind in ("input", "output"):
            r = rows[(kind, 9)]
            self.assertEqual(r["bytes"], 21)          # report ID + 20 opaque bytes
            self.assertEqual(r["usages"], ["0xff00:0x01"])

    def test_features(self):
        rows = {(r["kind"], r["report_id"]): r["usages"] for r in self.desc.summary()}
        self.assertIn("latency mode", rows[("feature", 10)])
        self.assertIn("haptic intensity", rows[("feature", 11)])
        self.assertEqual(rows[("feature", 8)], ["contact count maximum", "pad type"])

    def test_round_trip(self):
        cs = [hid.Contact(3, True, False, 100, 200, 500), hid.Contact(4, True, True, 4000, 2000, 9000)]
        raw = self.layout.encode(1234, cs, 1)
        self.assertEqual(len(raw), 40)
        rep = self.layout.decode(raw)
        self.assertEqual(rep.scan_time, 1234)
        self.assertEqual(rep.contact_count, 2)
        self.assertEqual(rep.buttons, 1)
        self.assertEqual(rep.contacts, cs)

    def test_other_reports_ignored(self):
        self.assertIsNone(self.layout.decode(bytes([9]) + bytes(20)))


if __name__ == "__main__":
    unittest.main()
