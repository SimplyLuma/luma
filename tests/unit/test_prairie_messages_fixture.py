# SPDX-License-Identifier: Apache-2.0
"""The Messages conform fixture never opens or changes the person's store."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/prairie-core"))

from prairie_apps.messages_fixture import FixtureMessageStore, preview  # noqa: E402


class FixtureMessagesTest(unittest.TestCase):
    def test_preview_names_sender_and_media(self) -> None:
        people = {"PR": {"name": "Priya Raman"}}
        self.assertEqual(preview({"from": "PR", "kind": "photo"}, people, group=True), "Priya: Photo")
        self.assertEqual(preview({"from": "me", "text": "On it"}, people), "You: On it")

    def test_fixture_reads_only_its_json_and_keeps_rows_in_memory(self) -> None:
        path = ROOT / "tests/fixtures/messages-v70.json"
        with tempfile.TemporaryDirectory() as isolated:
            untouched = Path(isolated) / "messages.db"
            with patch.dict("os.environ", {"XDG_DATA_HOME": isolated}):
                store = FixtureMessageStore(path)
                try:
                    self.assertEqual(len(store.conversations), 7)
                    self.assertEqual(len(store.meta), 24)
                    self.assertEqual(store.conversation_kind("+15105550101"), "group")
                    self.assertEqual(store.threads()[0].display_name, "Launch crew")
                    self.assertFalse(untouched.exists())
                finally:
                    store.close()
            self.assertFalse(list(Path(isolated).iterdir()))

    def test_fixture_asset_cannot_escape(self) -> None:
        store = FixtureMessageStore(ROOT / "tests/fixtures/messages-v70.json")
        try:
            with self.assertRaises(ValueError):
                store.asset("../../personal-photo.jpg")
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
