# SPDX-License-Identifier: Apache-2.0
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from charlie_luma.fixtures import DEMO_ACCOUNT, demo_messages
from charlie_luma.model import Account, Message, ServerConfig, normalized_subject, stable_thread_id
from charlie_luma.store import MailStore


class ModelTests(unittest.TestCase):
    def test_subject_prefixes_are_normalized(self):
        self.assertEqual(normalized_subject(" Re: FWD:  Project update "), "Project update")
        self.assertEqual(normalized_subject(""), "(No subject)")

    def test_thread_ancestry_wins_over_reply_subject(self):
        root = "<root@example.test>"
        self.assertEqual(
            stable_thread_id("<one@example.test>", "A", (root,)),
            stable_thread_id("<two@example.test>", "Re: A", (root,)),
        )


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = MailStore(Path(self.temporary.name) / "mail.db")
        self.store.upsert_account(DEMO_ACCOUNT)
        for message in demo_messages():
            self.store.upsert_message(message)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_conversations_are_grouped_and_sorted(self):
        conversations = self.store.conversations()
        self.assertGreaterEqual(len(conversations), 7)
        self.assertEqual(conversations[0].subject, "Re: Q3 report — final draft before Friday")
        self.assertEqual(len(conversations[0].messages), 4)
        self.assertEqual(conversations[0].correspondent.sender_label, "Carla Mendes")

    def test_full_text_search_reaches_message_bodies(self):
        result = self.store.conversations(query="roadmap")
        self.assertEqual(len(result), 1)
        self.assertIn("Q3 report", result[0].subject)
        identifiers = self.store.search_message_ids(("roadmap",))
        self.assertEqual(len(identifiers), 1)
        self.assertEqual(self.store.messages_by_ids(identifiers)[0].id, identifiers[0])

    def test_orphan_inbox_and_sent_messages_group_by_subject_and_peer(self):
        now = datetime.now(timezone.utc)
        common = dict(
            account_id=DEMO_ACCOUNT.id, uid=900, message_id="<leon-root@example.test>",
            thread_id="root-one", subject="Project check-in", sent_at=now,
            snippet="Hello", body_text="Hello", references=(),
        )
        incoming = Message(
            id="demo-luma:inbox:900", folder="inbox", sender_name="Leon",
            sender_address="leon@example.test", recipients=(DEMO_ACCOUNT.address,),
            unread=False, outgoing=False, **common,
        )
        outgoing = replace(
            incoming, id="demo-luma:sent:901", folder="sent", uid=901,
            message_id="<leon-reply@example.test>", thread_id="root-two",
            subject="Re: Project check-in", sender_name="You",
            sender_address=DEMO_ACCOUNT.address, recipients=("leon@example.test",),
            outgoing=True,
        )
        self.store.upsert_message(incoming)
        self.store.upsert_message(outgoing)
        conversation = next(
            item for item in self.store.conversations("inbox")
            if normalized_subject(item.subject) == "Project check-in"
        )
        self.assertEqual(len(conversation.messages), 2)
        self.assertEqual({item.folder for item in conversation.messages}, {"inbox", "sent"})

    def test_read_state_is_transactional(self):
        conversation = self.store.conversations()[0]
        unread = tuple(message.id for message in conversation.messages if message.unread)
        self.assertTrue(unread)
        self.store.set_read(unread, True)
        refreshed = next(item for item in self.store.conversations() if item.id == conversation.id)
        self.assertEqual(refreshed.unread_count, 0)

    def test_move_and_undo_change_folder_transactionally(self):
        conversation = self.store.conversations()[0]
        ids = tuple(message.id for message in conversation.messages)
        self.store.move_messages(ids, "archive")
        self.assertNotIn(conversation.id, {item.id for item in self.store.conversations("inbox")})
        self.assertIn(conversation.id, {item.id for item in self.store.conversations("archive")})
        self.store.move_messages(ids, "inbox")
        self.assertIn(conversation.id, {item.id for item in self.store.conversations("inbox")})

    def test_database_is_private(self):
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_attachment_payload_is_kept_in_private_store(self):
        conversation = next(item for item in self.store.conversations() if item.has_attachments)
        attachment = next(message.attachments[0] for message in conversation.messages if message.attachments)
        self.assertTrue(attachment.data)

    def test_formatted_message_survives_private_store(self):
        conversation = next(item for item in self.store.conversations() if item.has_attachments)
        message = next(item for item in conversation.messages if item.body_html)
        self.assertIn("<strong>final report</strong>", message.body_html)

    def test_server_metadata_round_trips_without_credentials(self):
        config = ServerConfig("imap.example.test", 993, "smtp.example.test", 587,
                              "nick@example.test", True)
        self.store.upsert_server_config(DEMO_ACCOUNT.id, config)
        self.assertEqual(self.store.server_config(DEMO_ACCOUNT.id), config)
        self.assertNotIn("password", {row[1] for row in self.store._connection.execute(
            "PRAGMA table_info(server_configs)"
        )})

    def test_account_profile_photo_round_trips_in_schema_four(self):
        account = replace(
            DEMO_ACCOUNT,
            avatar_url="https://lh3.googleusercontent.com/a/profile-photo",
        )
        self.store.upsert_account(account)
        restored = next(value for value in self.store.accounts() if value.id == account.id)
        self.assertEqual(restored.avatar_url, account.avatar_url)
        self.assertEqual(
            self.store._connection.execute("PRAGMA user_version").fetchone()[0], 4
        )

    def test_schema_three_account_is_migrated_without_losing_identity(self):
        legacy_path = Path(self.temporary.name) / "legacy.db"
        legacy = sqlite3.connect(legacy_path)
        legacy.executescript(
            """
            CREATE TABLE accounts (
              id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
              address TEXT NOT NULL, provider TEXT NOT NULL,
              colour TEXT NOT NULL, enabled INTEGER NOT NULL
            );
            INSERT INTO accounts VALUES
              ('legacy', 'Legacy', 'legacy@example.test', 'gmail', 'red', 1);
            PRAGMA user_version=3;
            """
        )
        legacy.close()
        migrated = MailStore(legacy_path)
        try:
            account = migrated.accounts()[0]
            self.assertEqual((account.id, account.display_name), ("legacy", "Legacy"))
            self.assertEqual(account.avatar_url, "")
            self.assertEqual(
                migrated._connection.execute("PRAGMA user_version").fetchone()[0], 4
            )
        finally:
            migrated.close()

    def test_account_filter_and_removal_are_transactional(self):
        second = Account("second", "Second", "second@example.test")
        self.store.upsert_account(second)
        message = replace(
            demo_messages()[0], id="second:inbox:1", account_id="second", uid=1
        )
        self.store.upsert_message(message)
        self.assertEqual(len(self.store.conversations(account_id="second")), 1)
        self.store.delete_account("second")
        self.assertFalse(any(account.id == "second" for account in self.store.accounts()))
        self.assertEqual(self.store.conversations(account_id="second"), ())
