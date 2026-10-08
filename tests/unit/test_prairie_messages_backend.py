#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/prairie-core"))

from prairie_apps.messages_backend import (  # noqa: E402
    MessageStore, ModemMessagingTransport, TransportMessage,
    incoming_transport_id, normalize_address,
)


class MessageStoreTests(unittest.TestCase):
    def test_normalization_is_conservative(self) -> None:
        self.assertEqual(normalize_address("+1 (312) 555-0102"), "+13125550102")
        self.assertEqual(normalize_address("312-555-0102"), "3125550102")
        with self.assertRaises(ValueError):
            normalize_address("12")

    def test_private_store_thread_search_and_delivery_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "data/messages.db"
            store = MessageStore(path)
            store.set_display_name("+13125550102", "Maya Chen")
            incoming = store.add(
                "+1 312 555 0102", "Arrived safely", direction="incoming", timestamp=10
            )
            outgoing = store.add(
                "+13125550102", "Wonderful", direction="outgoing", timestamp=11
            )
            self.assertEqual(incoming.state, "received")
            self.assertEqual(outgoing.state, "queued")
            self.assertEqual(store.threads()[0].display_name, "Maya Chen")
            self.assertEqual(store.threads()[0].unread, 1)
            self.assertEqual(len(store.threads("wonder")), 1)
            self.assertEqual(len(store.threads("arrived")), 1)
            store.update_state(outgoing.uid, "sent", "/sms/7")
            self.assertEqual(store.thread("+13125550102")[-1].state, "sent")
            interrupted=store.add("+13125550102","Interrupted",direction="outgoing",state="sending",timestamp=11)
            self.assertEqual(store.recover_interrupted(),0)
            self.assertEqual(next(item for item in store.thread("+13125550102") if item.uid==interrupted.uid).state,"sending")
            store.mark_read("+13125550102")
            self.assertEqual(store.threads()[0].unread, 0)
            imported = store.ingest("+13125550102", "Second", transport_id="/org/freedesktop/ModemManager1/SMS/8", timestamp=12)
            duplicate = store.ingest("+13125550102", "Second", transport_id="/org/freedesktop/ModemManager1/SMS/8", timestamp=12)
            self.assertIsNotNone(imported); self.assertIsNone(duplicate)
            self.assertEqual(store.incoming_revision(), (2, 4))
            store.close()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_modem_transport_gates_missing_sim_without_sending(self) -> None:
        calls = []
        def runner(arguments):
            calls.append(arguments)
            return "/Modem/0" if arguments == ["mmcli", "-L"] else "modem.generic.state-failed-reason: sim-missing"
        transport = ModemMessagingTransport(runner)
        capability = transport.inspect()
        self.assertFalse(capability.available)
        self.assertIn("SIM", capability.reason)
        with self.assertRaises(RuntimeError):
            transport.send("+13125550102", "Hello")
        self.assertFalse(any("--send" in call for call in calls))
        self.assertEqual(transport.received(), ())
        # Import remains readable even while registration/SIM state prevents
        # new sends; already-received modem objects must not be discarded.
        self.assertTrue(any("--messaging-list-sms" in call for call in calls))

    def test_modem_transport_creates_then_sends(self) -> None:
        calls = []
        def runner(arguments):
            calls.append(arguments)
            if arguments == ["mmcli", "-L"]: return "/Modem/0"
            if arguments == ["mmcli", "-m", "0", "--output-keyvalue"]: return "modem.generic.state: connected\nmodem.3gpp.registration-state: home"
            if any(arg.startswith("--messaging-create-sms=") for arg in arguments): return "created SMS: /org/freedesktop/ModemManager1/SMS/7"
            if "--send" in arguments: return "successfully sent"
            raise AssertionError(arguments)
        path = ModemMessagingTransport(runner).send("+1 312 555 0102", "Hello")
        self.assertEqual(path, "/org/freedesktop/ModemManager1/SMS/7")
        self.assertEqual(calls[-1], ["mmcli", "-s", "7", "--send"])

    def test_received_filters_sent_and_parses_incoming(self) -> None:
        def runner(arguments):
            if arguments == ["mmcli", "-L"]: return "/Modem/0"
            if arguments == ["mmcli", "-m", "0", "--output-keyvalue"]: return "modem.generic.state: registered\nmodem.3gpp.registration-state: home"
            if "--messaging-list-sms" in arguments: return "/SMS/2 (received)\n/SMS/3 (sent)"
            if arguments[2] == "2":
                return "sms.properties.state: received\nsms.content.pdu-type: deliver\nsms.content.number: +1 312 555 0102\nsms.content.text: Hello\nsms.content.timestamp: 2026-08-13T06:30:00Z"
            return "sms.properties.state: sent\nsms.content.pdu-type: submit\nsms.content.number: +13125550102\nsms.content.text: Outgoing"
        records = ModemMessagingTransport(runner).received()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0][0:2], ("+13125550102", "Hello"))
        self.assertEqual(
            records[0][2],
            incoming_transport_id("+13125550102", "Hello", records[0][3]),
        )

    def test_received_identity_does_not_depend_on_reusable_object_path(self) -> None:
        current_path = {"value": "2"}

        def runner(arguments):
            if arguments == ["mmcli", "-L"]:
                return "/Modem/0"
            if "--messaging-list-sms" in arguments:
                return f"/SMS/{current_path['value']} (received)"
            return (
                "sms.properties.state: received\n"
                "sms.content.pdu-type: deliver\n"
                "sms.content.number: +1 312 555 0102\n"
                "sms.content.text: Same message\n"
                "sms.content.timestamp: 2026-09-01T18:04:47Z"
            )

        transport = ModemMessagingTransport(runner)
        first = transport.received()[0]
        current_path["value"] = "0"
        second = transport.received()[0]
        self.assertEqual(first[2], second[2])

    def test_sent_modem_snapshot_reconciles_pending_row(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = MessageStore(Path(root) / "messages.db")
            pending = store.add(
                "+13125550102", "Delivered", direction="outgoing",
                state="sending", timestamp=20,
            )
            snapshot = (
                TransportMessage(
                    "+13125550102", "Delivered",
                    "/org/freedesktop/ModemManager1/SMS/1", 0,
                    "outgoing", "sent",
                ),
            )
            self.assertEqual(store.reconcile_outgoing(snapshot), 1)
            record = next(
                item for item in store.thread(pending.address)
                if item.uid == pending.uid
            )
            self.assertEqual(record.state, "sent")
            self.assertEqual(record.transport_id, f"mm-outgoing:v1:{pending.uid}")
            store.close()

    def test_legacy_object_path_is_migrated_before_path_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "messages.db"
            store = MessageStore(path)
            record = store.ingest(
                "+13125550102", "Old", transport_id=
                "/org/freedesktop/ModemManager1/SMS/2", timestamp=10,
            )
            self.assertIsNotNone(record)
            store.close()
            reopened = MessageStore(path)
            migrated = reopened.thread("+13125550102")[0]
            self.assertEqual(
                migrated.transport_id,
                incoming_transport_id("+13125550102", "Old", 10),
            )
            new = reopened.ingest(
                "+13125550102", "New",
                transport_id=incoming_transport_id(
                    "+13125550102", "New", 11
                ),
                timestamp=11,
            )
            self.assertIsNotNone(new)
            reopened.close()

    def test_modem_transport_accepts_roaming_registration(self) -> None:
        def runner(arguments):
            if arguments == ["mmcli", "-L"]:
                return "/Modem/0"
            if "--output-keyvalue" in arguments:
                return "modem.generic.state: enabled\nmodem.3gpp.registration-state: roaming"
            raise AssertionError(arguments)

        self.assertTrue(ModemMessagingTransport(runner).inspect().available)

    def test_connected_bearer_without_registration_is_not_sms_ready(self) -> None:
        def runner(arguments):
            if arguments == ["mmcli", "-L"]:
                return "/Modem/0"
            if "--output-keyvalue" in arguments:
                return "modem.generic.state: connected\nmodem.3gpp.registration-state: --"
            raise AssertionError(arguments)

        capability = ModemMessagingTransport(runner).inspect()
        self.assertFalse(capability.available)
        self.assertIn("cellular network", capability.reason)

    def test_default_sender_uses_private_dbus_path(self) -> None:
        transport = ModemMessagingTransport()
        self.assertTrue(transport._use_dbus_sender)
        self.assertFalse(ModemMessagingTransport(lambda _arguments: "")._use_dbus_sender)

    def test_incoming_revision_observes_an_external_import(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "messages.db"
            window_store = MessageStore(path)
            daemon_store = MessageStore(path)
            before = window_store.incoming_revision()
            daemon_store.ingest(
                "+13125550102", "Private body", transport_id="/org/freedesktop/ModemManager1/SMS/9"
            )
            self.assertNotEqual(window_store.incoming_revision(), before)
            daemon_store.close()
            window_store.close()

    def test_nanp_reply_merges_national_and_e164_threads(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "messages.db"
            store = MessageStore(path)
            store.add("3125550102", "Outgoing", direction="outgoing")
            store.ingest(
                "+13125550102", "Reply", transport_id="/org/freedesktop/ModemManager1/SMS/10"
            )
            threads = store.threads()
            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0].address, "+13125550102")
            self.assertEqual(len(store.thread("3125550102")), 2)
            store.close()

    def test_existing_nanp_threads_migrate_when_store_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "messages.db"
            store = MessageStore(path)
            store.add("3125550102", "National", direction="outgoing")
            store.close()
            connection = __import__("sqlite3").connect(path)
            connection.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                ("external", "+13125550102", "E164", 20, "incoming", "received", "/org/freedesktop/ModemManager1/SMS/11"),
            )
            connection.commit(); connection.close()
            reopened = MessageStore(path)
            self.assertEqual(len(reopened.threads()), 1)
            self.assertEqual(len(reopened.thread("+13125550102")), 2)
            reopened.close()

    def test_draft_survives_restart_is_searchable_and_does_not_reorder(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "messages.db"
            store = MessageStore(path)
            store.add("+13125550102", "Older", direction="incoming", timestamp=10)
            store.add("+13125550103", "Newer", direction="incoming", timestamp=20)
            store.set_draft("+13125550102", "Dinner options")
            self.assertEqual(store.threads()[0].address, "+13125550103")
            self.assertEqual(store.threads()[1].preview, "Draft: Dinner options")
            self.assertEqual(store.threads("dinner")[0].address, "+13125550102")
            store.close()
            reopened = MessageStore(path)
            self.assertEqual(reopened.draft("+13125550102"), "Dinner options")
            reopened.delete_draft("+13125550102")
            self.assertEqual(reopened.draft("+13125550102"), "")
            reopened.close()

    def test_local_message_delete_is_precise(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = MessageStore(Path(root) / "messages.db")
            first = store.add("+13125550102", "Keep", direction="incoming")
            second = store.add("+13125550102", "Delete", direction="outgoing")
            self.assertTrue(store.delete_message(second.uid))
            self.assertFalse(store.delete_message(second.uid))
            self.assertEqual(tuple(item.uid for item in store.thread(first.address)), (first.uid,))
            store.close()

    def test_modem_transport_accepts_roaming_registration(self) -> None:
        def runner(arguments):
            if arguments == ["mmcli", "-L"]:
                return "/Modem/0"
            if "--output-keyvalue" in arguments:
                return "modem.generic.state: enabled\nmodem.3gpp.registration-state: roaming"
            raise AssertionError(arguments)

        self.assertTrue(ModemMessagingTransport(runner).inspect().available)

    def test_default_sender_uses_private_dbus_path(self) -> None:
        transport = ModemMessagingTransport()
        self.assertTrue(transport._use_dbus_sender)
        self.assertFalse(ModemMessagingTransport(lambda _arguments: "")._use_dbus_sender)

    def test_incoming_revision_observes_an_external_import(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "messages.db"
            window_store = MessageStore(path)
            daemon_store = MessageStore(path)
            before = window_store.incoming_revision()
            daemon_store.ingest(
                "+13125550102", "Private body", transport_id="/org/freedesktop/ModemManager1/SMS/9"
            )
            self.assertNotEqual(window_store.incoming_revision(), before)
            daemon_store.close()
            window_store.close()

    def test_nanp_reply_merges_national_and_e164_threads(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "messages.db"
            store = MessageStore(path)
            store.add("3125550102", "Outgoing", direction="outgoing")
            store.ingest(
                "+13125550102", "Reply", transport_id="/org/freedesktop/ModemManager1/SMS/10"
            )
            threads = store.threads()
            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0].address, "+13125550102")
            self.assertEqual(len(store.thread("3125550102")), 2)
            store.close()

    def test_existing_nanp_threads_migrate_when_store_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "messages.db"
            store = MessageStore(path)
            store.add("3125550102", "National", direction="outgoing")
            store.close()
            connection = __import__("sqlite3").connect(path)
            connection.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                ("external", "+13125550102", "E164", 20, "incoming", "received", "/org/freedesktop/ModemManager1/SMS/11"),
            )
            connection.commit(); connection.close()
            reopened = MessageStore(path)
            self.assertEqual(len(reopened.threads()), 1)
            self.assertEqual(len(reopened.thread("+13125550102")), 2)
            reopened.close()


if __name__ == "__main__":
    unittest.main()
