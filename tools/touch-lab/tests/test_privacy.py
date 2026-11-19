# SPDX-License-Identifier: Apache-2.0
"""The recorder must never keep anything that identifies a key."""
import json
import os
import struct
import unittest

import _path  # noqa: F401
from touchlab import recorder

EV_KEY, EV_SYN, EV_MSC = 1, 0, 4


class Keyboard(unittest.TestCase):
    def collect(self, events):
        rd, wr = os.pipe()
        c = recorder.Collector.__new__(recorder.Collector)
        c.out, c.out_dropped, c.stop = bytearray(), 0, False
        c.out_fd = wr
        src = recorder.Source("keyboard", "/dev/input/event3", rd, 2)
        os.write(wr, b"".join(struct.pack("llHHi", 1, 2, *e) for e in events))
        c.read_evdev(src, 123_456_789)
        os.close(wr)
        data = b""
        while True:
            chunk = os.read(rd, 65536)
            if not chunk:
                break
            data += chunk
        os.close(rd)
        return [json.loads(l) for l in data.splitlines()]

    def test_only_press_times(self):
        events = [(EV_MSC, 4, 0x1E), (EV_KEY, 30, 1), (EV_SYN, 0, 0), (EV_KEY, 30, 2), (EV_KEY, 30, 0),
                  (EV_KEY, 57, 1), (EV_KEY, 0x110, 1)]
        recs = self.collect(events)
        self.assertEqual(recs, [[120_000_000, "k", 2], [120_000_000, "k", 2]])
        blob = json.dumps(recs)
        for leak in ("30", "57", "1e", "30,", "scan"):
            self.assertNotIn(leak, blob.replace("120000000", ""))

    def test_libinput_key_lines_dropped(self):
        s = recorder.sanitize_libinput_line
        self.assertIsNone(s(" event3   KEYBOARD_KEY      +1.000s\tKEY_A (30) pressed", "event6"))
        self.assertIsNone(s(" event3   KEYBOARD_KEY      +1.000s\t*** (-1) pressed", "event6"))
        self.assertIsNone(s("  12: event3  - something KEY_Q (16)", "event6"))
        self.assertEqual(s("  12: event6  - weird KEY_Q (16)", "event6"), "  12: event6  - weird KEY")
        self.assertIsNotNone(s("  12: event6  - palm: touch 0 (TOUCH_BEGIN), palm detected (typing)", "event6"))
        self.assertIsNotNone(s("  ... event3  - palm: dwt activated", "event6"))


if __name__ == "__main__":
    unittest.main()
