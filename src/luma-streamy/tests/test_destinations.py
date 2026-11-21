# SPDX-License-Identifier: Apache-2.0
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from luma_streamy.destinations import DestinationStore, new, shared_encodes  # noqa: E402


class DestinationTest(unittest.TestCase):
    def test_store_keeps_rows_and_a_recording(self):
        path = Path(tempfile.mkdtemp()) / "destinations.json"
        store = DestinationStore.load(path)
        self.assertEqual([r.platform for r in store.rows], ["record"])
        twitch = store.add("twitch")
        store.add("youtube", "vertical")
        again = DestinationStore.load(path)
        self.assertEqual([r.platform for r in again.rows], ["twitch", "youtube", "record"])
        self.assertEqual(again.get(twitch.id).canvas, "landscape")
        again.remove(again.rows[-1].id)  # the recording row stays
        self.assertEqual(len(again.rows), 3)

    def test_urls(self):
        self.assertEqual(new("twitch").url("abc"), "rtmps://live.twitch.tv/app/abc")
        row = new("custom")
        row.server = "srt://example.org:9000?streamid=x"
        self.assertEqual(row.url("ignored"), "srt://example.org:9000?streamid=x")
        row.server = "example.org"
        with self.assertRaises(ValueError):
            row.url("k")

    def test_one_encode_per_canvas_and_preset(self):
        a, b, c = new("twitch"), new("custom"), new("twitch", "vertical")
        off = new("kick")
        off.enabled = False
        groups = shared_encodes([a, b, c, off, new("record")])
        self.assertEqual(sorted(len(g) for g in groups.values()), [1, 2])


if __name__ == "__main__":
    unittest.main()
