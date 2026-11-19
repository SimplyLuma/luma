# SPDX-License-Identifier: Apache-2.0
"""Synthetic session -> contacts -> labels -> features -> report, with no recording."""
import csv
import math
import os
import random
import shutil
import tempfile
import unittest

import _path  # noqa: F401
from touchlab import contacts, dataset, features, hid, libinput_log, record_export, report, scenarios, synth
from touchlab.session import Session


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="touchlab-test-")
        cls.sdir = synth.build_session(cls.root, seed=11, trials_per=2, free_minutes=0.3)
        cls.joined = dataset.join(Session(cls.sdir, cls.root))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root)

    def test_tracks_and_labels(self):
        j = self.joined
        self.assertGreater(len(j.tracks), 40)
        labelled = [t for t in j.tracks if t.label]
        self.assertGreater(len(labelled), 0.7 * sum(1 for t in j.tracks if t.scenario and not t.scenario.startswith("free")))
        self.assertTrue(all(t.slot is not None for t in j.tracks), "every HID contact found its evdev slot")
        self.assertTrue(any(t.libinput for t in j.tracks), "libinput reasoning attached to contacts")
        self.assertTrue(any(t.marks for t in j.tracks), "natural-use marks reach contacts")
        self.assertTrue(any(t.kernel_palm_at for t in j.tracks))
        self.assertEqual({t.label_class for t in labelled}, {"intended", "accidental"})

    def test_features_causal_and_complete(self):
        j = self.joined
        for hz in (25, 100):
            rows = list(features.rows(j, hz))
            self.assertEqual(len(rows), len([t for t in j.tracks if t.samples]))
            for r, tr in zip(rows, [t for t in j.tracks if t.samples]):
                for k in features.FEATURES:
                    self.assertIn(k, r)
                    self.assertTrue(math.isfinite(float(r[k])), k)
                self.assertLessEqual(r["elapsed_ms"], hz + 0.001)
                self.assertEqual(r["n_samples"], max(1, sum(1 for s in tr.samples if s.t <= tr.t_down + hz * 1e6)))

    def test_feature_files(self):
        out = os.path.join(self.root, "features")
        paths = features.write([self.joined], out, horizons=(50,))
        with open(paths[0], newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertTrue(rows)
        self.assertTrue({"label_class", "li_verdict", "since_key_ms", "f_rise_per_s"} <= set(rows[0]))
        self.assertTrue(os.path.getsize(paths[1]) > 0)

    def test_report(self):
        path = report.render([self.joined], os.path.join(self.root, "r.html"))
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Touch Lab", text)
        self.assertIn('"synthetic":true', text)
        self.assertNotIn("</script><", text.split("const D=")[1].split(";\nconst css")[0])

    def test_record_export(self):
        out = os.path.join(self.root, "r.yml")
        record_export.export(Session(self.sdir, self.root), out)
        with open(out, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("ndevices: 1", text)
        self.assertGreater(text.count("- evdev:"), 100)

    def test_truncated_chunk(self):
        bad = os.path.join(self.root, "sessions", "cut")
        shutil.copytree(self.sdir, bad)
        chunk = os.path.join(bad, "stream-000001.jsonl.gz")
        with open(chunk, "rb") as fh:
            data = fh.read()
        with open(chunk, "wb") as fh:
            fh.write(data[: len(data) // 2])
        n = len(Session(bad, self.root).records())
        self.assertGreater(n, 100)
        self.assertLess(n, len(Session(self.sdir, self.root).records()))
        shutil.rmtree(bad)


class Rules(unittest.TestCase):
    def test_rules_match_the_script(self):
        rng = random.Random(5)
        layout = hid.TouchpadLayout(hid.parse_descriptor(synth.load_descriptor()))
        good = total = 0
        for s in scenarios.SCENARIOS:
            for _ in range(3):
                trial = synth.scenario_actors(s.id, 1.0, rng)
                tb = contacts.TrackBuilder(synth.load_descriptor())
                for t_s, raw, _f in synth.render_reports([trial], layout, rng):
                    tb.feed_hid(int(t_s * 1e9), raw)
                tb.finish(10 ** 12)
                labels, _why = scenarios.assign(s.rule, tb.tracks)
                by_cid = {a.ident: a.label for a in trial.actors}
                for tr in tb.tracks:
                    total += 1
                    good += labels.get(tr.key) == by_cid.get(tr.cid)
        self.assertGreater(good / total, 0.85, f"{good}/{total}")


class LibinputLines(unittest.TestCase):
    LINES = [
        " event4   POINTER_MOTION            2  +4.042s\t  2.59/  3.34 ( +8.00/+10.32)",
        " event4   POINTER_SCROLL_FINGER        +9.306s\tvert -4.58/0.0* horiz 0.00/0.0 (finger)",
        " event4   POINTER_BUTTON               +6.931s\tBTN_LEFT (272) pressed, seat count: 1",
        " event4   GESTURE_SWIPE_BEGIN          +14.962s\t4",
        " event4   GESTURE_HOLD_END             +4.034s\t1 cancelled",
        " 117: event4  - palm: touch 0 (TOUCH_UPDATE), palm detected (tool-palm)",
        "  ... event4  - thumb: touch 1, THUMB_STATE_FINGER → THUMB_STATE_SUPPRESSED",
        "  ... event4  - tap: touch 0 (TOUCH_BEGIN), tap state TAP_STATE_IDLE → TAP_EVENT_TOUCH → TAP_STATE_TOUCH",
        " 491: event4  - touch 1 is speed-based thumb",
    ]

    def test_parse(self):
        ds = [libinput_log.parse_line(1000 + i, l) for i, l in enumerate(self.LINES)]
        kinds = [d.kind for d in ds]
        self.assertEqual(kinds, ["motion", "scroll", "button", "gesture", "gesture", "palm", "thumb", "tap",
                                 "thumb-why"])
        self.assertAlmostEqual(ds[0].detail["dx"], 2.59)
        self.assertAlmostEqual(ds[1].detail["v"], -4.58)
        self.assertTrue(ds[2].detail["pressed"])
        self.assertEqual(ds[3].detail["fingers"], 4)
        self.assertTrue(ds[4].detail["cancelled"])
        self.assertEqual((ds[5].touch, ds[5].detail["reason"]), (0, "tool-palm"))
        self.assertEqual((ds[6].touch, ds[6].detail["to"]), (1, "THUMB_STATE_SUPPRESSED"))
        self.assertEqual(ds[7].detail["to"], "TAP_STATE_TOUCH")


if __name__ == "__main__":
    unittest.main()
