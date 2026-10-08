# SPDX-License-Identifier: Apache-2.0
"""Background mail agent against a local TLS IMAP server (no network, no session bus)."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from charlie_luma import APP_ID
from charlie_luma import mail_agent
from charlie_luma.mail_agent import (
    AgentState,
    MailAgentCore,
    MailboxMark,
    NewMail,
    backoff_delay,
    uids_after,
)
from charlie_luma.model import Account, ServerConfig
from charlie_luma.store import MailStore

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_imap import FakeImapServer, client_context, make_certificates, make_message  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT_ID = "mail-agent-test"
ADDRESS = "reader@example.test"
PASSWORD = "correct horse"

try:
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
except (ImportError, ValueError):
    GLib = None


def wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class RecordingNotifier:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[dict] = []
        self.posted: dict[str, dict] = {}
        self.withdrawn: list[str] = []
        self._next = 1

    def notify(self, key: str, summary: str, body: str = "", **options) -> int:
        with self.lock:
            call = dict(key=key, summary=summary, body=body, **options)
            self.calls.append(call)
            self.posted[key] = call
            self._next += 1
            return self._next

    def withdraw(self, key: str) -> None:
        with self.lock:
            self.withdrawn.append(key)
            self.posted.pop(key, None)

    def keys(self) -> list[str]:
        with self.lock:
            return list(self.posted)

    def for_key(self, prefix: str) -> list[dict]:
        with self.lock:
            return [call for call in self.calls if call["key"].startswith(prefix)]


class RecordingPublisher:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.values: dict[str, object] = {}

    def __call__(self, key: str, value) -> None:
        with self.lock:
            self.values[key] = value

    def get(self, key: str):
        with self.lock:
            return self.values.get(key)


class FakeSecrets:
    def __init__(self, values: dict[tuple[str, str], str]) -> None:
        self.values = dict(values)
        self.stored: list[tuple[str, str]] = []

    def lookup(self, account_id: str, kind: str) -> str | None:
        return self.values.get((account_id, kind))

    def store(self, account_id: str, kind: str, value: str) -> None:
        self.values[(account_id, kind)] = value
        self.stored.append((account_id, kind))


class FakeLauncher:
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.activated = 0

    def open_message(self, message_id: str, token: str = "") -> None:
        self.opened.append(message_id)

    def activate(self, token: str = "") -> None:
        self.activated += 1


class FakeOAuth:
    def __init__(
        self,
        access_token: str,
        refreshed: str | None,
        forced: tuple[str, str | None] | None = None,
    ) -> None:
        self.result = (access_token, refreshed)
        self.forced = forced or self.result
        self.calls = 0
        self.forced_calls = 0

    def access_token(self, _token_json: str, *, force_refresh: bool = False):
        self.calls += 1
        if force_refresh:
            self.forced_calls += 1
            return self.forced
        return self.result


@unittest.skipUnless(shutil.which("openssl"), "openssl is needed to create the test certificate authority")
class MailAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.certificates = tempfile.TemporaryDirectory(prefix="charlie-agent-tls-")
        cls.ca, cls.cert, cls.key = make_certificates(Path(cls.certificates.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.certificates.cleanup()

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="charlie-agent-")
        self.root = Path(self.directory.name)
        self.store_path = self.root / "data" / "charlie" / "mail.db"
        self.state_path = self.root / "state" / "charlie" / "agent.json"
        self.server: FakeImapServer | None = None
        self.core: MailAgentCore | None = None
        self.notifier = RecordingNotifier()
        self.publisher = RecordingPublisher()
        self.launcher = FakeLauncher()
        self.secrets = FakeSecrets({(ACCOUNT_ID, "password"): PASSWORD})
        self.store: MailStore | None = None

    def tearDown(self) -> None:
        if self.core is not None:
            self.core.stop()
        if self.server is not None:
            self.server.close()
        if self.store is not None:
            self.store.close()
        self.directory.cleanup()

    # -- fixtures -----------------------------------------------------------------
    def start_server(self, **options) -> FakeImapServer:
        self.server = FakeImapServer(self.cert, self.key, username=ADDRESS, password=PASSWORD, **options).start()
        return self.server

    def seed_account(self, provider: str = "imap") -> Account:
        self.store = MailStore(self.store_path)
        account = Account(ACCOUNT_ID, "Reader", ADDRESS, provider=provider)
        self.store.upsert_account(account)
        self.store.upsert_server_config(ACCOUNT_ID, ServerConfig("localhost", self.server.port, "localhost", 465,
                                                                 ADDRESS))
        return account

    def make_core(self, notifier=None, oauth=None) -> MailAgentCore:
        self.core = MailAgentCore(
            store_path=self.store_path, state_path=self.state_path, secrets=self.secrets,
            notifier=notifier or self.notifier, publish=self.publisher, dispatch=lambda callback: callback(),
            launcher=self.launcher, oauth=oauth, ssl_context=client_context(self.ca))
        return self.core

    def start_agent(self, **server_options) -> MailAgentCore:
        if self.server is None:
            self.start_server(**server_options)
        if self.store is None:
            self.seed_account()
        core = self.make_core()
        self.assertEqual(core.reload(), 1)
        return core

    def wait_idle(self) -> None:
        self.assertTrue(self.server.wait_for(lambda: bool(self.server.idling)), "agent never entered IDLE")

    # -- behaviour -------------------------------------------------------------------
    def test_first_run_sets_baseline_without_notifying(self):
        server = self.start_server()
        server.deliver(make_message(1), make_message(2), make_message(3))
        self.start_agent()
        self.wait_idle()
        self.assertTrue(wait_until(lambda: self.publisher.get("unread-count") == 3))
        self.assertEqual(self.publisher.get("unread-by-account"), {ACCOUNT_ID: 3})
        self.assertEqual(AgentState(self.state_path).get(ACCOUNT_ID), MailboxMark(7, 3))
        self.assertEqual(self.notifier.calls, [])
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("UID FETCH", server.commands)

    def test_new_message_during_idle_notifies_once_and_is_upserted(self):
        self.start_agent()
        self.wait_idle()
        with self.assertLogs("charlie.agent", level="INFO") as logs:
            (uid,) = self.server.deliver(make_message(1, sender="Grace Hopper <grace@example.test>",
                                                      subject="Compiler notes"))
            self.assertTrue(wait_until(lambda: self.notifier.for_key("message-")))
            self.assertTrue(wait_until(lambda: self.publisher.get("unread-count") == 1))
            self.wait_idle()
        time.sleep(0.3)
        calls = self.notifier.for_key("message-")
        self.assertEqual(len(calls), 1)
        call = calls[0]
        message_id = f"{ACCOUNT_ID}:inbox:{uid}"
        self.assertEqual(call["key"], f"message-{message_id}")
        self.assertEqual((call["summary"], call["body"]), ("Grace Hopper", "Compiler notes"))
        self.assertEqual((call["public_summary"], call["public_body"]), ("Charlie", "New mail"))
        self.assertEqual(call["category"], "email.arrived")
        self.assertEqual([action for action, _label in call["actions"]], ["default", "mark-read"])
        (stored,) = self.store.messages_by_ids((message_id,))
        self.assertTrue(stored.unread)
        self.assertEqual(stored.sender_address, "grace@example.test")
        self.assertEqual(AgentState(self.state_path).get(ACCOUNT_ID), MailboxMark(7, uid))
        self.assertNotIn("\\Seen", self.server.flags(uid))
        text = "\n".join(logs.output)
        for secret in ("grace@example.test", "Grace", "Compiler", ADDRESS, PASSWORD):
            self.assertNotIn(secret, text)

    def test_more_than_three_new_messages_are_grouped(self):
        self.start_agent()
        self.wait_idle()
        senders = ["Ada <ada@example.test>", "Brian <brian@example.test>", "Cleo <cleo@example.test>",
                   "Dora <dora@example.test>", "Emil <emil@example.test>"]
        uids = self.server.deliver(*(make_message(index, sender=sender) for index, sender in enumerate(senders)))
        self.assertTrue(wait_until(lambda: self.notifier.for_key("new-")))
        time.sleep(0.3)
        self.assertEqual(self.notifier.for_key("message-"), [])
        (grouped,) = self.notifier.for_key("new-")
        self.assertEqual(grouped["summary"], "5 new messages")
        self.assertEqual(grouped["body"], "Ada, Brian, Cleo")
        self.assertEqual(grouped["public_summary"], "Charlie")
        stored = self.store.messages_by_ids(tuple(f"{ACCOUNT_ID}:inbox:{uid}" for uid in uids))
        self.assertEqual(len(stored), 5)

    def test_uidvalidity_change_rebaselines_without_notifying(self):
        AgentState(self.state_path).set(ACCOUNT_ID, MailboxMark(7, 0))
        server = self.start_server(uidvalidity=9)
        server.deliver(make_message(1), make_message(2))
        self.start_agent()
        self.wait_idle()
        self.assertTrue(wait_until(lambda: AgentState(self.state_path).get(ACCOUNT_ID) == MailboxMark(9, 2)))
        self.assertEqual(self.notifier.calls, [])
        server.deliver(make_message(3, subject="After the rebuild"))
        self.assertTrue(wait_until(lambda: len(self.notifier.for_key("message-")) == 1))
        self.assertEqual(self.notifier.for_key("message-")[0]["body"], "After the rebuild")

    def test_authentication_failure_stops_the_account_and_notifies_once(self):
        self.secrets.values[(ACCOUNT_ID, "password")] = "wrong"
        core = self.start_agent()
        self.assertTrue(wait_until(lambda: self.notifier.for_key("auth-")))
        worker = core.workers()[ACCOUNT_ID]
        self.assertTrue(wait_until(lambda: not worker.is_alive()))
        self.assertTrue(worker.stopped_for_authentication)
        core.check_now()
        core.wake("network")
        time.sleep(0.4)
        self.assertEqual(self.server.failed_logins, 1)
        (call,) = self.notifier.for_key("auth-")
        self.assertEqual(call["summary"], f"Sign in to {ADDRESS} again")
        self.assertEqual(call["public_summary"], "Charlie")
        self.assertNotIn(ADDRESS, call["public_body"])

        self.secrets.values[(ACCOUNT_ID, "password")] = PASSWORD
        core.reload()
        self.wait_idle()
        self.assertEqual(self.server.logins, 1)
        self.assertEqual(self.notifier.withdrawn, [f"auth-{ACCOUNT_ID}"])
        call["on_action"]("default")
        self.assertEqual(self.launcher.activated, 1)

    def test_healthy_start_withdraws_an_auth_alert_from_an_older_agent(self):
        self.start_agent()
        self.wait_idle()
        self.assertEqual(self.notifier.withdrawn, [f"auth-{ACCOUNT_ID}"])

    def test_backoff_doubles_from_one_minute_to_thirty_with_jitter(self):
        self.assertEqual(backoff_delay(0), 0.0)
        self.assertEqual([backoff_delay(failures, 0.5) for failures in range(1, 9)],
                         [60, 120, 240, 480, 960, 1800, 1800, 1800])
        for failures in range(1, 12):
            low, high = backoff_delay(failures, 0.0), backoff_delay(failures, 1.0)
            self.assertGreaterEqual(low, 48)
            self.assertLessEqual(high, 1800)
            self.assertLessEqual(low, high)

    def test_connection_failure_waits_and_a_wake_reconnects_immediately(self):
        server = self.start_server()
        self.seed_account()
        server.close()
        core = self.make_core()
        attempts: list[float] = []
        original = core.connect

        def counting(account, config):
            attempts.append(time.monotonic())
            return original(account, config)

        core.connect = counting
        core.reload()
        self.assertTrue(wait_until(lambda: len(attempts) == 1))
        time.sleep(0.5)
        self.assertEqual(len(attempts), 1, "a refused connection must back off, not spin")
        core.wake("resume")
        self.assertTrue(wait_until(lambda: len(attempts) == 2, 3))

    def test_a_long_healthy_session_that_drops_reconnects_without_backoff(self):
        core = self.start_agent()
        self.wait_idle()
        worker = core.workers()[ACCOUNT_ID]
        self.assertEqual(worker.connections, 1)
        original = mail_agent.HEALTHY_SESSION_SECONDS
        mail_agent.HEALTHY_SESSION_SECONDS = 0.0
        try:
            with self.server.lock:
                dropped = list(self.server.idling)
            for connection in dropped:
                connection.close()  # the server side vanishes, as a NAT timeout would look
            self.assertTrue(wait_until(lambda: worker.connections == 2, 5))
            self.wait_idle()
        finally:
            mail_agent.HEALTHY_SESSION_SECONDS = original

    def test_mark_read_action_sets_seen_on_the_server_and_locally(self):
        self.start_agent()
        self.wait_idle()
        (uid,) = self.server.deliver(make_message(1))
        self.assertTrue(wait_until(lambda: self.notifier.for_key("message-")))
        self.assertTrue(wait_until(lambda: self.publisher.get("unread-count") == 1))
        call = self.notifier.for_key("message-")[0]
        call["on_action"]("mark-read")
        self.assertTrue(self.server.wait_for(lambda: "\\Seen" in self.server.flags(uid)))
        message_id = f"{ACCOUNT_ID}:inbox:{uid}"
        self.assertTrue(wait_until(lambda: not self.store.messages_by_ids((message_id,))[0].unread))
        self.assertTrue(wait_until(lambda: call["key"] in self.notifier.withdrawn))
        self.assertTrue(wait_until(lambda: self.publisher.get("unread-count") == 0))

    def test_default_action_opens_the_message_in_charlie(self):
        self.start_agent()
        self.wait_idle()
        (uid,) = self.server.deliver(make_message(1))
        self.assertTrue(wait_until(lambda: self.notifier.for_key("message-")))
        call = self.notifier.for_key("message-")[0]
        call["on_action"]("default")
        self.assertEqual(self.launcher.opened, [f"{ACCOUNT_ID}:inbox:{uid}"])
        self.assertIn(call["key"], self.notifier.withdrawn)

    def test_mail_read_elsewhere_withdraws_its_notification(self):
        self.start_agent()
        self.wait_idle()
        (uid,) = self.server.deliver(make_message(1))
        self.assertTrue(wait_until(lambda: self.notifier.for_key("message-")))
        self.wait_idle()
        with self.server.lock:
            message = next(item for item in self.server.messages if item.uid == uid)
            message.flags.add("\\Seen")
            for connection in self.server.idling:
                connection.push(f"* 1 FETCH (UID {uid} FLAGS (\\Seen))\r\n")
        self.assertTrue(wait_until(lambda: f"message-{ACCOUNT_ID}:inbox:{uid}" in self.notifier.withdrawn))
        self.assertTrue(wait_until(lambda: self.publisher.get("unread-count") == 0))

    def test_polls_when_the_server_has_no_idle(self):
        self.start_server(idle=False)
        self.seed_account()
        core = self.make_core()
        core.poll_seconds = 0.3
        core.reload()
        self.assertTrue(wait_until(lambda: AgentState(self.state_path).get(ACCOUNT_ID) is not None))
        self.server.deliver(make_message(1, subject="Polled"))
        self.assertTrue(wait_until(lambda: self.notifier.for_key("message-"), 5))
        self.assertNotIn("IDLE", self.server.commands)
        self.assertIn("LOGOUT", self.server.commands)

    def test_stop_breaks_idle_within_the_shutdown_budget(self):
        core = self.start_agent()
        self.wait_idle()
        workers = list(core.workers().values())
        started = time.monotonic()
        core.stop()
        self.core = None
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertFalse(any(worker.is_alive() for worker in workers))

    def test_uid_search_star_quirk_is_filtered(self):
        server = self.start_server()
        server.deliver(make_message(1), make_message(2))
        account = self.seed_account()
        core = self.make_core()
        client = core.connect(account, self.store.server_config(ACCOUNT_ID))
        try:
            client.select("INBOX", readonly=True)
            self.assertEqual(uids_after(client, 1), [2])
            self.assertEqual(uids_after(client, 5), [])  # "6:*" still matches UID 2
        finally:
            client.logout()

    def test_zero_accounts_publish_zero_and_start_nothing(self):
        counts: list[int] = []
        core = self.make_core()
        core.on_accounts = counts.append
        self.assertEqual(core.reload(), 0)
        self.assertEqual(counts, [0])
        self.assertEqual(self.publisher.get("unread-count"), 0)
        self.assertEqual(core.workers(), {})
        self.assertFalse(self.store_path.exists(), "the agent must not create Charlie's database")

    def test_removed_account_is_forgotten(self):
        core = self.start_agent()
        self.wait_idle()
        self.assertTrue(wait_until(lambda: AgentState(self.state_path).get(ACCOUNT_ID) is not None))
        self.store.delete_account(ACCOUNT_ID)
        self.assertEqual(core.reload(), 0)
        self.assertIsNone(AgentState(self.state_path).get(ACCOUNT_ID))
        self.assertEqual(self.publisher.get("unread-by-account"), {})

    def test_retained_gmail_agent_never_uses_saved_grant_or_connects(self):
        from charlie_luma.mail_agent import CredentialsUnavailable
        self.start_server(oauth_token="retained-access")
        self.seed_account(provider="gmail")
        saved = json.dumps({"access_token":"retained-access", "refresh_token":"retained-refresh"})
        self.secrets.values = {(ACCOUNT_ID, "oauth-token"): saved}
        oauth = FakeOAuth("retained-access", None)
        core = self.make_core(oauth=oauth)
        account = self.store.accounts()[0]
        for forced in (False, True):
            with self.assertRaisesRegex(CredentialsUnavailable, "Google sign-in is unavailable"):
                core.credentials(account, force_refresh=forced)
        self.assertEqual(self.server.logins, 0)
        self.assertEqual(self.secrets.values, {(ACCOUNT_ID, "oauth-token"): saved})
        self.assertEqual(self.secrets.stored, [])
        self.assertEqual(oauth.forced_calls, 0)

    def test_oauth_refreshes_and_saves_new_token_for_supported_provider(self):
        self.start_server(oauth_token="access-1")
        self.seed_account(provider="microsoft")
        self.secrets.values = {(ACCOUNT_ID, "oauth-token"): json.dumps({"refresh_token": "r"})}
        oauth = FakeOAuth("access-1", json.dumps({"access_token": "access-1", "refresh_token": "r"}))
        core = self.make_core(oauth=oauth)
        core.reload()
        self.wait_idle()
        self.assertEqual(self.server.logins, 1)
        self.assertEqual(self.secrets.stored, [(ACCOUNT_ID, "oauth-token")])

    def test_microsoft_refreshes_oauth_and_saves_the_new_token(self):
        self.start_server(oauth_token="access-1")
        self.seed_account(provider="microsoft")
        self.secrets.values = {(ACCOUNT_ID, "oauth-token"): json.dumps({
            "client_id": "charlie-client-id", "refresh_token": "r"
        })}
        oauth = FakeOAuth(
            "access-1",
            json.dumps({
                "client_id": "charlie-client-id",
                "access_token": "access-1",
                "refresh_token": "r",
            }),
        )
        core = self.make_core(oauth=oauth)
        core.reload()
        self.wait_idle()
        self.assertEqual(self.server.logins, 1)
        self.assertEqual(self.secrets.stored, [(ACCOUNT_ID, "oauth-token")])

    def test_microsoft_rejects_stale_access_token_then_recovers_without_alerting(self):
        self.start_server(oauth_token="fresh-access")
        self.seed_account(provider="microsoft")
        self.secrets.values = {(ACCOUNT_ID, "oauth-token"): json.dumps({
            "access_token": "stale-access", "refresh_token": "r"
        })}
        fresh_json = json.dumps({
            "access_token": "fresh-access", "refresh_token": "r"
        })
        oauth = FakeOAuth(
            "stale-access", None, forced=("fresh-access", fresh_json)
        )
        core = self.make_core(oauth=oauth)
        core.reload()
        self.wait_idle()
        self.assertEqual(self.server.failed_logins, 1)
        self.assertEqual(self.server.logins, 1)
        self.assertEqual(oauth.forced_calls, 1)
        self.assertEqual(self.notifier.for_key("auth-"), [])
        self.assertEqual(self.secrets.stored, [(ACCOUNT_ID, "oauth-token")])

    def test_messages_the_account_sent_itself_are_not_announced(self):
        self.start_agent()
        self.wait_idle()
        self.server.deliver(make_message(1, sender=f"Reader <{ADDRESS}>"))
        self.assertTrue(wait_until(lambda: self.publisher.get("unread-count") == 1))
        time.sleep(0.3)
        self.assertEqual(self.notifier.calls, [])


@unittest.skipUnless(GLib is not None, "PyGObject is needed for the notification wire format")
class NotifierPrivacyTests(unittest.TestCase):
    class Connection:
        def __init__(self) -> None:
            self.calls: list[tuple] = []
            self.subscriptions = 0

        def signal_subscribe(self, *_args) -> int:
            self.subscriptions += 1
            return self.subscriptions

        def signal_unsubscribe(self, _subscription) -> None:
            pass

        def call_sync(self, bus, path, interface, method, parameters, *_args):
            self.calls.append((method, parameters.unpack() if parameters is not None else None))
            return GLib.Variant("(u)", (len(self.calls),))

        def call(self, bus, path, interface, method, parameters, *_args):
            self.calls.append((method, parameters.unpack() if parameters is not None else None))

    def notify_while(self, locked: bool, details: bool = False):
        from charlie_luma.background import Notifier

        connection = self.Connection()
        notifier = Notifier(connection, APP_ID, "Charlie", lock_state=lambda: locked,
                            details_on_lock_screen=lambda: details)
        core = MailAgentCore(store_path=Path("/nonexistent/mail.db"), state_path=Path("/nonexistent/agent.json"),
                             secrets=FakeSecrets({}), notifier=notifier, publish=lambda *_: None,
                             dispatch=lambda callback: callback())
        account = Account(ACCOUNT_ID, "Reader", ADDRESS)
        core.announce(account, [NewMail(f"{ACCOUNT_ID}:inbox:4", ACCOUNT_ID, 4, "Ada Lovelace", "Engine notes")])
        (method, arguments), = connection.calls
        self.assertEqual(method, "Notify")
        return arguments, notifier, connection

    def test_locked_screen_without_details_gets_the_public_text(self):
        arguments, _notifier, _connection = self.notify_while(locked=True)
        app_name, _replaces, _icon, summary, body, actions, hints, _timeout = arguments
        self.assertEqual((app_name, summary, body), ("Charlie", "Charlie", "New mail"))
        self.assertEqual(hints["desktop-entry"], APP_ID)
        self.assertEqual(hints["category"], "email.arrived")
        self.assertEqual(actions, ["default", "Open", "mark-read", "Mark Read"])

    def test_unlocked_or_allowed_details_show_sender_and_subject(self):
        for locked, details in ((False, False), (True, True)):
            with self.subTest(locked=locked, details=details):
                arguments, _notifier, _connection = self.notify_while(locked=locked, details=details)
                self.assertEqual(arguments[3:5], ("Ada Lovelace", "Engine notes"))

    def test_unlock_replaces_redacted_text_silently(self):
        arguments, notifier, connection = self.notify_while(locked=True)
        notifier._screensaver_changed(None, None, None, None, None, GLib.Variant("(b)", (False,)))
        method, replaced = connection.calls[-1]
        self.assertEqual(method, "Notify")
        self.assertEqual(replaced[1], 1)  # replaces the redacted notification's id
        self.assertEqual(replaced[3:5], ("Ada Lovelace", "Engine notes"))
        self.assertTrue(replaced[6]["suppress-sound"])


@unittest.skipUnless(GLib is not None, "PyGObject is needed for the agent bus contract")
class BackgroundContractTests(unittest.TestCase):
    class Invocation:
        def __init__(self) -> None:
            self.error = None
            self.returned = False

        def return_dbus_error(self, name, _message) -> None:
            self.error = name

        def return_value(self, _value) -> None:
            self.returned = True

    class Bus:
        def __init__(self, service_owner: str | None) -> None:
            self.service_owner = service_owner

        def call_sync(self, _bus, _path, _interface, method, parameters, *_args):
            if method == "GetNameOwner" and self.service_owner:
                return GLib.Variant("(s)", (self.service_owner,))
            raise GLib.Error("no owner")

    def agent(self, service_owner: str | None):
        from charlie_luma.background import Agent

        class Probe(Agent):
            app_id = APP_ID
            agent_id = f"{APP_ID}.Agent"

        probe = Probe(connection=self.Bus(service_owner))
        probe.woken = []
        probe._enqueue = probe.woken.append
        return probe

    def wake(self, probe, sender: str, reason: str = "network", details=None):
        invocation = self.Invocation()
        probe._handle_call(None, sender, "/org/projectluma/BackgroundAgent1", "org.projectluma.BackgroundAgent1",
                           "Wake", GLib.Variant("(sa{sv})", (reason, details or {})), invocation)
        return invocation

    def test_wake_is_accepted_only_from_the_background_service(self):
        probe = self.agent(":1.7")
        self.assertEqual(self.wake(probe, ":1.9").error, "org.projectluma.BackgroundAgent1.Error.NotAuthorized")
        self.assertEqual(probe.woken, [])
        accepted = self.wake(probe, ":1.7", "schedule",
                             {"schedule": GLib.Variant("s", "digest"), "missed": GLib.Variant("b", True)})
        self.assertTrue(accepted.returned)
        self.assertEqual((probe.woken[0].reason, probe.woken[0].schedule, probe.woken[0].missed),
                         ("schedule", "digest", True))
        self.assertEqual(self.wake(probe, ":1.7", "unlock").error,
                         "org.projectluma.BackgroundAgent1.Error.InvalidArgument")
        self.assertEqual(self.wake(self.agent(None), ":1.7").error,
                         "org.projectluma.BackgroundAgent1.Error.NotAuthorized")

    def test_values_are_typed_as_the_kit_types_them(self):
        probe = self.agent(None)
        probe.publish("unread-count", 4)
        probe.publish("unread-by-account", {ACCOUNT_ID: 4})
        values = probe._get_property(None, None, None, None, "Values")
        self.assertEqual(values.get_type_string(), "a{sv}")
        self.assertEqual(values.lookup_value("unread-count", None).get_type_string(), "x")
        by_account = values.lookup_value("unread-by-account", None)
        self.assertEqual(by_account.get_type_string(), "a{sv}")
        self.assertEqual(by_account.lookup_value(ACCOUNT_ID, None).get_type_string(), "x")
        self.assertEqual(probe._get_property(None, None, None, None, "AppId").unpack(), APP_ID)
        with self.assertRaises(ValueError):
            probe.publish("UnreadCount", 1)

    def test_kit_module_is_loaded_by_path_without_its_package(self):
        with tempfile.TemporaryDirectory(prefix="charlie-kit-") as directory:
            package = Path(directory) / "luma_appkit"
            package.mkdir()
            (package / "__init__.py").write_text("raise ImportError('the agent must not import the kit package')\n")
            kit = (
                "import gi\n"
                "gi.require_version('Gio', '2.0')\n"
                "from gi.repository import Gio, GLib\n"
                'AGENT_INTERFACE = "org.projectluma.BackgroundAgent1"\n'
                'AGENT_PATH = "/org/projectluma/BackgroundAgent1"\n'
                "class Agent:\n"
                "    def on_start(self): pass\n"
                "    def on_wake(self, wake): pass\n"
                "    def on_stop(self): pass\n"
                "    def publish(self, name, value): pass\n"
                "    def quit(self, status=0): pass\n"
                "    def run(self, argv=None): return 0\n"
                "def request_background(app_id, **options): pass\n"
            )
            (package / "background.py").write_text(kit)
            script = ("import sys, charlie_luma.background as b\n"
                      "print(b.KIT_BACKGROUND, b.AgentBase.__module__, 'luma_appkit' in sys.modules)\n")
            result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True,
                                    env={**os.environ, "PYTHONPATH": f"{directory}:{ROOT}"}, check=True)
            self.assertEqual(result.stdout.split(), ["True", "charlie_luma._kit_background", "False"])

            (package / "background.py").write_text(kit.replace("/org/projectluma/BackgroundAgent1",
                                                                "/org/projectluma/Background/Agent"))
            result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True,
                                    env={**os.environ, "PYTHONPATH": f"{directory}:{ROOT}"}, check=True)
            self.assertEqual(result.stdout.split(), ["False", "charlie_luma.background", "False"])

            (package / "background.py").write_text(kit + "gi.require_version('Gtk', '4.0')\n")
            result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True,
                                    env={**os.environ, "PYTHONPATH": f"{directory}:{ROOT}"}, check=True)
            self.assertEqual(result.stdout.split()[0], "False")


class AgentImportGraphTests(unittest.TestCase):
    def test_agent_never_loads_the_toolkit(self):
        modules = ["charlie_luma.mail_agent"]
        if GLib is not None:
            modules.append("charlie_luma.background")
        script = (
            "import sys\n"
            + "".join(f"import {name}\n" for name in modules)
            + "print('\\n'.join(sorted(sys.modules)))\n"
        )
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True,
                                env={**os.environ, "PYTHONPATH": str(ROOT)}, check=True)
        loaded = set(result.stdout.split())
        forbidden = [name for name in loaded if name.startswith((
            "gi.repository.Gtk", "gi.repository.Gdk", "gi.repository.Adw", "gi.repository.WebKit",
            "gi.repository.Gsk", "luma_appkit", "charlie_luma.application", "charlie_luma.html_reader"))]
        self.assertEqual(forbidden, [])
        self.assertIn("charlie_luma.mail_agent", loaded)

    def test_entry_point_routes_agent_before_importing_the_application(self):
        for relative in ("data/org.projectluma.Charlie.in", "charlie_luma/__main__.py"):
            source = (ROOT / relative).read_text()
            self.assertLess(source.index('["--agent"]'), source.index("application import main"), relative)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    unittest.main()
