#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the real UI transport handlers with isolated storage and no modem."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from gi.repository import GLib
from prairie_apps import messages as app
from prairie_apps.messages_backend import MessageStore


class ContentRuntime(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = MessageStore(self.root / "private/messages.db")
        self.addCleanup(self.store.close)
        self.peer = "+12025550100"
        self.native = "/org/ofono/mms/test/incoming"
        self.errors = []
        self.service = SimpleNamespace(store=self.store, provider=None, transport=None, mms_transport=None)
        self.window = SimpleNamespace(store=self.store, _mms_imported=lambda error: self.errors.append(error))
        self.pdu = self.root / ".mms/content"
        self.pdu.parent.mkdir()
        self.pdu.write_bytes(b"captionimage payload")
        self.properties = {
            "Status": "received", "Sender": self.peer,
            "Recipients": ["+12025550999"], "Modem Number": "+12025550999",
            "Attachments": [("message.txt", "text/plain", str(self.pdu), 0, 7),
                            ("image.png", "image/png", str(self.pdu), 7, 13)],
        }

    def import_message(self, properties=None):
        with patch.object(app.Path, "home", return_value=self.root):
            app.MessagesWindow._import_mms(self.window, self.service, self.native, properties or self.properties)
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)

    def test_received_parts_are_durable_and_not_duplicated_or_resurrected(self):
        self.import_message()
        message, = self.store.thread(self.peer)
        self.assertEqual(message.body, "caption")
        self.assertEqual(message.attachments[0].name, "image.png")
        self.assertEqual(self.store.attachment_path(message.attachments[0]).read_bytes(), b"image payload")
        self.import_message()
        self.assertEqual(len(self.store.thread(self.peer)), 1)
        self.assertFalse(any(self.errors), self.errors)
        self.store.delete_message(message.uid)
        self.import_message()
        self.assertEqual(self.store.thread(self.peer), ())

    def test_group_mms_stays_in_service_instead_of_becoming_one_to_one(self):
        self.import_message(dict(self.properties, Recipients=["+12025550101", "+12025550999"]))
        self.assertEqual(self.store.thread(self.peer), ())
        self.assertTrue(any("Group MMS" in error for error in self.errors))

    def test_bad_part_rolls_back_already_staged_files(self):
        parts = [self.properties["Attachments"][1], ("bad.png", "image/png", str(self.pdu), 900, 1)]
        self.import_message(dict(self.properties, Attachments=parts))
        self.assertEqual(self.store.thread(self.peer), ())
        self.assertEqual(self.store.draft_attachments(self.peer), ())
        self.assertFalse(list((self.store.path.parent / "attachments").iterdir()))
        self.assertTrue(any(self.errors))

    def test_mms_send_queue_acceptance_is_not_sent_and_caption_is_exact(self):
        part = self.store.attach_file(self.peer, self.pdu, name="image.png")
        parent = self.store.add(self.peer, "previous", direction="incoming")
        message = self.store.add(self.peer, "reply", direction="outgoing",
                                 attachment_uids=(part.uid,), reply_to=parent.uid)
        calls = []

        def send(recipients, parts):
            calls.append((recipients, [(mime, Path(path).read_bytes()) for _name, mime, path in parts]))
            return "/org/ofono/mms/test/outgoing"

        self.service.mms_transport = SimpleNamespace(send=send)
        self.window._finish_transmit = lambda *_args: False
        app.MessagesWindow._transmit(self.window, self.service, message.uid, self.peer, message.wire_body)
        stored = self.store.message(message.uid)
        self.assertEqual(stored.state, "queued")
        self.assertEqual(stored.transport_id, "mmsd:/org/ofono/mms/test/outgoing")
        self.assertEqual(calls[0][0], (self.peer,))
        self.assertEqual(calls[0][1][0], ("text/plain", message.wire_body.encode()))
        self.assertFalse(list(self.store.path.parent.glob(".mms-send-*")))
        self.native = "/org/ofono/mms/test/outgoing"
        self.import_message({"Status": "sent"})
        self.assertEqual(self.store.message(message.uid).state, "sent")


if __name__ == "__main__":
    unittest.main()
