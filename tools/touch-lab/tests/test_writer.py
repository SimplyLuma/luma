# SPDX-License-Identifier: Apache-2.0
"""The writer: sessions, compression, pause, stop and the disk cap (no root needed)."""
import gzip
import json
import os
import shutil
import tempfile
import threading
import time
import unittest

import _path  # noqa: F401
from touchlab import recorder


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def line(rec):
    return (json.dumps(rec, separators=(",", ":")) + "\n").encode()


class WriterTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="touchlab-writer-")

    def tearDown(self):
        shutil.rmtree(self.root)

    def run_writer(self, cap=1 << 30, floor=0, feed=None):
        rd, wr = os.pipe()
        w = recorder.Writer(self.root, os.getuid(), cap, floor)
        w.runtime = os.path.join(self.root, "run")
        out = {}
        t = threading.Thread(target=lambda: out.update(rc=w.run(rd)))
        orig = recorder.signal.signal
        recorder.signal.signal = lambda *a: None          # signal handlers only work in the main thread
        try:
            t.start()
            feed(wr, w)
            os.close(wr)
            t.join(20)
        finally:
            recorder.signal.signal = orig
        return w, out

    def test_session_and_pause(self):
        def feed(wr, w):
            os.write(wr, line([1, "H", {"boot_id": "abcdef12-x", "format": "luma-touch-lab/1"}]))
            os.write(wr, line([2, "h", "0300"]))
            time.sleep(0.3)
            open(os.path.join(self.root, "control", "pause"), "w").close()
            time.sleep(1.6)
            os.write(wr, line([3, "h", "0301"]))
            time.sleep(0.3)
        w, out = self.run_writer(feed=feed)
        self.assertEqual(out.get("rc"), 0)
        sdir = os.path.join(self.root, "sessions", os.listdir(os.path.join(self.root, "sessions"))[0])
        self.assertTrue(sdir.endswith("abcdef12"))
        meta = load(os.path.join(sdir, "session.json"))
        self.assertEqual(meta["boot_id"], "abcdef12-x")
        with gzip.open(os.path.join(sdir, "stream-000001.jsonl.gz")) as fh:
            recs = [json.loads(l) for l in fh.read().splitlines()]
        tags = [r[1] for r in recs]
        self.assertEqual(tags[0], "H")
        self.assertIn([2, "h", "0300"], recs)
        self.assertNotIn([3, "h", "0301"], recs)            # dropped while paused
        self.assertTrue(any(r[1] == "s" and r[2].get("event") == "pause" for r in recs))
        self.assertEqual(w.dropped, 1)
        st = load(os.path.join(self.root, "control", "status.json"))
        self.assertEqual(st["paused"], "user")

    def test_stop_file(self):
        def feed(wr, w):
            os.write(wr, line([1, "H", {"boot_id": "b"}]))
            time.sleep(0.2)
            open(os.path.join(self.root, "control", "stop"), "w").close()
            time.sleep(1.8)
        w, out = self.run_writer(feed=feed)
        self.assertTrue(w.stop)

    def test_disk_cap(self):
        def feed(wr, w):
            os.write(wr, line([1, "H", {"boot_id": "c"}]))
            time.sleep(1.5)
            os.write(wr, line([2, "h", "03"]))
            time.sleep(0.2)
        w, out = self.run_writer(cap=1, feed=feed)
        self.assertEqual(w.paused_by, "disk")


if __name__ == "__main__":
    unittest.main()
