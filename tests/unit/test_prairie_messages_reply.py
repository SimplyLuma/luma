# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/prairie-core"))
from prairie_apps.messages_backend import MessageStore
from prairie_apps.messages_reply import ReplyError, ReplySender


class ReplyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "messages.db"
        self.peer = "+12025550100"
        self.token = "a" * 32
        self.request = "b" * 32
        self.calls = []
        self.ready = True
        self.failure = False
        self.transport = SimpleNamespace(inspect=lambda: SimpleNamespace(available=self.ready), send=self.send)
        self.sender = ReplySender(self.path, lambda: self.transport)

    def send(self, address, body):
        self.calls.append((address, body))
        if self.failure:
            raise RuntimeError("private content must not escape")

    def reply(self, text="hello", **kwargs):
        return self.sender.send(self.peer, self.token, self.request, text, **kwargs)

    def test_sent_is_persisted_and_replay_does_not_send_again(self):
        result = self.reply()
        self.assertEqual(result[1], "sent")
        self.assertEqual(self.reply(), result)
        self.assertEqual(self.calls, [(self.peer, "hello")])
        store = MessageStore(self.path)
        self.addCleanup(store.close)
        self.assertEqual(store.message(result[0]).body, "hello")

    def test_conflicting_body_and_address_rejected(self):
        self.reply()
        for peer, text in [(self.peer, "different"), ("+12025550101", "hello")]:
            with self.assertRaises(ReplyError) as error:
                self.sender.send(peer, self.token, self.request, text)
            self.assertEqual(error.exception.code, "RequestConflict")
        self.assertEqual(len(self.calls), 1)

    def test_preflight_failure_allows_explicit_retry_with_same_request(self):
        self.ready = False
        with self.assertRaises(ReplyError) as error:
            self.reply()
        self.assertEqual(error.exception.code, "NotReady")
        self.assertFalse(self.calls)
        self.ready = True
        self.assertEqual(self.reply()[1], "sent")

    def test_unknown_outcome_survives_restart_without_resend(self):
        self.failure = True
        result = self.reply()
        self.assertEqual(result[1], "unknown")
        self.sender = ReplySender(self.path, lambda: self.transport)
        self.assertEqual(self.reply(), result)
        self.assertEqual(len(self.calls), 1)
        store = MessageStore(self.path)
        self.addCleanup(store.close)
        self.assertEqual(store.message(result[0]).state, "queued")

    def test_deleted_message_cannot_be_replayed(self):
        uid, _state = self.reply()
        store = MessageStore(self.path)
        store.delete_message(uid)
        store.close()
        with self.assertRaises(ReplyError) as error:
            self.reply()
        self.assertEqual(error.exception.code, "NoLongerAvailable")
        self.assertEqual(len(self.calls), 1)

    def test_owner_revoked_during_preflight_does_not_send(self):
        valid = True
        def inspect():
            nonlocal valid
            valid = False
            return SimpleNamespace(available=True)
        self.transport.inspect = inspect
        with self.assertRaises(ReplyError) as error:
            self.reply(authorized=lambda: valid)
        self.assertEqual(error.exception.code, "Unauthorized")
        self.assertFalse(self.calls)

    def test_invalid_and_multibyte_oversize_body_do_not_send(self):
        for text in ("", "  ", "bad\x00text", "界" * 1366):
            with self.assertRaises(ReplyError):
                self.reply(text)
        self.assertFalse(self.calls)


if __name__ == "__main__":
    unittest.main()
