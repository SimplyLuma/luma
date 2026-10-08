#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Nothing is delivered to another person except what a person sent, and each exactly once.

2026-09-16: a contact of Nick's received a picture again. These tests hold the
guarantees of messages_outbound: the only delivery path, the once-only claim,
the kill switch, and a long flaky session in which reconnects, helper crashes,
unanswered sends, media that is gone and backfill must add not one delivery.
The helper is the fake network (tests/fake_messages_bridge.py), which records
every command it receives and refuses deliveries without a person's token.
"""
import ast
import json
import os
from pathlib import Path
import random
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from messages_accounts_unit import AccountsCase, wait  # noqa: E402
from prairie_apps import messages_outbound as outbound  # noqa: E402
from prairie_apps.messages_accounts import AccountStore, BridgeError  # noqa: E402

SOURCES = Path(__file__).resolve().parents[1] / "prairie_apps"


class StructureTest(unittest.TestCase):
    """The guarantees are in the code's shape, checked by reading it."""

    # function -> where it may be called
    ALLOWED = {
        "deliver": {("messages_accounts.py", "_send"), ("messages_accounts.py", "_react")},
        "claim_send": {("messages_accounts.py", "_send"), ("messages_accounts.py", "_react"), ("messages_app_rpc.py", "native_send")},
        "issue_claim": {("messages_accounts.py", "claim_send")},
        "authorize_send": {("messages.py", "_send_message"), ("messages.py", "retry"), ("messages.py", "send_text_reaction"), ("messages_agent.py", "send"),
                           ("messages_accounts.py", "_react"), ("messages_app_rpc.py", "authorize_request")},
    }

    def test_only_the_person_facing_paths_reach_delivery(self):
        problems = []
        for path in sorted(SOURCES.glob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(function):
                    if not isinstance(node, ast.Call):
                        continue
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                    if name in self.ALLOWED and (path.name, function.name) not in self.ALLOWED[name]:
                        # Nested functions are walked with their parents; only the innermost owner counts.
                        owners = [f for f in ast.walk(function) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                                  and node in ast.walk(f)]
                        owner = owners[-1].name
                        if (path.name, owner) not in self.ALLOWED[name]:
                            problems.append(f"{path.name}:{node.lineno} {owner} calls {name}")
                    if name == "request" and node.args and isinstance(node.args[0], ast.Constant) \
                            and node.args[0].value in outbound.DELIVERY_COMMANDS:
                        problems.append(f"{path.name}:{node.lineno} sends {node.args[0].value} through request()")
        self.assertEqual(problems, [])

    def test_the_old_automatic_paths_are_gone(self):
        text = (SOURCES / "messages_accounts.py").read_text()
        self.assertNotIn('"force": True', text, "no media retry asks the helper to force anything")
        self.assertIn("unconfirmed_claims", text)


class OutboundTest(AccountsCase):
    def connected(self):
        account = self.sign_in()
        provider, states = self.provider(account)
        provider.send_timeout = 1.5
        wait(lambda: provider.status["state"] == "ready", "connected")
        store = AccountStore(provider.store_path)
        self.addCleanup(store.close)
        wait(lambda: store.thread("chat.one"), "history imported")
        return account, provider, store

    def data(self, account) -> Path:
        return self.accounts.directory(account.id)

    def sent(self, account) -> list[dict]:
        path = self.data(account) / "sent.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def commands(self, account) -> list[dict]:
        path = self.data(account) / "commands.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def person_sends(self, provider, store, text, conversation="chat.one"):
        record = store.add(conversation, text, direction="outgoing", state="sending")
        return record.uid, provider.send_message(record.uid, store.authorize_send(record.uid))

    def reconnect(self, provider):
        process = provider._process
        process.request("fake.status", {"state": "connected"})
        provider._submit(lambda: None).result(timeout=10)
        provider._submit(lambda: None).result(timeout=10)

    def restart(self, provider):
        old = provider._process
        provider._submit(provider._launch).result(timeout=30)
        wait(lambda: provider._process is not old and provider.status["state"] == "ready", "restarted")

    def test_a_request_without_a_claim_is_refused_and_turns_sending_off(self):
        account, provider, store = self.connected()
        with self.assertRaises(BridgeError) as refused:
            provider._process.request("message.send", {"conversation": "chat.one", "client_id": "a" * 32, "text": "x"})
        self.assertEqual(refused.exception.code, "blocked")
        self.assertIsNotNone(outbound.disabled_reason(self.accounts.root))
        uid, result = self.person_sends(provider, store, "while sending is off")
        self.assertEqual(result["state"], "queued")
        self.assertEqual(self.sent(account), [])
        # The person turns sending back on; the queued message goes out once on the next connection.
        provider.resume_outbound()
        self.reconnect(provider)
        wait(lambda: len(self.sent(account)) == 1, "sent after resuming")
        self.reconnect(provider)
        time.sleep(0.3)
        self.assertEqual([item["client_id"] for item in self.sent(account)], [uid])

    def test_an_unanswered_send_is_never_sent_again_automatically(self):
        account, provider, store = self.connected()
        (self.data(account) / "fake-swallow-send").touch()
        uid, result = self.person_sends(provider, store, "Did this go?")
        self.assertEqual(result["state"], "uncertain")
        self.assertEqual(store.message(uid).send_status, "uncertain")
        for _ in range(3):
            self.reconnect(provider)
        self.restart(provider)
        self.reconnect(provider)
        time.sleep(0.3)
        self.assertEqual(len(self.sent(account)), 1, "delivered once; reconnects and a restart add nothing")
        # Pressing Send again on the same message cannot deliver it: only Review Send's Retry can.
        with self.assertRaises(ValueError):
            store.authorize_send(uid)
        # The restarted helper lists what the network kept, and the row is confirmed, not duplicated.
        wait(lambda: store.message(uid).state == "sent", "confirmed from the network's own copy after restarting")
        self.assertEqual(len([m for m in store.thread("chat.one") if m.body == "Did this go?"]), 1)

    def test_a_helper_that_dies_after_delivering_does_not_deliver_twice(self):
        account, provider, store = self.connected()
        (self.data(account) / "fake-exit-after-send").touch()
        uid, result = self.person_sends(provider, store, "Crash right after")
        self.assertEqual(result["state"], "uncertain")
        wait(lambda: provider.status["state"] == "ready" and provider._process is not None, "restarted", timeout=40)
        for _ in range(3):
            self.reconnect(provider)
        time.sleep(0.3)
        self.assertEqual(len(self.sent(account)), 1)
        wait(lambda: store.message(uid).state == "sent", "confirmed from the network's copy")

    def test_retry_is_a_persons_new_request_delivered_once(self):
        account, provider, store = self.connected()
        (self.data(account) / "fake-swallow-send").touch()
        uid, _ = self.person_sends(provider, store, "Review me")
        retry_token = store.authorize_send(uid, retry=True)
        self.assertEqual(provider.retry_message(uid, retry_token)["state"], "sent")
        provider.retry_message(uid, retry_token)  # the same confirmation arriving twice
        self.assertEqual(len(self.sent(account)), 2, "the person's retry is delivered once")

    def test_offline_sends_go_once_when_connected(self):
        account, provider, store = self.connected()
        provider._process.request("fake.status", {"state": "phone_offline"})
        provider._submit(lambda: None).result(timeout=10)
        uids = [self.person_sends(provider, store, f"offline {n}")[0] for n in range(3)]
        self.assertEqual(self.sent(account), [])
        for _ in range(4):
            self.reconnect(provider)
        wait(lambda: len(self.sent(account)) == 3, "the three offline sends went out")
        time.sleep(0.3)
        self.assertEqual(sorted(item["client_id"] for item in self.sent(account)), sorted(uids))

    def test_rows_from_before_tokens_are_never_sent_automatically(self):
        account, provider, store = self.connected()
        old = store.add("chat.one", "stuck from an older Messages", direction="outgoing", state="sending")
        for _ in range(3):
            self.reconnect(provider)
        time.sleep(0.3)
        self.assertEqual(self.sent(account), [])
        self.assertEqual(store.message(old.uid).send_status, "uncertain")

    def test_too_many_sends_in_a_minute_trips_the_switch(self):
        account, provider, store = self.connected()
        told = []
        provider.on_outbound_disabled = told.append
        results = [self.person_sends(provider, store, f"burst {n}")[1]["state"] for n in range(outbound.BURST_LIMIT + 3)]
        self.assertEqual(results.count("sent"), outbound.BURST_LIMIT)
        self.assertIsNotNone(outbound.disabled_reason(self.accounts.root))
        wait(lambda: told, "the person is told")
        self.assertEqual(len(self.sent(account)), outbound.BURST_LIMIT)

    def test_media_recovery_replay_issues_no_delivery(self):
        """Nick's sequence: attachments Google no longer holds, retried, reconnected, tapped."""
        account, provider, store = self.connected()
        for ident in ("25443", "25508", "797", "26867", "27283", "241992"):
            provider._process.request("fake.incoming_media", {"id": ident, "parts": [
                {"part": "0", "state": "failed", "error": "gone", "retryable": False}]})
        wait(lambda: all(store.uid_for_transport(f"whatsapp:{i}") for i in ("25443", "241992")), "stored")
        for round_ in range(4):
            self.reconnect(provider)
            for ident in ("25443", "797", "241992"):
                uid = store.uid_for_transport(f"whatsapp:{ident}")
                provider._submit(provider._retry_media, uid, "0").result(timeout=10)
        time.sleep(0.3)
        deliveries = [c for c in self.commands(account) if c["cmd"] in outbound.DELIVERY_COMMANDS]
        self.assertEqual(deliveries, [])
        self.assertEqual(self.sent(account), [])

    def test_a_long_flaky_session_delivers_exactly_the_persons_sends(self):
        seed = int(os.environ.get("OUTBOUND_FUZZ_SEED", time.time_ns() % 1_000_000))
        steps = int(os.environ.get("OUTBOUND_FUZZ_STEPS", "160"))
        rng = random.Random(seed)
        account, provider, store = self.connected()
        provider.send_timeout = 1.0
        # This session sends far faster than a person; the burst limit has its own test.
        provider._burst = outbound.BurstGuard(self.accounts.root, limit=10**6)
        persons = []
        for step in range(steps):
            action = rng.randrange(10)
            try:
                if action in (0, 1, 2):
                    if rng.random() < 0.15:
                        (self.data(account) / "fake-swallow-send").touch()
                    uid, _ = self.person_sends(provider, store, f"step {step}")
                    persons.append(uid)
                elif action == 3:
                    self.reconnect(provider)
                elif action == 4 and rng.random() < 0.3:
                    self.restart(provider)
                elif action == 5:
                    provider._process.request("fake.status", {"state": rng.choice(["phone_offline", "connected", "error"])})
                elif action == 6:
                    ident = f"media{step}"
                    provider._process.request("fake.incoming_media", {"id": ident, "parts": [
                        {"part": "0", "state": "failed", "error": rng.choice(["gone", "network", "expired"])}]})
                elif action == 7:
                    provider._process.request("fake.incoming", {"id": f"in{step}", "text": "incoming"})
                elif action == 8:
                    provider._submit(provider._catch_up).result(timeout=30)
                elif action == 9 and persons:
                    uid = rng.choice(persons)  # a flaky layer asks again for a message already handled
                    provider._submit(provider._send, uid).result(timeout=30)
            except (BridgeError, TimeoutError, OSError):
                pass
            if provider.status["state"] != "ready":
                try:
                    self.reconnect(provider)
                except (BridgeError, AttributeError):
                    wait(lambda: provider.status["state"] == "ready", f"back after step {step} (seed {seed})", timeout=40)
        for _ in range(3):
            try:
                self.reconnect(provider)
            except (BridgeError, AttributeError):
                pass
        time.sleep(1.0)
        sent = [item["client_id"] for item in self.sent(account)]
        self.assertEqual(len(sent), len(set(sent)), f"seed {seed}: something was delivered twice")
        self.assertTrue(set(sent) <= set(persons), f"seed {seed}: something no person sent was delivered")
        untokened = [c for c in self.commands(account) if c["cmd"] in outbound.DELIVERY_COMMANDS and not c["user_token"]]
        self.assertEqual(untokened, [], f"seed {seed}")
        for uid in persons:
            record = store.message(uid)
            self.assertTrue(record.state in {"sent", "queued", "sending"} or record.send_status == "uncertain",
                            f"seed {seed}: {uid[:8]} {record.state} {record.send_status}")
        self.assertIsNone(outbound.disabled_reason(self.accounts.root), f"seed {seed}: the person's pace tripped the switch")


if __name__ == "__main__":
    unittest.main()
