#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Data boundaries for the v70 Messages fixture and approved settings store."""

import json
from pathlib import Path
import stat
import tempfile
import unittest

from prairie_apps.messages_fixture import FixtureMessageStore, preview
from prairie_apps.messages_preferences import ConversationPreferences


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests/fixtures/messages-v70.json"


class PreferencesTest(unittest.TestCase):
    def test_first_write_backs_up_and_changes_only_one_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation-settings.json"
            original = {"account|one": {"pinned": False, "other": "keep"},
                        "account|two": {"muted": True, "other": 3}}
            path.write_text(json.dumps(original), encoding="utf-8")
            preferences = ConversationPreferences(path)
            preferences.set_flag("account|one", "pinned", True)
            backup = path.with_name(path.name + ".before-pin-mute")
            self.assertEqual(json.loads(backup.read_text()), original)
            self.assertEqual(json.loads(path.read_text()),
                             {"account|one": {"pinned": True, "other": "keep"},
                              "account|two": original["account|two"]})
            preferences.set_flag("account|one", "muted", True)
            self.assertEqual(json.loads(backup.read_text()), original)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_new_store_backs_up_empty_state_and_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            preferences = ConversationPreferences(path)
            with self.assertRaises(ValueError):
                preferences.set_flag("one", "archived", True)
            self.assertFalse(path.exists())
            preferences.set_flag("one", "muted", True)
            self.assertEqual(json.loads(path.with_name(path.name + ".before-pin-mute").read_text()), {})
            self.assertEqual(preferences.flags("one"), {"pinned": False, "muted": True})

    def test_message_pin_is_scoped_and_has_its_own_first_write_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = {"account|one": {"muted": True, "other": "keep"},
                        "account|two": {"pinned": True}}
            path.write_text(json.dumps(original), encoding="utf-8")
            preferences = ConversationPreferences(path)
            preferences.set_message_pinned("account|one", "message-7", True)
            backup = path.with_name(path.name + ".before-message-pin")
            self.assertEqual(json.loads(backup.read_text()), original)
            self.assertEqual(preferences.pinned_messages("account|one"), {"message-7"})
            self.assertEqual(preferences.pinned_messages("account|two"), set())
            preferences.set_message_pinned("account|one", "message-7", False)
            self.assertEqual(json.loads(path.read_text()), original)
            self.assertEqual(json.loads(backup.read_text()), original)
            with self.assertRaises(ValueError):
                preferences.set_message_pinned("account|one", "", True)


class FixtureTest(unittest.TestCase):
    def test_memory_store_and_asset_boundary(self):
        store = FixtureMessageStore(FIXTURE)
        try:
            self.assertEqual(len(store.threads()), 7)
            self.assertFalse((FIXTURE.parent / "conversation-settings.json").exists())
            self.assertEqual(store.asset("messages-v70/stage.svg").parent, FIXTURE.parent / "messages-v70")
            with self.assertRaises(ValueError):
                store.asset("../outside")
        finally:
            store.close()

    def test_group_media_preview_names_the_sender(self):
        people = {"NF": {"name": "Nora Feld"}}
        self.assertEqual(preview({"from": "NF", "kind": "voice"}, people, group=True),
                         "Nora: Voice message")
        self.assertEqual(preview({"from": "me", "kind": "file", "name": "deck.stage"}, people, group=True),
                         "You: deck.stage")


if __name__ == "__main__":
    unittest.main()
