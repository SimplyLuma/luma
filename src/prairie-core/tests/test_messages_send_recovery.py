import sqlite3
import tempfile
import unittest
import threading
from pathlib import Path
from types import SimpleNamespace
from prairie_apps.messages_backend import MessageStore
from prairie_apps.messages_reply import ReplySender, ReplyError


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "messages.db"
        self.calls = 0
        self.fail = True
        outer = self
        class Transport:
            def inspect(self): return SimpleNamespace(available=True)
            def send(self, address, text):
                outer.calls += 1
                if outer.calls > 1:
                    with sqlite3.connect(outer.path) as db:
                        assert db.execute("SELECT 1 FROM notification_reply_attempts WHERE outcome='pending'").fetchone()
                if outer.fail: raise RuntimeError("synthetic transport failure")
        self.sender = ReplySender(self.path, Transport)
        self.token = "a" * 32
        self.original = "1" * 32
        self.attempt = "2" * 32
        self.args = ("+12025550123", self.token, self.original, "test")

    def tearDown(self): self.tmp.cleanup()

    def retry(self, **kwargs):
        return self.sender.send(self.args[0], self.token, self.attempt, "test",
                                retry_of=self.original, **kwargs)

    def test_retry_preserves_uid_and_sent_dedup(self):
        uid, state = self.sender.send(*self.args)
        self.assertEqual(state, "unknown")
        with_store = MessageStore(self.path)
        self.assertEqual(with_store.message(uid).send_status, "uncertain")
        with_store.close()
        self.fail = False
        self.assertEqual(self.retry(), (uid, "sent"))
        self.assertEqual(self.retry(), (uid, "sent"))
        self.attempt = "3" * 32
        self.assertEqual(self.retry(), (uid, "sent"))
        self.assertEqual(self.calls, 2)
        store = MessageStore(self.path)
        self.assertEqual(store.message(uid).send_status, "")
        self.assertEqual(store._connection.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
        store.close()

    def test_failed_attempt_never_replays(self):
        self.sender.send(*self.args)
        self.retry(); self.retry()
        self.assertEqual(self.calls, 2)
        with self.assertRaises(ReplyError):
            self.sender.send(self.args[0], self.token, self.attempt, "test")
        self.assertEqual(self.calls, 2)

    def test_conflict_and_pairing(self):
        self.sender.send(*self.args)
        for address, token, text in [(self.args[0],self.token,"changed"),
                                     ("+12025550124",self.token,"test"),
                                     (self.args[0],"b"*32,"test")]:
            with self.assertRaises(ReplyError):
                self.sender.send(address,token,self.attempt,text,retry_of=self.original)
        self.assertEqual(self.calls, 1)

    def test_unauthorized(self):
        self.sender.send(*self.args)
        with self.assertRaises(ReplyError): self.retry(authorized=lambda:False)
        self.assertEqual(self.calls, 1)

    def test_pending_attempt_blocks_new_attempt(self):
        uid, _ = self.sender.send(*self.args)
        self.retry()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE notification_reply_attempts SET outcome='pending'")
        self.attempt = "3" * 32
        with self.assertRaises(ReplyError): self.retry()
        self.assertEqual(self.calls, 2)

    def test_backfill_only_attempted_inline(self):
        uid, _ = self.sender.send(*self.args)
        store = MessageStore(self.path)
        untouched = store.add(self.args[0], "draft", direction="outgoing", state="queued")
        store._connection.execute("DELETE FROM message_send_status")
        store._connection.commit(); store.close()
        store = MessageStore(self.path)
        self.assertEqual(store.message(uid).send_status, "uncertain")
        self.assertEqual(store.message(untouched.uid).send_status, "")
        store.close()

    def test_active_original_cannot_be_retried_after_reopen(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        outer = self
        class HeldTransport:
            def inspect(self): return SimpleNamespace(available=True)
            def send(self, address, text):
                outer.calls += 1
                entered.set()
                assert release.wait(5)
        sender = ReplySender(self.path, HeldTransport)
        worker = threading.Thread(target=lambda: results.append(sender.send(*self.args)))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            store = MessageStore(self.path)
            uid = store._connection.execute("SELECT uid FROM messages").fetchone()[0]
            self.assertEqual(store.message(uid).send_status, "")
            # Even an externally marked uncertain status cannot bypass pending.
            store.mark_send_uncertain(uid)
            store.close()
            with self.assertRaises(ReplyError): self.retry()
            self.assertEqual(self.calls, 1)
        finally:
            release.set(); worker.join(5)
        self.assertEqual(results[0][1], "sent")

if __name__ == "__main__": unittest.main()
