# SPDX-License-Identifier: Apache-2.0
"""Recently updated, going back, and pausing (depot_app_history)."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from luma_installer import depot_app_history as h
from luma_installer import depot_autoupdate as au

NOW = 1_800_000_000
DAY = 86400
A = "a" * 64
B = "b" * 64
C = "c" * 64


def entry(app="org.example.Notes", frm="1.0", to="1.1", at=NOW, fc=A, tc=B, **kw):
    return h.Entry(app, "Notes", frm, to, at, from_commit=fc, to_commit=tc, **kw)


class Recording(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = {"XDG_STATE_HOME": self.dir.name}

    def test_updates_are_kept_for_ninety_days_newest_first(self):
        h.record(entry(at=NOW - 91 * DAY), self.env, now=NOW)
        h.record(entry(to="1.2", at=NOW - 2 * DAY), self.env, now=NOW)
        h.record(entry(app="org.example.Maps", at=NOW - DAY, automatic=True), self.env, now=NOW)
        recent = h.recent(h.load(self.env, now=NOW))
        self.assertEqual([(e.app_id, e.to_version) for e in recent],
                         [("org.example.Maps", "1.1"), ("org.example.Notes", "1.2")])
        self.assertTrue(recent[0].automatic)

    def test_the_file_is_private_and_junk_is_ignored(self):
        h.record(entry(), self.env, now=NOW)
        path = h.state_path(self.env)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.write_text(json.dumps({"entries": [{"app_id": "x"}, "junk", {"app_id": "y", "at": True}],
                                    "paused": "nope"}))
        self.assertEqual(h.load(self.env, now=NOW).entries, [])
        path.write_text("{not json")
        self.assertEqual(h.load(self.env, now=NOW).paused, {})

    def test_bad_commits_are_not_kept(self):
        h.record(entry(fc="../../etc", tc="B" * 64), self.env, now=NOW)
        kept = h.load(self.env, now=NOW).entries[0]
        self.assertEqual((kept.from_commit, kept.to_commit), ("", ""))


class GoingBack(unittest.TestCase):
    def test_only_the_latest_update_within_thirty_days_on_the_build_it_installed(self):
        history = h.History([entry(at=NOW - 5 * DAY)])
        self.assertIsNotNone(h.revertable(history, "org.example.Notes", B, now=NOW))
        self.assertIsNone(h.revertable(history, "org.example.Notes", C, now=NOW), "changed since")
        self.assertIsNone(h.revertable(history, "org.example.Notes", B, now=NOW + 26 * DAY), "too old")
        history.entries.append(entry(frm="1.1", to="1.2", fc=B, tc=C, at=NOW - DAY))
        self.assertEqual(h.revertable(history, "org.example.Notes", C, now=NOW).to_version, "1.2")
        self.assertIsNone(h.revertable(h.History([entry(fc="")]), "org.example.Notes", B, now=NOW),
                          "the previous build must be known")
        self.assertIsNone(h.revertable(h.History([entry(kind="revert")]), "org.example.Notes", B, now=NOW))


class Pausing(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = {"XDG_STATE_HOME": self.dir.name}

    def test_a_revert_pauses_until_something_newer_than_what_was_left(self):
        history = h.load(self.env, now=NOW)
        h.pause(history, entry(frm="1.9", to="1.10", fc=A, tc=B), self.env, now=NOW)
        history = h.load(self.env, now=NOW)
        self.assertTrue(h.holds(history, "org.example.Notes", "1.10", B))
        self.assertTrue(h.holds(history, "org.example.Notes", "1.9.5"))
        self.assertTrue(h.holds(history, "org.example.Notes", "", C), "unknown versions stay paused")
        self.assertFalse(h.holds(history, "org.example.Notes", "1.11", C), "1.11 is newer than 1.10")
        self.assertFalse(h.holds(history, "org.example.Other", "1.0"))
        lifted = h.lift_outdated_pauses(history, {"org.example.Notes": ("1.11", C)}, self.env)
        self.assertEqual(lifted, ["org.example.Notes"])
        self.assertEqual(h.load(self.env, now=NOW).paused, {})

    def test_resume(self):
        history = h.load(self.env, now=NOW)
        h.pause(history, entry(), self.env, now=NOW)
        self.assertEqual(h.resume("org.example.Notes", self.env).paused, {})

    def test_automatic_updates_skip_a_paused_app_and_a_low_battery(self):
        history = h.History()
        history.paused["org.example.Notes"] = h.Pause("org.example.Notes", "Notes", "1.1", B, NOW)
        notes = au.Pending("org.example.Notes", "Notes", "1.1", widens=False, commit=B)
        maps = au.Pending("org.example.Maps", "Maps", "2.0", widens=False)
        plan = au.plan([notes, maps], {"approved": [], "notified": []}, enabled=True, history=history)
        self.assertEqual(plan.install, ("org.example.Maps",))
        plan = au.plan([maps], {"approved": [], "notified": []}, enabled=True, low_battery=True)
        self.assertEqual((plan.install, plan.skipped_reason), ((), "The battery is low."))


class Versions(unittest.TestCase):
    def test_newer(self):
        self.assertTrue(h.newer("1.10", "1.9"))
        self.assertTrue(h.newer("2.0.1", "2.0"))
        self.assertFalse(h.newer("1.0", "1.0"))
        self.assertFalse(h.newer("", "1.0"))


if __name__ == "__main__":
    unittest.main()
