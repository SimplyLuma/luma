# SPDX-License-Identifier: Apache-2.0
"""Pin/Mute updates preserve other conversations and back up the first write."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/prairie-core"))

from prairie_apps.messages_preferences import ConversationPreferences  # noqa: E402


class ConversationPreferencesTest(unittest.TestCase):
    def test_first_write_backed_up_and_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation-settings.json"
            original = {"luma|alice": {"muted": True, "future": "keep"},
                        "luma|bob": {"pinned": True}}
            path.write_text(json.dumps(original), encoding="utf-8")
            prefs = ConversationPreferences(path)
            prefs.set_flag("luma|alice", "pinned", True)
            backup = path.with_name(path.name + ".before-pin-mute")
            self.assertEqual(json.loads(backup.read_text()), original)
            current = json.loads(path.read_text())
            self.assertEqual(current["luma|alice"], {"muted": True, "future": "keep", "pinned": True})
            self.assertEqual(current["luma|bob"], original["luma|bob"])
            prefs.set_flag("luma|alice", "muted", False)
            self.assertEqual(json.loads(backup.read_text()), original)
            self.assertEqual(prefs.flags("luma|alice"), {"pinned": True, "muted": False})

    def test_new_store_has_empty_backup_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages" / "conversation-settings.json"
            prefs = ConversationPreferences(path)
            self.assertEqual(prefs.flags("new"), {"pinned": False, "muted": False})
            prefs.set_flag("new", "muted", True)
            self.assertEqual(json.loads(path.with_name(path.name + ".before-pin-mute").read_text()), {})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
