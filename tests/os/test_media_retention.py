#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Controlled publication/retirement boundaries; no download or deletion."""
import copy
import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
import subprocess
import json
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("retention", ROOT / "scripts/os/lib/media_retention.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Retention(unittest.TestCase):
    def setUp(self):
        self.now = m.utc("2026-10-10T12:00:00Z")
        self.entries = [{"build_id": "20261009.4"}, {"build_id": "20261010.1"}, {"build_id": "20261010.3"}]

    def test_first_enrollment_keeps_all_existing_and_same_day(self):
        ledger, eligible = m.policy(self.entries, "20261010.3", "20261010.1", self.now)
        self.assertEqual(eligible, [])
        self.assertTrue(all(m.utc(x) == self.now + dt.timedelta(days=7) for x in ledger["deadlines"].values()))

    def test_seven_day_boundary_and_current_never_expires(self):
        ledger, _ = m.policy(self.entries, "20261010.3", "20261010.1", self.now, confirmations={x["build_id"]: self.now.isoformat() for x in self.entries[:-1]})
        _, early = m.policy(self.entries, "20261010.3", "20261010.3", self.now + dt.timedelta(days=7, microseconds=-1), previous=ledger)
        _, exact = m.policy(self.entries, "20261010.3", "20261010.3", self.now + dt.timedelta(days=7), previous=ledger)
        self.assertEqual(early, [])
        self.assertEqual(set(exact), {"20261009.4", "20261010.1"})

    def test_replacing_long_lived_current_starts_new_seven_days(self):
        ledger, _ = m.policy(self.entries[:2], "20261010.1", "20261009.4", self.now)
        later = self.now + dt.timedelta(days=30)
        changed, eligible = m.policy(self.entries, "20261010.3", "20261010.1", later, previous=ledger)
        self.assertNotIn("20261010.1", eligible)
        self.assertEqual(m.utc(changed["deadlines"]["20261010.1"]), later + dt.timedelta(days=7))

    def test_repeated_publish_and_clock_rollback_do_not_shorten(self):
        ledger, _ = m.policy(self.entries, "20261010.3", "20261010.1", self.now)
        changed, _ = m.policy(self.entries, "20261010.3", "20261010.3", self.now - dt.timedelta(days=1), previous=ledger)
        self.assertEqual(changed["deadlines"], ledger["deadlines"])

    def test_longer_existing_deadline_and_out_of_band_selection_preserved(self):
        ledger, _ = m.policy(self.entries, "20261010.3", "20261010.1", self.now)
        ledger["deadlines"]["20261010.1"] = (self.now + dt.timedelta(days=20)).isoformat()
        changed, eligible = m.policy(self.entries, "20261010.3", "20261010.1", self.now + dt.timedelta(days=8), previous=ledger)
        self.assertNotIn("20261010.1", eligible)
        self.assertEqual(changed["deadlines"]["20261010.1"], ledger["deadlines"]["20261010.1"])

    def test_bad_schema_time_identity_or_short_window_refused(self):
        for options in ({"days": 6}, {"previous": {"schema": "wrong"}},
                        {"previous": {"schema": m.SCHEMA, "deadlines": {"20261010.1": "2026-10-10T12:00:00"}}}):
            with self.assertRaises(ValueError):
                m.policy(self.entries, "20261010.3", "20261010.1", self.now, **options)
        with self.assertRaises(ValueError):
            m.policy(self.entries + [self.entries[0]], "20261010.3", "20261010.1", self.now)

    def test_no_cleanup_before_actual_website_confirmation(self):
        ledger, _ = m.policy(self.entries, "20261010.3", "20261010.1", self.now)
        later = self.now + dt.timedelta(days=30)
        _, eligible = m.policy(self.entries, "20261010.3", "20261010.3", later, previous=ledger)
        self.assertEqual(eligible, [])
        confirmed, eligible = m.policy(self.entries, "20261010.3", "20261010.3", later, previous=ledger,
                                      confirmations={"20261010.1": later.isoformat()})
        self.assertNotIn("20261010.1", eligible)
        self.assertEqual(m.utc(confirmed["deadlines"]["20261010.1"]), later + dt.timedelta(days=7))
        with self.assertRaises(ValueError):
            m.policy(self.entries, "20261010.3", "20261010.3", later, previous=confirmed,
                     confirmations={"20261010.1": (later + dt.timedelta(seconds=1)).isoformat()})

    def test_actual_cli_preserves_public_entries_and_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary) / "tree"
            tree.mkdir()
            for entry in self.entries:
                name = "luma-nightly-" + entry["build_id"] + ".iso"
                (tree / (name + ".json")).write_text(json.dumps(entry))
                (tree / name).write_bytes(b"controlled installer fixture")
            (tree / "latest.json").write_text(json.dumps({"latest": self.entries[1]}))
            before = {p.name: p.read_bytes() for p in tree.iterdir()}
            result = subprocess.run(["python3", str(ROOT / "scripts/os/lib/media_retention.py"),
                                     "--tree", str(tree), "--ledger", temporary + "/ledger.json",
                                     "--current", "20261010.3"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "")
            self.assertEqual(before, {p.name: p.read_bytes() for p in tree.iterdir()})


if __name__ == "__main__":
    unittest.main()
