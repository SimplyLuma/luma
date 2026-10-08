"""Messages network accounts (ADR-023) against a fake luma-messages-bridge/1 helper."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prairie_apps.messages_accounts import (  # noqa: E402
    AccountLogin, AccountProvider, Accounts, AccountStore, KeyringLocked, available_networks, capture_browser_cookies,
    remove_account, _read_firefox_cookies)

FAKE = Path(__file__).resolve().parent / "fake_messages_bridge.py"


class FakeSecrets:
    def __init__(self):
        self.items, self.locked = {}, False

    def get(self, account_id):
        if self.locked: raise KeyringLocked("locked")
        return self.items.get(account_id)

    def set(self, account_id, _label, value):
        if self.locked: raise KeyringLocked("locked")
        self.items[account_id] = value

    def delete(self, account_id):
        self.items.pop(account_id, None)


def wait(predicate, what, timeout=10):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError("timed out: " + what)
        time.sleep(0.02)


class AccountsCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.helpers = root / "libexec"
        self.helpers.mkdir()
        helper = self.helpers / "whatsapp"
        helper.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
        helper.chmod(0o755)
        self.accounts = Accounts(root / "accounts")
        self.secrets = FakeSecrets()
        self.steps = []

    def sign_in(self):
        login = AccountLogin("whatsapp", self.accounts, secrets=self.secrets, dispatch=lambda fn: fn(),
                             on_step=lambda name, data: self.steps.append((name, data)), helper_dir=self.helpers)
        login.start("qr")
        wait(lambda: any(name == "login.qr" for name, _ in self.steps), "QR code step")
        login.submit("confirm", "scanned")
        wait(lambda: any(name == "login.done" for name, _ in self.steps), "sign-in done")
        return login.account

    def provider(self, account, **kwargs):
        states = []
        provider = AccountProvider(account, self.accounts, secrets=self.secrets, dispatch=lambda fn: fn(),
                                   helper_dir=self.helpers, **kwargs)
        self.addCleanup(lambda: provider.close(wait=True))
        provider.start(states.append)
        return provider, states


class LoginTest(AccountsCase):
    def test_only_installed_helpers_are_offered(self):
        self.assertEqual([n.id for n in available_networks(self.helpers)], ["whatsapp"])

    def test_qr_sign_in_keeps_the_session_in_the_keyring_only(self):
        account = self.sign_in()
        self.assertFalse(account.pending)
        self.assertEqual((account.name, account.handle), ("Fake Person", "+15550100"))
        self.assertEqual(self.secrets.items[account.id], "fake-session-token")
        directory = self.accounts.directory(account.id)
        self.assertEqual(oct(directory.stat().st_mode & 0o777), "0o700")
        on_disk = b"".join(p.read_bytes() for p in directory.rglob("*") if p.is_file())
        self.assertNotIn(b"fake-session-token", on_disk)
        self.assertEqual([a.id for a in self.accounts.list()], [account.id])

    def test_cancelling_removes_the_pending_account(self):
        login = AccountLogin("whatsapp", self.accounts, secrets=self.secrets, dispatch=lambda fn: fn(),
                             on_step=lambda name, data: self.steps.append((name, data)), helper_dir=self.helpers)
        login.start("qr")
        wait(lambda: self.steps, "first step")
        login.cancel()
        wait(lambda: not self.accounts.list(include_pending=True), "pending account removed")
        self.assertEqual(self.secrets.items, {})

    def test_browser_cookies_then_emoji(self):
        captured = []

        def capture(url, names, domains, *, optional, cancelled):
            captured.append((url, names, domains))
            return {"SID": "cookie-value"}

        login = AccountLogin("whatsapp", self.accounts, secrets=self.secrets, dispatch=lambda fn: fn(),
                             on_step=lambda name, data: self.steps.append((name, data)), helper_dir=self.helpers,
                             capture=capture)
        login.start("cookies")
        wait(lambda: any(name == "login.code" for name, _ in self.steps), "emoji step")
        self.assertEqual(captured, [("https://fake.example/signin", ["SID"], ["fake.example"])])
        self.assertIn(("login.code", {"code": "🦊", "kind": "emoji"}), self.steps)
        self.assertEqual(self.steps[0][0], "browser.waiting")
        login.cancel()
        # Cancelling finishes on the login's worker (the helper is stopped and
        # the pending account removed); let it, before the fixture's folder goes.
        wait(lambda: not self.accounts.list(include_pending=True), "pending account removed")


class ProviderTest(AccountsCase):
    def test_sync_send_receive_and_read(self):
        account = self.sign_in()
        arrivals = []
        provider, states = self.provider(account)
        provider.on_message = lambda address, name, text: arrivals.append((address, name, text))
        wait(lambda: provider.status["state"] == "ready", "connected")
        store = AccountStore(provider.store_path)
        self.addCleanup(store.close)
        wait(lambda: len(store.threads()) == 2, "initial conversations imported")
        names = {t.address: t.display_name for t in store.threads()}
        self.assertEqual(names, {"chat.one": "Fake Friend", "group.one": "Fake Group"})
        wait(lambda: store.thread("group.one"), "group message imported")
        group_message = store.thread("group.one")[0]
        # The sender is written just after its message; Messages redraws when it lands.
        wait(lambda: provider.sender(group_message.uid) == "Other Person", "group sender recorded")
        self.assertEqual(store.thread("chat.one")[0].state, "received")
        self.assertTrue(provider.sms_transport().inspect().available)

        outgoing = store.add("chat.one", "Sent from Luma", direction="outgoing", state="sending")
        result = provider.send_message(outgoing.uid, store.authorize_send(outgoing.uid))
        self.assertEqual(result["state"], "sent")
        wait(lambda: store.message(outgoing.uid).state == "sent" and store.message(outgoing.uid).transport_id, "confirmed")
        sent = [json.loads(line) for line in (self.accounts.directory(account.id) / "sent.jsonl").read_text().splitlines()]
        self.assertEqual([(s["conversation"], s["client_id"], s["text"]) for s in sent], [("chat.one", outgoing.uid, "Sent from Luma")])
        self.assertEqual(len(store.thread("chat.one")), 2, "the confirmation is not imported twice")

        provider._process.request("fake.incoming", {"id": "m9", "text": "Are you there?"})
        wait(lambda: arrivals, "incoming notification")
        self.assertEqual(arrivals, [("chat.one", "Fake Friend", "Are you there?")])
        self.assertEqual(store.thread("chat.one")[-1].body, "Are you there?")

        provider.conversation_opened("chat.one")
        read = self.accounts.directory(account.id) / "read.jsonl"
        wait(read.exists, "read receipt sent")
        self.assertEqual(json.loads(read.read_text()), {"conversation": "chat.one", "message": "m9"})

    def test_reactions_arrive_with_messages_and_later_additions_replace_them(self):
        account = self.sign_in()
        provider, _ = self.provider(account)
        wait(lambda: provider.status["state"] == "ready", "connected")
        store = AccountStore(provider.store_path)
        self.addCleanup(store.close)
        wait(lambda: store.thread("chat.one"), "history imported")
        first = store.thread("chat.one")[0]
        self.assertEqual(store.reactions(first.uid), ())
        # Someone hearts a message already on this computer: opening the
        # conversation brings the reaction in, without a new message.
        provider._process.request("fake.react", {"conversation": "chat.one", "message": "m1",
                                                 "reactions": [{"sender": "friend", "emoji": "❤️"},
                                                               {"sender": "self", "emoji": "😂"}]})
        provider._fetched.clear()
        provider.conversation_opened("chat.one")
        wait(lambda: len(store.reactions(first.uid)) == 2, "reactions imported")
        self.assertEqual({(r.sender, r.emoji, r.state) for r in store.reactions(first.uid)},
                         {("friend", "❤️", "received"), ("self", "😂", "sent")})
        # A reaction removed on the phone goes away here too.
        provider._process.request("fake.react", {"conversation": "chat.one", "message": "m1",
                                                 "reactions": [{"sender": "friend", "emoji": "❤️"}]})
        provider._fetched.clear()
        provider.conversation_opened("chat.one")
        wait(lambda: len(store.reactions(first.uid)) == 1, "removed reaction dropped")

    def test_reacting_adds_changes_and_removes_this_accounts_reaction(self):
        account = self.sign_in()
        provider, _ = self.provider(account)
        wait(lambda: provider.status["state"] == "ready", "connected")
        self.assertEqual(provider.reaction_emoji, ("😍", "😂", "👍"))
        store = AccountStore(provider.store_path)
        self.addCleanup(store.close)
        wait(lambda: store.thread("chat.one"), "history imported")
        first = store.thread("chat.one")[0]
        reacted = self.accounts.directory(account.id) / "reacted.jsonl"
        recorded = lambda: [json.loads(line) for line in reacted.read_text().splitlines()] if reacted.exists() else []
        # Each reaction carries its own person's token (messages_outbound), used once.
        sent = lambda: [{k: v for k, v in item.items() if k != "user_token"} for item in recorded()]
        self.assertEqual(provider.react(first.uid, "😂"), {"state": "sent"})
        self.assertEqual({(r.sender, r.emoji, r.state) for r in store.reactions(first.uid)}, {("self", "😂", "sent")})
        self.assertEqual(provider.react(first.uid, "👍"), {"state": "sent"})
        self.assertEqual(provider.react(first.uid, None), {"state": "sent"})
        self.assertEqual(store.reactions(first.uid), ())
        self.assertEqual(sent(), [
            {"conversation": "chat.one", "message": "m1", "emoji": "😂"},
            {"conversation": "chat.one", "message": "m1", "emoji": "👍", "previous": "😂"},
            {"conversation": "chat.one", "message": "m1", "emoji": None, "previous": "👍"}])
        tokens = [item.get("user_token") for item in recorded()]
        self.assertTrue(all(isinstance(t, str) and len(t) == 32 for t in tokens) and len(set(tokens)) == 3, tokens)
        # An emoji the network doesn't offer is never sent.
        self.assertEqual(provider.react(first.uid, "🦊"), {"state": "failed"})
        # When the phone doesn't answer, the reaction shown stays the one the network has.
        provider.react(first.uid, "😍")
        (self.accounts.directory(account.id) / "fail-react").touch()
        self.assertEqual(provider.react(first.uid, "😂"), {"state": "failed"})
        self.assertEqual({(r.sender, r.emoji, r.state) for r in store.reactions(first.uid)}, {("self", "😍", "sent")})

    def test_a_helper_that_stays_broken_is_restarted(self):
        from prairie_apps import messages_accounts
        original = messages_accounts.RECOVERY_SECONDS
        messages_accounts.RECOVERY_SECONDS = 0.3
        self.addCleanup(setattr, messages_accounts, "RECOVERY_SECONDS", original)
        account = self.sign_in()
        provider, _ = self.provider(account)
        wait(lambda: provider.status["state"] == "ready", "connected")
        first = provider._process
        first.request("fake.status", {"state": "error"})
        wait(lambda: provider._process is not first and provider.status["state"] == "ready", "restarted and reconnected")

    def test_a_connection_that_never_finishes_is_restarted(self):
        from prairie_apps import messages_accounts
        original = messages_accounts.CONNECT_SECONDS
        messages_accounts.CONNECT_SECONDS = 0.3
        self.addCleanup(setattr, messages_accounts, "CONNECT_SECONDS", original)
        account = self.sign_in()
        (self.accounts.directory(account.id) / "fake-stall-connect").touch()
        provider, states = self.provider(account)
        wait(lambda: provider.status["state"] == "ready", "restarted and connected")
        self.assertIn("connecting", [state["state"] for state in states])
        self.assertFalse((self.accounts.directory(account.id) / "fake-stall-connect").exists())

    def test_sent_messages_show_delivered_then_read(self):
        account = self.sign_in()
        provider, _ = self.provider(account)
        wait(lambda: provider.status["state"] == "ready", "connected")
        store = AccountStore(provider.store_path)
        self.addCleanup(store.close)
        wait(lambda: store.thread("chat.one"), "history imported")
        outgoing = store.add("chat.one", "Seen yet?", direction="outgoing", state="sending")
        provider.send_message(outgoing.uid, store.authorize_send(outgoing.uid))
        wait(lambda: store.message(outgoing.uid).transport_id, "confirmed")
        message = store.message(outgoing.uid).transport_id.split(":", 1)[1]
        provider._process.request("fake.receipt", {"conversation": "chat.one", "message": message, "state": "delivered"})
        wait(lambda: store.receipt(outgoing.uid) == "delivered", "delivered")
        provider._process.request("fake.receipt", {"conversation": "chat.one", "message": message, "state": "read"})
        wait(lambda: store.receipt(outgoing.uid) == "read", "read")
        provider._process.request("fake.receipt", {"conversation": "chat.one", "message": message, "state": "delivered"})
        time.sleep(0.3)
        self.assertEqual(store.receipt(outgoing.uid), "read", "read is never taken back")

    def test_a_failed_first_sync_is_retried(self):
        from prairie_apps import messages_accounts
        account = self.sign_in()
        (self.accounts.directory(account.id) / "fail-conversations-once").touch()
        original = messages_accounts.SYNC_RETRY
        messages_accounts.SYNC_RETRY = 0.2
        self.addCleanup(setattr, messages_accounts, "SYNC_RETRY", original)
        provider, _ = self.provider(account)
        store = AccountStore(provider.store_path)
        self.addCleanup(store.close)
        wait(lambda: len(store.threads()) == 2, "conversations imported after the retry")
        self.assertFalse((self.accounts.directory(account.id) / "fail-conversations-once").exists())

    def test_new_conversation_by_number_takes_the_network_id(self):
        account = self.sign_in()
        provider, _ = self.provider(account)
        wait(lambda: provider.status["state"] == "ready", "connected")
        store = AccountStore(provider.store_path)
        self.addCleanup(store.close)
        wait(lambda: len(store.threads()) == 2, "synced")
        draft = store.add("+15550177", "First message", direction="outgoing", state="sending")
        self.assertEqual(provider.send_message(draft.uid, store.authorize_send(draft.uid))["state"], "sent")
        self.assertEqual(store.message(draft.uid).address, "chat.new")
        self.assertEqual(store.thread("+15550177"), ())

    def test_a_locked_keyring_stops_the_account_without_a_file_fallback(self):
        account = self.sign_in()
        self.secrets.locked = True
        provider, _ = self.provider(account)
        wait(lambda: provider.status.get("network_state") == "keyring", "keyring state")
        self.assertFalse(provider.sms_transport().inspect().available)
        self.assertIn("Unlock your keyring", provider.sms_transport().inspect().reason)

    def test_signed_out_elsewhere_asks_to_sign_in_again(self):
        account = self.sign_in()
        self.secrets.items.clear()
        provider, _ = self.provider(account)
        wait(lambda: provider.status.get("network_state") == "needs_login", "needs login")
        self.assertIn("Sign in to WhatsApp again", provider.sms_transport().inspect().reason)

    def test_a_missing_helper_is_reported(self):
        account = self.sign_in()
        (self.helpers / "whatsapp").unlink()
        provider, _ = self.provider(account)
        wait(lambda: provider.status.get("network_state") == "missing", "missing helper")

    def test_removal_logs_out_and_deletes_everything(self):
        account = self.sign_in()
        provider, _ = self.provider(account)
        wait(lambda: provider.status["state"] == "ready", "connected")
        remove_account(account, self.accounts, secrets=self.secrets, provider=provider)
        marker = self.accounts.root / "logged-out.json"
        self.assertEqual(json.loads(marker.read_text()), {"account": account.id, "remote": True})
        self.assertFalse(self.accounts.directory(account.id).exists())
        self.assertNotIn(account.id, self.secrets.items)
        self.assertEqual(self.accounts.list(), [])


class MediaTest(AccountsCase):
    """Pictures reach their message however late they download, and say what is happening meanwhile."""

    def connected(self, account=None):
        account = account or self.sign_in()
        provider, states = self.provider(account)
        wait(lambda: provider.status["state"] == "ready", "connected")
        store = AccountStore(provider.store_path)
        self.addCleanup(store.close)
        wait(lambda: store.thread("chat.one"), "history imported")
        self.settle(provider)
        return account, provider, store

    @staticmethod
    def settle(provider):
        """Waits until the provider's worker has run what is queued now and what that queues in turn.

        "ready" is set while the worker handles the connected event, which then syncs and queues the
        reconnect media pass. A test that sends a picture before that pass has run sees the pass
        fetch it too, so the first no-op runs after the connected event and the second after the pass.
        """
        for _ in range(2):
            provider._submit(lambda: None).result(timeout=10)

    def message(self, store, transport):
        # The message row commits before its media rows, so wait for both.
        wait(lambda: (uid := store.uid_for_transport(transport)) and store.media_parts(uid), f"{transport} stored")
        return store.uid_for_transport(transport)

    def fetches(self, account):
        path = self.accounts.directory(account.id) / "media-fetch.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_a_picture_still_on_its_way_joins_its_message_when_it_arrives(self):
        account, provider, store = self.connected()
        arrivals = []
        provider.on_message = lambda address, name, text: arrivals.append(text)
        provider._process.request("fake.incoming_media", {"id": "p1", "parts": [
            {"part": "0", "state": "pending", "error": "waiting_for_phone"}]})
        uid = self.message(store, "whatsapp:p1")
        self.assertEqual(store.message(uid).body, "")
        self.assertEqual([(p.part, p.state, p.error) for p in provider.media_parts(uid)], [("0", "pending", "waiting_for_phone")])
        wait(lambda: arrivals, "the message was announced")  # after the message and its parts are stored
        self.assertEqual(arrivals, ["Photo"], "a picture-only message is announced as a photo")
        self.assertEqual(next(t.preview for t in store.threads() if t.address == "chat.one"), "Photo")
        # The phone finishes uploading: the same message comes again with the file.
        provider._process.request("fake.incoming_media", {"id": "p1", "parts": [{"part": "0", "content": "\xff\xd8\xff one"}]})
        wait(lambda: store.message(uid).attachments, "the picture joined its message")
        self.assertEqual(len(store.thread("chat.one")), 2, "no second message")
        attachment = store.message(uid).attachments[0]
        self.assertEqual((attachment.content_type, store.attachment_path(attachment).read_bytes()), ("image/jpeg", b"\xff\xd8\xff one"))
        self.assertEqual(store.media_part(uid, "0").state, "done")

    def test_messages_written_on_the_phone_are_never_unread(self):
        account, provider, store = self.connected()
        arrivals = []
        provider.on_message = lambda address, name, text: arrivals.append(text)
        unread = lambda: next(t.unread for t in store.threads() if t.address == "chat.one")
        store.mark_read("chat.one")
        provider._process.request("fake.incoming", {"id": "phone1", "text": "Sent from my phone", "outgoing": True})
        wait(lambda: store.uid_for_transport("whatsapp:phone1"), "the phone's message stored")
        self.settle(provider)
        message = store.message(store.uid_for_transport("whatsapp:phone1"))
        self.assertEqual((message.direction, message.state, unread(), arrivals), ("outgoing", "sent", 0, []))
        # Stored as incoming by an older helper, then reported again as this account's: corrected.
        provider._process.request("fake.incoming", {"id": "phone2", "text": "Also from my phone", "time": 400})
        wait(lambda: unread() == 1, "stored as incoming, unread")
        provider._process.request("fake.incoming", {"id": "phone2", "text": "Also from my phone", "time": 400, "outgoing": True})
        wait(lambda: unread() == 0, "corrected to outgoing")
        self.assertEqual(store.message(store.uid_for_transport("whatsapp:phone2")).direction, "outgoing")

    def test_reading_on_the_phone_clears_unread_here(self):
        account, provider, store = self.connected()
        unread = lambda: next(t.unread for t in store.threads() if t.address == "chat.one")
        store.mark_read("chat.one")
        provider._process.request("fake.incoming", {"id": "r1", "text": "one", "time": 500})
        provider._process.request("fake.incoming", {"id": "r2", "text": "two", "time": 501})
        wait(lambda: unread() == 2, "two unread")
        # The message is reported again as read (INCOMING_DISPLAYED on the phone).
        provider._process.request("fake.incoming", {"id": "r1", "text": "one", "time": 500, "state": "read"})
        wait(lambda: unread() == 1, "read on the phone")
        # The conversation says nothing is unread as of its latest message.
        provider._process.request("fake.conversation", {"unread": 0, "updated": 501})
        wait(lambda: unread() == 0, "conversation read on the phone")
        # A message newer than what the phone had read stays unread.
        provider._process.request("fake.incoming", {"id": "r3", "text": "three", "time": 900})
        wait(lambda: unread() == 1, "new message")
        provider._process.request("fake.conversation", {"unread": 0, "updated": 600})
        self.settle(provider)
        self.assertEqual(unread(), 1)

    def test_a_file_google_no_longer_holds_waits_for_a_tap(self):
        with tempfile.TemporaryDirectory() as temp:
            store = AccountStore(Path(temp) / "messages.db")
            self.addCleanup(store.close)
            record = store.add("chat.one", "old photo", direction="incoming", state="read", transport_id="gmessages:797")
            store.set_media(record.uid, "0", state="failed", mime="image/png", error="gone", now=1000)
            self.assertEqual(store.media_due(now=10**10), [], "not asked again on a schedule")
            self.assertEqual(store.media_due(now=10**10, reconnected=True), [], "nor on reconnecting")
            self.assertEqual(store.media_due(immediate=True), [(record.uid, "chat.one", "797", "0")], "a tap asks again")
            from prairie_apps.messages import _media_status
            text, retry = _media_status(store.media_part(record.uid, "0"))
            self.assertTrue(retry and "no longer has this photo" in text, text)

    def test_the_full_picture_replaces_a_preview_saved_as_the_picture(self):
        with tempfile.TemporaryDirectory() as temp:
            store = AccountStore(Path(temp) / "messages.db")
            self.addCleanup(store.close)
            uid = store.add_media_message("chat.one", "", direction="incoming", state="received", timestamp=10,
                                          transport_id="gmessages:m1")
            tiny, full = Path(temp) / "tiny", Path(temp) / "full"
            tiny.write_bytes(b"\xff\xd8\xff" + b"t" * 900)
            full.write_bytes(b"\xff\xd8\xff" + b"f" * 200_000)
            store.complete_media(uid, "0", tiny, name="IMG_6948.jpeg", mime="image/jpeg")
            first = store.message(uid).attachments[0]
            self.assertEqual(store.suspect_previews(), [(uid, "chat.one", "m1", "0")])
            self.assertEqual(store.suspect_previews(), [], "asked about once")
            replaced = store.complete_media(uid, "0", full, name="IMG_6948.jpeg", mime="image/jpeg")
            attachments = store.message(uid).attachments
            self.assertEqual(len(attachments), 1)
            self.assertEqual((attachments[0].uid, attachments[0].size), (first.uid, full.stat().st_size))
            self.assertEqual(replaced.uid, first.uid)
            self.assertFalse(store.attachment_path(first).exists(), "the preview's blob is removed")
            self.assertEqual(store.attachment_path(attachments[0]).read_bytes(), full.read_bytes())
            # A smaller file never replaces the picture.
            self.assertIsNone(store.complete_media(uid, "0", tiny, name="IMG_6948.jpeg", mime="image/jpeg"))

    def test_a_caption_stored_first_still_gets_its_picture(self):
        # The old failure: a message with text was stored while its picture was on
        # its way, and the picture reported later was ignored for good.
        account, provider, store = self.connected()
        provider._process.request("fake.incoming_media", {"id": "p2", "text": "look at this", "parts": [
            {"part": "0", "state": "downloading"}]})
        uid = self.message(store, "whatsapp:p2")
        provider._process.request("fake.media", {"message": "p2", "part": "0", "content": "\x89PNG\r\n\x1a\n",
                                                 "mime": "image/png", "name": "p.png"})
        wait(lambda: store.message(uid).attachments, "picture attached")
        self.assertEqual(store.message(uid).body, "look at this")

    def test_an_eof_failure_waits_its_turn_then_reconnecting_and_opening_retry_it(self):
        account, provider, store = self.connected()
        provider._process.request("fake.incoming_media", {"id": "p3", "parts": [{"part": "0", "state": "downloading"}]})
        uid = self.message(store, "whatsapp:p3")
        provider._process.request("fake.media", {"message": "p3", "part": "0", "state": "failed", "error": "empty",
                                                 "retryable": True, "attempt": 6})
        wait(lambda: store.media_part(uid, "0").state == "failed", "failed recorded")
        part = store.media_part(uid, "0")
        self.assertEqual((part.error, part.attempts), ("empty", 1))
        self.assertGreater(part.next_attempt, time.time() + 30, "retries wait, with backoff")
        self.assertEqual(store.media_due(), [], "not due yet")
        # An empty file is the phone's to fix, so reconnecting doesn't retry it; opening the conversation does.
        provider._process.request("fake.status", {"state": "connected"})
        self.settle(provider)  # the status event was queued before the answer; then its media pass
        self.assertEqual([f for f in self.fetches(account) if f["message"] == "p3"], [])
        provider._media_fetched.clear()
        provider.conversation_opened("chat.one")
        wait(lambda: store.message(uid).attachments, "fetched when the conversation opened")
        self.assertEqual(self.fetches(account)[-1], {"conversation": "chat.one", "message": "p3", "part": "0"})

    def test_a_network_failure_is_retried_as_soon_as_the_account_reconnects(self):
        account, provider, store = self.connected()
        provider._process.request("fake.incoming_media", {"id": "p4", "parts": [{"part": "0", "state": "downloading"}]})
        uid = self.message(store, "whatsapp:p4")
        provider._process.request("fake.media", {"message": "p4", "part": "0", "state": "failed", "error": "network"})
        wait(lambda: store.media_part(uid, "0").state == "failed", "failed recorded")
        provider._process.request("fake.status", {"state": "connected"})
        wait(lambda: store.message(uid).attachments, "fetched after reconnecting")

    def test_tap_to_retry_downloads_again_and_asks_the_phone_nothing(self):
        account, provider, store = self.connected()
        provider._process.request("fake.incoming_media", {"id": "p5", "parts": [
            {"part": "0", "state": "failed", "error": "expired", "preview": "\xff\xd8\xff thumb"}]})
        uid = self.message(store, "whatsapp:p5")
        wait(lambda: store.media_part(uid, "0") and store.media_part(uid, "0").preview, "thumbnail kept")
        self.assertEqual(store.media_part(uid, "0").preview.read_bytes(), b"\xff\xd8\xff thumb")
        provider.retry_media(uid, "0")
        wait(lambda: store.message(uid).attachments, "retried")
        self.assertIn({"conversation": "chat.one", "message": "p5", "part": "0", "reread": True}, self.fetches(account))
        # "force" meant "ask the phone to send it again" in helpers up to 0.8.
        # A tap asks for a re-read and nothing else; force is never sent.
        self.assertFalse(any("force" in item for item in self.fetches(account)))

    # ── Asking the phone for a received picture's file (Nick, 2026-09-22) ────
    #
    # Approved: "received photos only". Only a person's Try Again on a part of
    # an incoming message that is waiting for its file carries ask_phone.

    def waiting_picture(self, store, provider, message_id, *, outgoing=False, error="waiting_for_phone"):
        provider._process.request("fake.incoming_media", {"id": message_id, "outgoing": outgoing, "parts": [
            {"part": "0", "state": "pending", "error": error, "mime": "image/heic", "name": "IMG_9958.heic",
             "size": 737288, "preview": "\xff\xd8\xff thumb"}]})
        return self.message(store, f"whatsapp:{message_id}")

    def asked_phone(self, account, message_id):
        return [item for item in self.fetches(account) if item.get("message") == message_id and item.get("ask_phone")]

    def test_incoming_try_again_asks_the_phone_once(self):
        account, provider, store = self.connected()
        uid = self.waiting_picture(store, provider, "f1")
        provider.retry_media(uid, "0")
        provider._submit(lambda: None).result(timeout=10)
        self.assertEqual(self.asked_phone(account, "f1"),
                         [{"conversation": "chat.one", "message": "f1", "part": "0", "reread": True, "ask_phone": True}],
                         "one press is one request for the full file")

    def test_outgoing_try_again_never_asks_the_phone(self):
        account, provider, store = self.connected()
        uid = self.waiting_picture(store, provider, "f2", outgoing=True)
        self.assertEqual(store.message(uid).direction, "outgoing")
        provider.retry_media(uid, "0")
        provider._submit(lambda: None).result(timeout=10)
        self.assertTrue([item for item in self.fetches(account) if item.get("message") == "f2"], "the tap still re-reads")
        self.assertEqual(self.asked_phone(account, "f2"), [], "never for a picture this account sent")

    def test_only_a_tap_ever_asks_the_phone(self):
        account, provider, store = self.connected()
        self.waiting_picture(store, provider, "f3")
        provider._process.request("fake.status", {"state": "connected"})  # a reconnect
        provider.conversation_opened("chat.one")
        provider._submit(provider._resume_media, None, True).result(timeout=10)  # every due pass
        provider._submit(provider._resume_media, None, False, True).result(timeout=10)
        self.settle(provider)
        self.assertTrue([item for item in self.fetches(account) if item.get("message") == "f3"], "it was asked for again")
        self.assertEqual(self.asked_phone(account, "f3"), [], "no automatic path asks the phone")

    def test_a_tap_on_a_failure_that_is_not_waiting_does_not_ask_the_phone(self):
        account, provider, store = self.connected()
        uid = self.waiting_picture(store, provider, "f4", error="network")
        provider.retry_media(uid, "0")
        provider._submit(lambda: None).result(timeout=10)
        self.assertEqual(self.asked_phone(account, "f4"), [])

    # ── An incoming picture that waits for its file (2026-09-17) ──────────────
    #
    # Five pictures sat behind "Downloading photo…", then behind a line saying
    # the phone was sending them, and never finished. Their parts were pending
    # with error waiting_for_phone, attempts 0 and no next attempt: the helper
    # parked them with no re-read, and next_media_retry() only counted failed
    # parts, so nothing here armed a pass either. Both halves now keep going,
    # and no path on the way touches sending.

    def sends(self, account):
        """Everything the fake network was asked to deliver. Must stay empty."""
        path = self.accounts.directory(account.id) / "sent.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_a_picture_waiting_for_its_file_is_asked_for_again(self):
        account, provider, store = self.connected()
        provider._process.request("fake.incoming_media", {"id": "w1", "parts": [
            {"part": "0", "state": "pending", "error": "waiting_for_phone", "size": 1245192,
             "mime": "image/heic", "name": "IMG_4821.heic", "preview": "\xff\xd8\xff thumb"}]})
        uid = self.message(store, "whatsapp:w1")
        part = store.media_part(uid, "0")
        self.assertEqual((part.state, part.error, part.retryable), ("pending", "waiting_for_phone", True))
        # A part still on its way arms the next pass. It used to arm nothing,
        # which is what left these five pictures waiting for ever.
        self.assertIsNotNone(store.next_media_retry(), "a waiting picture keeps a retry pass armed")
        before = len(self.fetches(account))
        provider._process.request("fake.status", {"state": "connected"})
        wait(lambda: len(self.fetches(account)) > before, "asked for again")
        # The file turns up on a later read of the conversation.
        provider._process.request("fake.incoming_media", {"id": "w1", "parts": [
            {"part": "0", "content": "\xff\xd8\xff the photo", "mime": "image/heic", "name": "IMG_4821.heic"}]})
        wait(lambda: store.media_part(uid, "0").state == "done", "the picture finished")
        self.assertEqual(store.message(uid).attachments[0].content_type, "image/heic")
        self.assertEqual(self.sends(account), [], "nothing was ever delivered")

    def test_a_waiting_picture_is_asked_for_on_a_backoff_not_every_pass(self):
        from prairie_apps.messages_accounts import MEDIA_PENDING_REASK
        with tempfile.TemporaryDirectory() as temp:
            store = AccountStore(Path(temp) / "messages.db")
            self.addCleanup(store.close)
            record = store.add("chat.one", "a photo", direction="incoming", state="read", transport_id="gmessages:w9")
            store.set_media(record.uid, "0", state="pending", mime="image/heic", error="waiting_for_phone", now=1000)
            due = (record.uid, "chat.one", "w9", "0")
            # The helper has just reported on it; it is re-reading on its own
            # backoff, so asking again now would only repeat the same read.
            self.assertEqual(store.media_due(now=1000), [], "not asked again straight away")
            self.assertEqual(store.media_due(now=1000 + MEDIA_PENDING_REASK - 1), [], "nor before the backoff is up")
            self.assertEqual(store.media_due(now=1000 + MEDIA_PENDING_REASK), [due], "asked again once it has gone quiet")
            # Reconnecting is the moment to ask, whatever the backoff says.
            self.assertEqual(store.media_due(now=1000, reconnected=True), [due], "asked at once on reconnecting")
            # And a pass is armed for it at all, which is what was missing.
            self.assertEqual(store.next_media_retry(), 1000 + MEDIA_PENDING_REASK)

    def test_a_picture_simply_downloading_offers_no_retry_button(self):
        """The row the Try Again condition is likeliest to decorate by mistake.

        A part that has just arrived and hit nothing yet must show a spinner
        and nothing else, or every ordinary download grows a button mid-flight.
        The scroll-anchor work's placeholders are exactly this shape, so this
        is asserted here rather than left as incidental coverage there.
        """
        from prairie_apps.messages import _media_status
        with tempfile.TemporaryDirectory() as temp:
            store = AccountStore(Path(temp) / "messages.db")
            self.addCleanup(store.close)
            record = store.add("chat.one", "a photo", direction="incoming", state="read",
                               transport_id="gmessages:w13")
            for state in ("downloading", "pending"):
                with self.subTest(state=state):
                    store.set_media(record.uid, "0", state=state, mime="image/jpeg", error="")
                    part = store.media_part(record.uid, "0")
                    self.assertEqual((part.state, part.error), (state, ""))
                    _text, can_retry = _media_status(part)
                    # _media_widget shows the button on `retry and (failed or error)`,
                    # where retry needs a state other than downloading.
                    retry = can_retry and part.state != "downloading"
                    self.assertFalse(retry and (part.state == "failed" or bool(part.error)),
                                     f"a {state} picture with nothing wrong must show no Try Again")

    def test_a_picture_the_phone_never_published_keeps_being_re_read_for_a_day(self):
        from prairie_apps.messages_accounts import (MEDIA_WAITING_REASK, MEDIA_WAITING_WINDOW,
                                                    media_retry_delay)
        with tempfile.TemporaryDirectory() as temp:
            store = AccountStore(Path(temp) / "messages.db")
            self.addCleanup(store.close)
            arrived = 1_000_000
            record = store.add("chat.one", "a photo", direction="incoming", state="read",
                               transport_id="gmessages:w10", timestamp=arrived)
            due = (record.uid, "chat.one", "w10", "0")
            # The helper has spent its own re-reads and given up for now.
            store.set_media(record.uid, "0", state="downloading", mime="image/heic", now=arrived)
            store.set_media(record.uid, "0", state="failed", mime="image/heic", error="no_full_size",
                            retryable=True, now=arrived)
            part = store.media_part(record.uid, "0")
            # A steady half hour, not the widening backoff a real failure gets:
            # the attempt is one cheap read and the phone may simply wake up.
            self.assertEqual(media_retry_delay(part.attempts, "no_full_size"), MEDIA_WAITING_REASK)
            self.assertEqual(part.next_attempt, arrived + MEDIA_WAITING_REASK)
            self.assertEqual(store.media_due(now=arrived + MEDIA_WAITING_REASK - 1), [], "not before its half hour")
            self.assertEqual(store.media_due(now=arrived + MEDIA_WAITING_REASK), [due], "re-read quietly")
            # Reconnecting looks again at once, whatever the half hour says.
            self.assertEqual(store.media_due(now=arrived, reconnected=True), [due], "and at once on reconnecting")
            # It stops for good after a day rather than re-reading forever.
            late = arrived + MEDIA_WAITING_WINDOW + 1
            self.assertEqual(store.media_due(now=late), [], "gives up after a day")
            self.assertEqual(store.media_due(now=late, reconnected=True), [], "even on reconnecting")
            # Opening the conversation is still a person asking, so it asks.
            self.assertEqual(store.media_due(now=late, immediate=True), [due], "a tap always asks")

    def test_a_genuinely_gone_or_oversized_picture_is_never_re_read(self):
        with tempfile.TemporaryDirectory() as temp:
            store = AccountStore(Path(temp) / "messages.db")
            self.addCleanup(store.close)
            for name, error in (("w11", "gone"), ("w12", "too_large")):
                record = store.add("chat.one", "a photo", direction="incoming", state="read",
                                   transport_id=f"gmessages:{name}")
                store.set_media(record.uid, "0", state="failed", mime="image/png", error=error,
                                retryable=False, now=1000)
            self.assertEqual(store.media_due(now=10 ** 10), [], "no schedule re-reads a dead file")
            self.assertEqual(store.media_due(now=10 ** 10, reconnected=True), [], "nor does reconnecting")

    def test_an_incoming_picture_never_says_the_phone_is_sending(self):
        from prairie_apps.messages import _media_status
        with tempfile.TemporaryDirectory() as temp:
            store = AccountStore(Path(temp) / "messages.db")
            self.addCleanup(store.close)
            record = store.add("chat.one", "a photo", direction="incoming", state="read", transport_id="gmessages:w2")
            store.set_media(record.uid, "0", state="pending", mime="image/heic", error="waiting_for_phone", size=983048)
            text, retry = _media_status(store.media_part(record.uid, "0"))
            # It is arriving here. Nothing is being sent, and saying so is what
            # made a received picture look like one of his own going out.
            self.assertNotIn("send", text.lower(), text)
            self.assertTrue(retry, "a waiting picture can always be retried")
            self.assertIn("photo", text)

    def test_a_picture_that_never_arrives_ends_in_a_plain_failure(self):
        from prairie_apps.messages import _media_status
        account, provider, store = self.connected()
        provider._process.request("fake.incoming_media", {"id": "w3", "parts": [
            {"part": "0", "state": "pending", "error": "waiting_for_phone", "size": 2162696, "mime": "image/heic"}]})
        uid = self.message(store, "whatsapp:w3")
        # The helper spends its re-reads and reports the wait as a failure.
        provider._process.request("fake.media", {"message": "w3", "part": "0", "state": "failed",
                                                 "error": "no_full_size", "retryable": True,
                                                 "size": 2162696, "mime": "image/heic"})
        wait(lambda: store.media_part(uid, "0").state == "failed", "the wait ended")
        part = store.media_part(uid, "0")
        self.assertTrue(part.retryable, "it can still be retried")
        text, retry = _media_status(part)
        self.assertTrue(retry and "hasn't made this photo available" in text, text)
        self.assertEqual(self.sends(account), [], "nothing was ever delivered")

    def test_every_way_a_picture_can_fail_ends_in_a_picture_or_a_clear_failure(self):
        from prairie_apps.messages import _media_status
        account, provider, store = self.connected()
        # mime and size as the helper reports them, then what Messages must show.
        cases = [
            ("ok", {"content": "\xff\xd8\xff arrived"}, "done", None),
            ("gone", {"state": "failed", "error": "gone", "retryable": False}, "failed", "no longer has this photo"),
            ("timeout", {"state": "failed", "error": "timeout", "retryable": True}, "failed", "didn't download"),
            ("nofile", {"state": "failed", "error": "no_full_size", "retryable": True}, "failed",
             "hasn't made this photo available"),
            ("big", {"state": "failed", "error": "too_large", "retryable": False, "size": 20 * 1024 * 1024},
             "failed", "too large to download here"),
        ]
        for name, spec, expected, wording in cases:
            with self.subTest(case=name):
                provider._process.request("fake.incoming_media", {"id": f"m-{name}", "parts": [
                    {"part": "0", "mime": "image/jpeg", "name": f"{name}.jpg", **spec}]})
                uid = self.message(store, f"whatsapp:m-{name}")
                wait(lambda u=uid, e=expected: store.media_part(u, "0").state == e, f"{name} reached {expected}")
                part = store.media_part(uid, "0")
                if wording is None:
                    self.assertTrue(store.message(uid).attachments, "the picture is there")
                    continue
                text, retry = _media_status(part)
                self.assertIn(wording, text)
                # Never a spinner with nothing to do: either it can be retried,
                # or the row says plainly why it cannot be fetched here.
                self.assertTrue(retry or "on your phone" in text, text)
                self.assertNotIn("send", text.lower(), f"{name} must not mention sending: {text}")
        # A picture that arrives while the account is offline downloads when it is back.
        provider._process.request("fake.status", {"state": "offline"})
        provider._process.request("fake.incoming_media", {"id": "m-offline", "parts": [
            {"part": "0", "state": "pending", "error": "not_connected", "mime": "image/jpeg", "name": "late.jpg"}]})
        uid = self.message(store, "whatsapp:m-offline")
        provider._process.request("fake.status", {"state": "connected"})
        wait(lambda: store.message(uid).attachments, "downloaded once the account was back")
        # The whole matrix, offline included, delivered nothing.
        self.assertEqual(self.sends(account), [], "no send was attempted on any path")
        self.assertFalse(any("force" in item for item in self.fetches(account)),
                         "the phone is never asked to send a picture again")

    def test_a_group_mms_the_phone_is_fetching_gives_way_to_its_parts(self):
        account, provider, store = self.connected()
        provider._process.request("fake.incoming_media", {"id": "g5", "conversation": "group.one", "parts": [
            {"part": "mms", "state": "pending", "error": "waiting_for_phone", "mime": "application/octet-stream", "name": ""}]})
        uid = self.message(store, "whatsapp:g5")
        self.assertEqual(provider.media_parts(uid)[0].noun, "picture message")
        provider._process.request("fake.incoming_media", {"id": "g5", "conversation": "group.one", "parts": [
            {"part": "0", "content": "GIF89a..", "mime": "image/gif", "name": "a.gif"},
            {"part": "1", "state": "downloading", "mime": "video/mp4", "name": "b.mp4"}]})
        # Each part commits on its own, so the first can be attached before the second is recorded.
        wait(lambda: [(p.part, p.state) for p in store.media_parts(uid)] == [("0", "done"), ("1", "downloading")],
             "first part attached, second on its way")
        self.assertEqual(len(store.message(uid).attachments), 1)
        self.assertEqual(provider.sender(uid), "Fake Friend")

    def test_a_media_event_before_its_message_waits_for_it(self):
        account, provider, store = self.connected()
        provider._process.request("fake.media", {"message": "p6", "part": "0", "content": "\xff\xd8\xff early"})
        time.sleep(0.2)
        provider._process.request("fake.incoming_media", {"id": "p6", "parts": [{"part": "0", "state": "downloading"}]})
        uid = self.message(store, "whatsapp:p6")
        wait(lambda: store.message(uid).attachments, "the early file was applied")

    def test_a_file_stored_before_media_states_is_not_imported_twice(self):
        account, provider, store = self.connected()
        media = self.accounts.directory(account.id) / "media"
        media.mkdir(exist_ok=True)
        (media / "p7-0-old.jpg").write_bytes(b"\xff\xd8\xff same")
        staged = store.attach_file("chat.one", media / "p7-0-old.jpg", name="p7-0-old.jpg")
        record = store.add("chat.one", "", direction="incoming", state="received", transport_id="whatsapp:p7",
                           attachment_uids=(staged.uid,))
        provider._process.request("fake.incoming_media", {"id": "p7", "parts": [{"part": "0", "content": "\xff\xd8\xff same"}]})
        wait(lambda: store.media_part(record.uid, "0") and store.media_part(record.uid, "0").state == "done", "recorded as done")
        self.assertEqual(len(store.message(record.uid).attachments), 1)

    def test_pictures_older_helpers_replaced_with_text_are_fetched_again(self):
        (self.helpers / "gmessages").write_text((self.helpers / "whatsapp").read_text())
        (self.helpers / "gmessages").chmod(0o755)
        account = self.accounts.create("gmessages")
        account.pending = False
        self.accounts.save(account)
        self.secrets.items[account.id] = "fake-session-token"
        # The store as the gmessages helper 0.1 left it.
        old = AccountStore(self.accounts.directory(account.id) / "messages.db")
        old.remember_conversation({"id": "chat.one", "kind": "direct", "participants": []})
        photo = old.add("chat.one", "[A photo couldn't be downloaded. Open it on your phone.]", direction="incoming",
                        state="read", timestamp=100, transport_id="gmessages:old1")
        mixed = old.add("chat.one", "see below\n[An attachment couldn't be downloaded. Open it on your phone.]",
                        direction="incoming", state="read", timestamp=101, transport_id="gmessages:old2")
        huge = old.add("chat.one", "[A video (40 MB) is on your phone. It's too large to download here.]",
                       direction="incoming", state="read", timestamp=102, transport_id="gmessages:old3")
        old.close()
        script = {"old1": [{"part": "0", "content": "\xff\xd8\xff recovered"}],
                  "old2": [{"state": "failed", "error": "not_found", "retryable": False}]}
        (self.accounts.directory(account.id) / "media-script.json").write_text(json.dumps(script))
        account, provider, store = self.connected(account)
        wait(lambda: store.message(photo.uid).attachments, "the missed photo was recovered")
        self.assertEqual(store.message(photo.uid).body, "")
        self.assertEqual([(p.part, p.state) for p in store.media_parts(photo.uid)], [("0", "done")])
        wait(lambda: store.media_parts(mixed.uid)[0].state == "failed", "a message the phone no longer has")
        self.assertEqual(store.message(mixed.uid).body, "see below")
        self.assertEqual((store.media_parts(mixed.uid)[0].error, store.media_parts(mixed.uid)[0].retryable), ("not_found", False))
        self.assertEqual([(p.state, p.error, p.retryable) for p in store.media_parts(huge.uid)], [("failed", "too_large", False)])
        self.assertEqual(sorted(f["message"] for f in self.fetches(account)), ["old1", "old2"], "a video too large isn't asked for")
        self.assertTrue(all("part" not in f for f in self.fetches(account)), "the helper looks up which part it was")


class MediaStoreTest(unittest.TestCase):
    def test_failures_back_off_and_done_stays_done(self):
        from prairie_apps.messages_accounts import media_retry_delay
        with tempfile.TemporaryDirectory() as temp:
            store = AccountStore(Path(temp) / "messages.db")
            self.addCleanup(store.close)
            record = store.add("chat.one", "hi", direction="incoming", state="received", transport_id="fake:m1")
            store.set_media(record.uid, "0", state="failed", error="network", now=1000)
            self.assertEqual(store.media_part(record.uid, "0").next_attempt, 1000 + media_retry_delay(1))
            store.set_media(record.uid, "0", state="failed", error="network", now=2000)  # the same round
            self.assertEqual(store.media_part(record.uid, "0").attempts, 1)
            store.mark_media_requested(record.uid, "0")
            store.set_media(record.uid, "0", state="failed", error="network", now=3000)
            self.assertEqual((store.media_part(record.uid, "0").attempts, store.media_part(record.uid, "0").next_attempt),
                             (2, 3000 + media_retry_delay(2)))
            self.assertEqual(store.media_due(now=3001), [])
            self.assertEqual(store.media_due(now=3001, reconnected=True), [(record.uid, "chat.one", "m1", "0")])
            self.assertEqual(media_retry_delay(99), 6 * 3600)
            source = Path(temp) / "photo"
            source.write_bytes(b"\xff\xd8\xff")
            store.complete_media(record.uid, "0", source, name="photo", mime="")
            self.assertEqual(store.message(record.uid).attachments[0].content_type, "image/jpeg")
            self.assertEqual(store.set_media(record.uid, "0", state="failed", error="network"), ("done", "done"))
            with self.assertRaises(ValueError):
                store.set_media(record.uid, "../x", state="pending")


class WaitingForPhoneBoundTest(unittest.TestCase):
    """A picture whose full-size file the phone never publishes resolves; it is never a spinner for ever.

    Nick's ThinkPad, 2026-09-22: 88 pictures sat on "Getting this photo from
    your phone…" with a spinner. The helper's own give-up after its re-reads
    lives in its memory, so every helper restart, reconnect and tap started it
    over, and Messages itself put no bound on a part in that state.
    """

    def setUp(self):
        from prairie_apps import messages_accounts
        self.m = messages_accounts
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "messages.db"
        self.store = AccountStore(self.path)
        self.addCleanup(self.store.close)
        self.uid = self.store.add_media_message("chat.one", "", direction="incoming", state="read", timestamp=900,
                                                transport_id="gmessages:232427")

    def wait(self, now, error="waiting_for_phone"):
        return self.store.set_media(self.uid, "0", state="pending", mime="image/heic", name="IMG_9958.heic",
                                    size=737288, error=error, now=now)

    def test_a_helper_that_keeps_reporting_waiting_is_bounded(self):
        bound = self.m.MEDIA_WAITING_BOUND
        self.wait(1000)
        self.wait(1000 + bound - 1)  # the helper re-reports, as after each restart or re-ask
        self.assertEqual(self.store.media_part(self.uid, "0", raw=True).state, "pending", "still inside its wait")
        self.wait(1000 + bound)
        part = self.store.media_part(self.uid, "0", raw=True)
        self.assertEqual((part.state, part.error, part.retryable), ("failed", "no_full_size", True),
                         "the wait resolves to a clear, retryable state")
        self.assertEqual(part.next_attempt, 1000 + bound + self.m.MEDIA_WAITING_REASK, "and is re-read quietly later")

    def test_a_wait_with_no_word_from_the_helper_still_resolves(self):
        self.wait(1000)
        self.store.mark_media_requested(self.uid, "0")  # asked again; nothing comes back
        later = 1000 + self.m.MEDIA_WAITING_BOUND
        self.store.expire_waiting_media(now=later)
        part = self.store.media_part(self.uid, "0", raw=True)
        self.assertEqual((part.state, part.error), ("failed", "no_full_size"))

    def test_the_window_shows_an_overdue_wait_as_resolved_before_the_agent_writes_it(self):
        self.wait(int(time.time()) - self.m.MEDIA_WAITING_BOUND - 5)
        self.assertEqual(self.store.media_part(self.uid, "0", raw=True).state, "pending")
        shown = self.store.media_parts(self.uid)[0]
        self.assertEqual((shown.state, shown.error), ("failed", "no_full_size"), "no spinner past the bound")

    def test_try_again_gives_a_short_fresh_wait_and_then_resolves_again(self):
        bound, window = self.m.MEDIA_WAITING_BOUND, self.m.MEDIA_TAP_WINDOW
        self.wait(1000)
        self.wait(1000 + bound)
        self.assertEqual(self.store.media_part(self.uid, "0", raw=True).state, "failed")
        tapped = 1000 + bound + 50
        self.store.restart_media_wait(self.uid, "0", now=tapped)
        self.wait(tapped + 1)  # the helper answers the tap: still no file
        self.assertEqual(self.store.media_part(self.uid, "0", raw=True).state, "pending", "the tap is being tried")
        self.wait(tapped + window)
        self.assertEqual(self.store.media_part(self.uid, "0", raw=True).state, "failed", "and the tap's wait is bounded too")

    def test_a_file_that_arrives_after_the_bound_still_completes(self):
        self.wait(1000)
        self.wait(1000 + self.m.MEDIA_WAITING_BOUND)
        source = self.path.parent / "photo"
        source.write_bytes(b"\xff\xd8\xff the photo")
        self.store.complete_media(self.uid, "0", source, name="IMG_9958.heic", mime="image/jpeg")
        self.assertEqual(self.store.media_part(self.uid, "0", raw=True).state, "done")
        self.assertEqual(len(self.store.message(self.uid).attachments), 1)

    def test_a_store_from_before_the_bound_gets_one(self):
        import sqlite3
        self.wait(1000)
        self.store.close()
        with sqlite3.connect(self.path) as db:
            db.execute("ALTER TABLE account_media DROP COLUMN waiting_since")
            db.execute("UPDATE account_media SET updated=2000")
        self.store = AccountStore(self.path)
        self.assertEqual(self.store.media_part(self.uid, "0", raw=True).waiting_since, 2000,
                         "an existing wait is counted from its last report")


class ReactionStoreTest(unittest.TestCase):
    def test_replacing_reactions_ignores_junk_and_reports_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            store = AccountStore(Path(temp) / "messages.db")
            self.addCleanup(store.close)
            record = store.add("chat.one", "hi", direction="incoming", state="received", transport_id="fake:m1")
            self.assertTrue(store.replace_reactions(record.uid, [{"sender": "friend", "emoji": "👍"}, "junk",
                                                                  {"sender": "", "emoji": "👍"},
                                                                  {"sender": "x", "emoji": "not an emoji at all!!"}]))
            self.assertFalse(store.replace_reactions(record.uid, [{"sender": "friend", "emoji": "👍"}]))
            self.assertEqual([(r.sender, r.emoji) for r in store.reactions(record.uid)], [("friend", "👍")])


class AttachmentTypeTest(unittest.TestCase):
    def test_stored_images_without_a_type_are_recognised(self):
        with tempfile.TemporaryDirectory() as temp:
            store = AccountStore(Path(temp) / "messages.db")
            self.addCleanup(store.close)
            photo = Path(temp) / "63788"
            photo.write_bytes(b"\xff\xd8\xff\xe0" + b"\0" * 64)
            notes = Path(temp) / "notes"
            notes.write_bytes(b"plain text")
            first = store.attach_file("chat.one", photo)
            second = store.attach_file("chat.one", notes)
            self.assertEqual((first.content_type, second.content_type), ("application/octet-stream", "application/octet-stream"))
            self.assertEqual(store.repair_attachment_types(), 1)
            kinds = dict(store._connection.execute("SELECT uid, content_type FROM message_attachments").fetchall())
            self.assertEqual((kinds[first.uid], kinds[second.uid]), ("image/jpeg", "application/octet-stream"))


class CookieTest(unittest.TestCase):
    def test_reads_only_named_cookies_for_the_domains(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as temp:
            profile = Path(temp)
            db = sqlite3.connect(profile / "cookies.sqlite")
            db.execute("CREATE TABLE moz_cookies(host TEXT, name TEXT, value TEXT)")
            db.executemany("INSERT INTO moz_cookies VALUES(?,?,?)", [
                (".google.com", "SID", "a"), ("messages.google.com", "OSID", "b"),
                (".evil.example", "HSID", "c"), (".google.com", "NID", "d")])
            db.commit(); db.close()
            found = _read_firefox_cookies(profile, {"SID", "OSID", "HSID"}, ["google.com", "messages.google.com"])
            self.assertEqual(found, {"SID": "a", "OSID": "b"})

    def test_capture_closes_firefox_and_deletes_the_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            firefox = Path(temp) / "firefox"
            # A stand-in browser that "signs in" by writing the cookie database into its profile.
            firefox.write_text(f"""#!{sys.executable}
import sqlite3, sys, time
profile = sys.argv[sys.argv.index('--profile') + 1]
time.sleep(0.3)
db = sqlite3.connect(profile + '/cookies.sqlite')
db.execute('CREATE TABLE moz_cookies(host TEXT, name TEXT, value TEXT)')
db.execute("INSERT INTO moz_cookies VALUES('.fake.example','SID','cookie-value')")
db.commit(); db.close()
open('{temp}/profile-path', 'w').write(profile)
time.sleep(60)
""")
            firefox.chmod(0o755)
            found = capture_browser_cookies("https://fake.example/", ["SID"], ["fake.example"], firefox=str(firefox), poll=0.1, timeout=20)
            self.assertEqual(found, {"SID": "cookie-value"})
            profile = Path((Path(temp) / "profile-path").read_text())
            self.assertFalse(profile.exists())


if __name__ == "__main__":
    unittest.main()
