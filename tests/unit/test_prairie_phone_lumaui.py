# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import importlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/prairie-core"))
from prairie_apps.phone_fixture import FixtureSource, PhonePerson, dial_display, matching_person, source_from_environment, visible_people
from prairie_apps.phone_data import BlockList, LiveSource, PreviewContactsSource, back_up_call_log, moment, read_rows
from prairie_apps.phone_control import VoiceController
from prairie_apps.phone_backend import CallPhase, CallSession

FIXTURE = ROOT / "tests/fixtures/phone-v70.json"


class PhoneFixtureTests(unittest.TestCase):
    def setUp(self):
        self.source = FixtureSource(FIXTURE)

    def test_all_v70_records_and_photo_assets_exist(self):
        self.assertEqual(len(self.source.people), 8)
        self.assertEqual(len(self.source.calls), 7)
        self.assertEqual(len(self.source.voicemails), 2)
        self.assertEqual(self.source.favourites, ("PR", "NF", "MO", "SK"))
        self.assertEqual(self.source.calls[2].people, ("PR", "NF", "SK"))
        self.assertEqual(self.source.calls[4].number, "+1 (415) 555-0199")
        self.assertEqual(self.source.initial["tab"], "pad")
        for person in self.source.people:
            if person.photo:
                self.assertGreater(len(self.source.photo(person)), 0)

    def test_fixture_is_explicit_and_bad_fixture_never_falls_back(self):
        self.assertIsNone(source_from_environment({}))
        self.assertIsInstance(source_from_environment({"LUMA_PHONE_FIXTURE": str(FIXTURE)}), FixtureSource)
        with self.assertRaises(FileNotFoundError):
            source_from_environment({"LUMA_PHONE_FIXTURE": "/nonexistent/phone-fixture.json"})
        with self.assertRaises(FrozenInstanceError):
            self.source.people[0].phone = "changed"

    def test_assets_cannot_read_outside_fixture(self):
        with self.assertRaises(ValueError):
            self.source._asset("../../../outside")
        with self.assertRaises(ValueError):
            self.source._asset("/etc/passwd")

    def test_number_and_symbol_matching(self):
        self.assertIsNone(matching_person(self.source.people, ""))
        self.assertIsNone(matching_person(self.source.people, "*#*"))
        self.assertIsNone(matching_person(self.source.people, "51"))
        self.assertEqual(matching_person(self.source.people, "510").uid, "PR")
        self.assertEqual(matching_person(self.source.people, "5550132").uid, "PR")
        self.assertEqual(dial_display("5105550132"), "(510) 555-0132")
        self.assertEqual(dial_display("*#06#"), "*#06#")
        self.assertEqual(dial_display("+15105550132"), "151-055-50132")

    def test_number_search_and_duplicate_numeric_names(self):
        people = (PhonePerson("number", "5105550132", "+1 510 555 0132"), *self.source.people)
        self.assertEqual([p.uid for p in visible_people(people, "555-0132")], ["number", "PR"])
        self.assertEqual([p.uid for p in visible_people(people, "PRIYA")], ["PR"])
        self.assertEqual(visible_people(people, "no such person"), ())

    def test_together_omits_group_calls_and_uses_real_fixture_text(self):
        items = self.source.together("PR")
        self.assertEqual(items[0].title, "Video call")
        self.assertEqual(items[0].duration, "12 min")
        self.assertEqual(items[1].title, "Priya texted: “Will do 👍”")
        self.assertEqual(items[2].title, "Email: Launch walkthrough deck")
        self.assertEqual(items[3].title, "Email: Venue confirmed")
        self.assertFalse(any(item.duration == "42 min" for item in items))
        dad = self.source.together("MO")
        self.assertTrue(dad[0].missed)
        self.assertEqual(dad[0].icon, "phone-missed")
        self.assertTrue(dad[1].title.endswith("…”"))

    def test_preview_contacts_uses_address_book_without_replacing_sample_calls(self):
        photo = b"real-contact-photo"
        records = (SimpleNamespace(uid="PR", name="Real Priya", phone="123", email="priya@example.org",
                                   photo=photo),
                   SimpleNamespace(uid="no-phone", name="A contact without a phone", phone="", email="", photo=b""))
        with patch("prairie_apps.eds_backend.load_contacts", return_value=records):
            preview = PreviewContactsSource.load(self.source)
        self.assertIs(preview.calls, self.source.calls)
        self.assertIs(preview.voicemails, self.source.voicemails)
        self.assertEqual([person.name for person in preview.contacts],
                         ["Real Priya", "A contact without a phone"])
        self.assertIn("PR", {person.uid for person in preview.people})
        self.assertIn("eds:PR", {person.uid for person in preview.people})
        self.assertEqual(preview.photo(preview.contacts[0]), photo)
        self.assertEqual(preview.together("eds:PR"), ())
        self.assertEqual(preview.together("PR"), self.source.together("PR"))


class PhoneReadOnlyDataTests(unittest.TestCase):
    def test_call_log_backup_includes_wal_and_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calls.db"
            db = sqlite3.connect(path)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE calls(value TEXT)")
                db.execute("INSERT INTO calls VALUES('original')")
                db.commit()
                back_up_call_log(path)
                backup = path.with_suffix(".db.bak")
                self.assertEqual(read_rows(backup, "SELECT value FROM calls")[0][0], "original")
                first = backup.read_bytes()
                db.execute("INSERT INTO calls VALUES('later')")
                db.commit()
                back_up_call_log(path)
                self.assertEqual(backup.read_bytes(), first)
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
                self.assertEqual(list(Path(tmp).glob(".calls-backup-*")), [])
            finally:
                db.close()

    def test_call_log_backup_missing_and_invalid_sources_never_create_a_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing/calls.db"
            back_up_call_log(path)
            self.assertFalse(path.parent.exists())
            path = Path(tmp) / "calls.db"
            path.write_bytes(b"not a database")
            with self.assertRaises(sqlite3.DatabaseError):
                back_up_call_log(path)
            self.assertEqual(path.read_bytes(), b"not a database")
            self.assertFalse(path.with_suffix(".db.bak").exists())
            self.assertEqual(list(Path(tmp).glob(".calls-backup-*")), [])

    def test_missing_store_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing" / "calls.db"
            self.assertEqual(read_rows(path, "SELECT * FROM calls"), ())
            self.assertFalse(path.parent.exists())

    def test_store_is_read_only_and_not_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calls.db"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE calls(value TEXT)")
            db.execute("INSERT INTO calls VALUES('kept')")
            db.commit()
            db.close()
            before = path.read_bytes()
            self.assertEqual(read_rows(path, "SELECT value FROM calls")[0][0], "kept")
            with self.assertRaises(sqlite3.OperationalError):
                read_rows(path, "DELETE FROM calls")
            self.assertEqual(path.read_bytes(), before)

    def test_live_reads_existing_local_and_cloud_calls_without_creating_stores(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / "prairie/phone/calls.db"
            path.parent.mkdir(parents=True)
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE calls(uid TEXT PRIMARY KEY,address TEXT,direction TEXT,started INTEGER,duration INTEGER,outcome TEXT)")
            db.execute("INSERT INTO calls VALUES('local', '5105550132', 'outgoing', 100, 60, 'completed')")
            db.commit()
            db.close()
            before = path.read_bytes()
            contact = SimpleNamespace(uid="PR", name="Priya", phone="5105550132", email="", photo=b"")
            cloud = [{"id": "other", "number": "4155550199", "direction": "missed", "started_at": 200, "duration_s": 0}]
            modules = {"prairie_apps.eds_backend": SimpleNamespace(load_contacts=lambda: (contact,))}
            cloud_module = importlib.import_module("prairie_apps.connect_messages")
            with patch.dict(sys.modules, modules), patch.object(cloud_module, "cloud_calls", return_value=cloud):
                source = LiveSource(base).load()
            self.assertEqual([call.uid for call in source.calls], ["cloud:other", "local"])
            self.assertEqual(source.calls[1].people, ("PR",))
            self.assertEqual(source.calls[0].direction, "missed")
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((path.parent / "favourites.json").exists())
            self.assertFalse((base / "prairie/messages").exists())

    def test_dates_at_day_boundaries(self):
        now = datetime(2026, 9, 26, 0, 1)
        self.assertEqual(moment(int(datetime(2026, 9, 25, 23, 59).timestamp()), now=now), "Yesterday, 11:59 PM")
        self.assertEqual(moment(int(datetime(2026, 9, 26, 0, 0).timestamp()), now=now), "Today, 12:00 AM")


class PhoneNativeVoiceTests(unittest.TestCase):
    def setUp(self):
        self.states, self.errors = [], []
        self.voice = VoiceController(dispatch=lambda cb: cb(), on_state=self.states.append,
                                     on_capability=lambda value: None, on_error=self.errors.append)

    def tearDown(self):
        self.voice.close()

    def test_constructor_does_not_start_services_or_open_stores(self):
        self.assertIsNone(self.voice.monitor)
        self.assertIsNone(self.voice.provider)
        self.assertIsNone(self.voice.transport)
        self.assertEqual(self.voice.session.phase, CallPhase.IDLE)

    def test_explicit_application_provider_is_started_and_published(self):
        published, listeners = [], []
        provider = SimpleNamespace(start=listeners.append, close=lambda: None)
        self.voice.on_provider = published.append
        self.voice._task = lambda operation, then, **kwargs: then(operation())
        feedback = SimpleNamespace(Ringer=lambda _: SimpleNamespace(stop=lambda: None))
        with patch.dict(sys.modules, {"prairie_apps.phone_feedback": feedback}), \
                patch("prairie_apps.phone_control.importlib.util.find_spec",
                      side_effect=AssertionError("injected provider must not be rediscovered")):
            self.voice.start(provider)
        self.assertEqual(published, [provider])
        self.assertIs(self.voice.provider, provider)
        self.assertEqual(listeners, [self.voice._paired_changed])

    def test_explicit_no_provider_uses_modem_inspection(self):
        from prairie_apps.phone_backend import PhoneCapability, NO_MODEM_REASON
        capability = PhoneCapability(False, NO_MODEM_REASON, no_modem=True)
        reported, published = [], []
        self.voice.on_capability = reported.append
        self.voice.on_provider = published.append
        self.voice._task = lambda operation, then, **kwargs: then(operation())
        with patch("prairie_apps.phone_control.inspect_phone_capability", return_value=capability), \
                patch("prairie_apps.phone_control.importlib.util.find_spec",
                      side_effect=AssertionError("explicit no-phone choice must be preserved")):
            self.voice.start(None)
        self.assertEqual(reported, [capability])
        self.assertEqual(published, [None])

    def test_carrier_loss_preserves_paired_provider_and_reports_voice_unavailable(self):
        reported = []
        provider = SimpleNamespace(control_authorized=lambda: True, calls=lambda: (), close=lambda: None)
        self.voice.provider = provider
        self.voice.on_capability = reported.append
        self.voice._paired_changed({"voice_available": False}, True)
        self.assertIs(self.voice.provider, provider)
        self.assertFalse(reported[-1].available)
        self.assertEqual(reported[-1].reason, "Your phone cannot make calls right now.")
        self.voice._paired_changed({"voice_available": True}, True)
        self.assertTrue(reported[-1].available)

    def test_answer_hands_audio_over_once_in_either_reply_event_order(self):
        from prairie_apps.phone_backend import NativeCall
        for event_first in (False, True):
            with self.subTest(event_first=event_first):
                tasks, audio, answers = [], [], []
                active = NativeCall("answered", "5550100", "incoming", CallPhase.ACTIVE,
                                    started_at=100, answered_at=110)
                provider = SimpleNamespace(control_authorized=lambda: True, calls=lambda: (active,),
                    accept=answers.append, start_audio=audio.append, invalidate=lambda: None, close=lambda: None)
                self.voice.provider = self.voice.transport = provider
                self.voice.ringer = SimpleNamespace(start=lambda: None, stop=lambda: None)
                self.voice.session = CallSession.from_native(NativeCall(
                    "answered", "5550100", "incoming", CallPhase.INCOMING, started_at=100))
                self.voice._task = lambda operation, then, **kwargs: tasks.append((operation, then))
                self.voice.control("accept")
                operation, confirmed = tasks.pop(0)
                operation()
                if event_first:
                    self.voice._paired_changed({"voice_available": True}, True)
                confirmed(None)
                if not event_first:
                    self.voice._paired_changed({"voice_available": True}, True)
                self.assertTrue(self.voice.audio_pending)
                self.assertEqual(answers, ["answered"])
                self.assertEqual(len(tasks), 1)
                self.voice._paired_changed({"voice_available": True}, True)
                self.assertEqual(len(tasks), 1)
                operation, confirmed = tasks.pop(0)
                confirmed(operation())
                self.assertEqual(audio, ["answered"])
                self.assertFalse(self.voice.audio_pending)
                self.voice._paired_changed({"voice_available": True}, True)
                self.assertEqual(tasks, [])

    def test_blocked_incoming_call_is_declined_without_presenting_it(self):
        operations = []
        self.voice.is_blocked = lambda address: address == "5550100"
        self.voice._task = lambda operation, then, **kwargs: then(operation())
        self.voice.control = operations.append
        self.voice._native_added("blocked", "incoming", "5550100")
        self.assertEqual(operations, ["decline"])
        self.assertEqual(self.states, [])
        self.voice._native_changed("blocked", "ringing-in", "")
        self.assertEqual(operations, ["decline"])
        self.assertEqual(self.states, [])

    def test_late_block_policy_reply_cannot_decline_a_different_call(self):
        replies, operations = [], []
        self.voice.is_blocked = lambda _: True
        self.voice._task = lambda operation, then, **kwargs: replies.append(then)
        self.voice.control = operations.append
        self.voice._native_added("old", "incoming", "5550100")
        self.voice.session = CallSession.preparing("5550101").with_call_id("new")
        replies[0](True)
        self.assertEqual(operations, [])

    def test_failed_block_policy_read_keeps_call_available(self):
        failure = []
        self.voice.is_blocked = lambda _: False
        self.voice._task = lambda operation, then, **kwargs: failure.append(kwargs["on_failure"])
        self.voice._native_added("available", "incoming", "5550100")
        failure[0](ValueError("Invalid policy"))
        self.assertEqual(self.states[-1].call_id, "available")
        self.assertEqual(self.states[-1].phase, CallPhase.INCOMING)

    def test_terminal_rejection_does_not_keep_hiding_later_calls(self):
        operations = []
        self.voice.is_blocked = lambda _: True
        self.voice._task = lambda operation, then, **kwargs: then(operation())
        self.voice.control = operations.append
        self.voice._native_added("blocked", "incoming", "5550100")
        with patch.object(self.voice, "_record"):
            self.voice._native_ended("blocked")
        self.assertIsNone(self.voice._rejected)
        self.assertEqual(self.states, [])
        self.voice.is_blocked = lambda _: False
        self.voice._native_added("later", "incoming", "5550100")
        self.assertEqual(self.states[-1].call_id, "later")

    def test_decline_failure_restores_incoming_call_controls(self):
        self.voice.is_blocked = lambda _: True
        def run(operation, then, **kwargs):
            if kwargs.get("on_failure") and self.voice.pending:
                kwargs["on_failure"](PermissionError("Not authorized"))
                self.voice.on_state(self.voice.session)
            else:
                then(operation())
        self.voice._task = run
        self.voice._native_added("available", "incoming", "5550100")
        self.assertFalse(self.voice.pending)
        self.assertIsNone(self.voice._rejected)
        self.assertEqual(self.states[-1].phase, CallPhase.INCOMING)

    def test_native_calls_and_timer_need_authoritative_active_event(self):
        self.voice._native_added("native-1", "incoming", "+15105550132")
        self.assertEqual(self.voice.session.phase, CallPhase.INCOMING)
        self.assertEqual(self.voice.session.elapsed(), 0)
        self.voice._native_changed("another-call", "active", "")
        self.assertEqual(self.voice.session.phase, CallPhase.INCOMING)
        self.voice._native_changed("native-1", "active", "")
        self.assertEqual(self.voice.session.phase, CallPhase.ACTIVE)
        self.assertGreater(self.voice.session.connected_at, 0)

    def test_late_dial_reply_preserves_native_active_snapshot(self):
        replies = []
        self.voice._task = lambda operation, then, **kwargs: replies.append(then)
        self.voice.dial("+15105550132")
        self.assertEqual(self.voice.session.phase, CallPhase.PREPARING)
        self.voice._native_added("native-1", "outgoing", "+15105550132")
        self.voice._native_changed("native-1", "active", "")
        replies[0]((object(), "native-1"))
        self.assertEqual(self.voice.session.phase, CallPhase.ACTIVE)
        self.assertFalse(self.voice.pending)

    def test_invalid_address_never_leaves_controller_pending(self):
        self.voice.dial("letters")
        self.assertEqual(self.voice.session.phase, CallPhase.IDLE)
        self.assertFalse(self.voice.pending)
        self.assertTrue(self.errors)

    def test_duplicate_added_event_cannot_reset_connected_state(self):
        self.voice._native_added("native-1", "outgoing", "5105550132")
        self.voice._native_changed("native-1", "active", "")
        self.voice._native_added("native-1", "outgoing", "5105550132")
        self.assertEqual(self.voice.session.phase, CallPhase.ACTIVE)

    def test_failed_dial_releases_pending_and_never_shows_connected(self):
        replies = []
        self.voice._task = lambda operation, then, **kwargs: replies.append(kwargs["on_failure"])
        self.voice.dial("+15105550132")
        replies[0](RuntimeError("Unavailable"))
        self.assertEqual(self.voice.session.phase, CallPhase.FAILED)
        self.assertFalse(self.voice.pending)
        self.assertEqual(self.voice.session.connected_at, 0)

    def test_restore_reply_cannot_replace_a_newer_call(self):
        from prairie_apps.phone_backend import NativeCall
        self.voice._native_added("new", "incoming", "5105550132")
        self.voice._restore((object(), (NativeCall("old", "555", "outgoing", CallPhase.ACTIVE),), 0))
        self.assertEqual(self.voice.session.call_id, "new")

    def test_paired_snapshot_cannot_be_replaced_by_a_stale_dial_reply(self):
        from prairie_apps.phone_backend import NativeCall
        replies = []
        self.voice._task = lambda operation, then, **kwargs: replies.append(then)
        self.voice.dial("5105550132")
        self.voice.provider = SimpleNamespace(control_authorized=lambda: True,
            calls=lambda: (NativeCall("new-call", "5550100", "incoming", CallPhase.ACTIVE, answered_at=100),),
            close=lambda: None)
        self.voice.ringer = SimpleNamespace(stop=lambda: None, start=lambda: None)
        self.voice._paired_changed({"voice_available": True}, True)
        replies[0]((object(), "old-call"))
        self.assertEqual(self.voice.session.call_id, "new-call")
        self.assertEqual(self.voice.session.phase, CallPhase.ACTIVE)

    def test_empty_paired_snapshot_keeps_dial_preparing_until_native_call_arrives(self):
        from prairie_apps.phone_backend import NativeCall
        for acknowledgement_first in (False, True):
            with self.subTest(acknowledgement_first=acknowledgement_first):
                tasks, calls, dialed = [], [], []
                provider = SimpleNamespace(control_authorized=lambda: True, calls=lambda: tuple(calls),
                    dial=lambda address: dialed.append(address) or "",
                    start_audio=lambda _: self.fail("dial must not choose computer audio"), close=lambda: None)
                self.voice.provider = provider
                self.voice.ringer = SimpleNamespace(start=lambda: None, stop=lambda: None)
                self.voice.session = CallSession()
                self.voice.pending = False
                self.voice._task = lambda operation, then, **kwargs: tasks.append((operation, then))
                self.voice.dial("5550100")
                operation, confirmed = tasks.pop(0)
                result = operation()
                if acknowledgement_first:
                    confirmed(result)
                self.voice._paired_changed({"voice_available": True}, True)
                self.assertEqual(self.voice.session.phase, CallPhase.PREPARING)
                self.assertEqual(self.voice.pending, not acknowledgement_first)
                self.assertEqual(self.voice.session.elapsed(), 0)
                calls.append(NativeCall("outgoing", "5550100", "outgoing", CallPhase.ACTIVE,
                                        started_at=100, answered_at=110))
                self.voice._paired_changed({"voice_available": True}, True)
                if not acknowledgement_first:
                    confirmed(result)
                self.assertEqual(self.voice.session.call_id, "outgoing")
                self.assertEqual(self.voice.session.phase, CallPhase.ACTIVE)
                self.assertFalse(self.voice.pending)
                self.assertEqual(dialed, ["5550100"])
                self.voice._paired_changed({"voice_available": True}, True)
                self.assertEqual(tasks, [])

    def test_observed_paired_call_still_ends_when_it_disappears(self):
        from prairie_apps.phone_backend import NativeCall
        calls = [NativeCall("observed", "5550100", "outgoing", CallPhase.ACTIVE,
                            started_at=100, answered_at=110)]
        self.voice.provider = SimpleNamespace(control_authorized=lambda: True,
                                               calls=lambda: tuple(calls), close=lambda: None)
        self.voice.ringer = SimpleNamespace(start=lambda: None, stop=lambda: None)
        self.voice._paired_changed({"voice_available": True}, True)
        calls.clear()
        with patch.object(self.voice, "_record") as record:
            self.voice._paired_changed({"voice_available": True}, True)
            self.voice._paired_changed({"voice_available": True}, True)
        record.assert_called_once_with()
        self.assertEqual(self.voice.session.phase, CallPhase.ENDED)
        self.assertFalse(self.voice.pending)

    def test_ended_call_cannot_be_resurrected_by_dial_reply(self):
        replies = []
        self.voice._task = lambda operation, then, **kwargs: replies.append(then)
        self.voice.dial("5105550132")
        self.voice._native_added("native-1", "outgoing", "5105550132")
        self.voice._native_ended("native-1")
        replies[0]((object(), "native-1"))
        self.assertEqual(self.voice.session.phase, CallPhase.ENDED)

    def test_queued_dtmf_keeps_selected_provider_call_and_tone(self):
        operations, sent = [], []
        self.voice._task = lambda operation, then, **kwargs: operations.append(operation)
        original = SimpleNamespace(send_dtmf=lambda call_id, tone: sent.append((call_id, tone)), close=lambda: None)
        self.voice.provider = original
        self.voice.transport = SimpleNamespace(send_dtmf=lambda *_: self.fail("unselected transport used"))
        self.voice._native_added("old", "outgoing", "5550100")
        self.voice._native_changed("old", "active", "")
        self.voice.dtmf("5")
        self.voice.provider = None
        self.voice.session = CallSession.preparing("5550101").with_call_id("new")
        with patch("prairie_apps.phone_control.ImsVoiceTransport", side_effect=AssertionError("no modem fallback")):
            operations[0]()
        self.assertEqual(sent, [("old", "5")])

    def test_unsupported_selected_provider_reports_clear_tone_failure_without_modem_fallback(self):
        operations = []
        self.voice._task = lambda operation, then, **kwargs: operations.append(operation)
        self.voice.provider = SimpleNamespace(close=lambda: None)
        self.voice.transport = SimpleNamespace(send_dtmf=lambda *_: self.fail("unselected transport used"))
        self.voice._native_added("paired", "outgoing", "5550100")
        self.voice._native_changed("paired", "active", "")
        self.voice.dtmf("#")
        with patch("prairie_apps.phone_control.ImsVoiceTransport", side_effect=AssertionError("no modem fallback")), \
                self.assertRaisesRegex(RuntimeError, "This phone connection cannot send keypad tones"):
            operations[0]()
        self.assertEqual(self.voice.session.phase, CallPhase.ACTIVE)

    def test_modem_dtmf_dispatches_original_call_and_tone(self):
        operations, sent = [], []
        self.voice._task = lambda operation, then, **kwargs: operations.append(operation)
        self.voice._native_added("modem", "outgoing", "5550100")
        self.voice._native_changed("modem", "active", "")
        self.voice.dtmf("*")
        transport = SimpleNamespace(send_dtmf=lambda call_id, tone: sent.append((call_id, tone)))
        with patch("prairie_apps.phone_control.ImsVoiceTransport", return_value=transport) as make_transport:
            operations[0]()
        make_transport.assert_called_once_with()
        self.assertEqual(sent, [("modem", "*")])

    def test_mute_changes_only_after_success(self):
        callbacks = []
        confirmed = []
        self.voice._task = lambda operation, then, **kwargs: callbacks.append(then)
        self.voice.mute(True, confirmed=confirmed.append)
        self.assertEqual(confirmed, [])
        callbacks[0](None)
        self.assertEqual(confirmed, [True])

    def test_queued_mute_keeps_original_provider_without_modem_fallback(self):
        operations, muted = [], []
        self.voice._task = lambda operation, then, **kwargs: operations.append(operation)
        original = SimpleNamespace(mute_audio=muted.append, close=lambda: None)
        self.voice.provider = original
        self.voice.mute(True)
        self.voice.provider = None
        with patch("prairie_apps.phone_control.set_audio_input_muted",
                   side_effect=AssertionError("unselected modem audio used")):
            operations[0]()
        self.assertEqual(muted, [True])

    def test_queued_modem_mute_does_not_switch_to_a_new_provider(self):
        operations = []
        self.voice._task = lambda operation, then, **kwargs: operations.append(operation)
        self.voice.mute(False)
        self.voice.provider = SimpleNamespace(
            mute_audio=lambda _: self.fail("new provider used"), close=lambda: None)
        with patch("prairie_apps.phone_control.set_audio_input_muted") as muted:
            operations[0]()
        muted.assert_called_once_with(False)

    def test_queued_native_events_do_not_publish_after_close(self):
        self.voice._native_added("closed-call", "outgoing", "5550100")
        self.voice.close()
        previous = self.voice.session
        self.states.clear()
        self.voice._native_changed("closed-call", "active", "")
        self.voice._native_ended("closed-call")
        self.voice._native_added("new-call", "incoming", "5550101")
        self.assertEqual(self.voice.session, previous)
        self.assertEqual(self.states, [])

    def test_queued_paired_event_does_not_touch_closed_provider(self):
        provider = SimpleNamespace(
            close=lambda: None,
            control_authorized=lambda: self.fail("closed provider queried"))
        self.voice.provider = provider
        self.voice.close()
        previous = self.voice.session
        self.voice._paired_changed({"voice_available": True}, True)
        self.assertEqual(self.voice.session, previous)
        self.assertEqual(self.states, [])

    def test_worker_failure_reports_error_only_on_ui_dispatch(self):
        dispatched, ready, confirmed = [], Event(), []
        def dispatch(callback):
            dispatched.append(callback)
            ready.set()
        self.voice.dispatch = dispatch
        def fail():
            raise PermissionError("Call control authorization expired")
        self.voice._task(fail, confirmed.append)
        self.assertTrue(ready.wait(5), "worker must queue its result")
        self.assertEqual(self.errors, [])
        self.assertEqual(confirmed, [])
        dispatched.pop(0)()
        self.assertEqual(self.errors, ["Call control authorization expired"])
        self.assertEqual(confirmed, [])
        self.assertEqual(self.states[-1].phase, CallPhase.IDLE)

    def test_close_cancels_queued_work_and_discards_running_worker_reply(self):
        entered, release, completed = Event(), Event(), Event()
        dispatched, operations, confirmed = [], [], []
        def dispatch(callback):
            dispatched.append(callback)
            completed.set()
        self.voice.dispatch = dispatch
        def running():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test worker was not released")
            return "completed"
        try:
            self.voice._task(running, confirmed.append)
            self.assertTrue(entered.wait(5), "first operation must be running")
            self.voice._task(lambda: operations.append("queued"), confirmed.append)
            self.voice.close()
            release.set()
            self.voice.executor.shutdown(wait=True)
            self.assertTrue(completed.is_set(), "worker must dispatch completion")
            for callback in dispatched:
                callback()
            self.assertEqual(operations, [])
            self.assertEqual(confirmed, [])
            self.assertEqual(self.errors, [])
            self.assertEqual(self.states, [])
        finally:
            release.set()

    def test_queued_audio_keeps_original_call_and_provider(self):
        operations, routed = [], []
        self.voice._task = lambda operation, then, **kwargs: operations.append(operation)
        original = SimpleNamespace(start_audio=lambda call_id: routed.append(call_id))
        self.voice.provider = original
        self.voice.session = CallSession().preparing("5105550132").with_call_id("old")
        self.voice.audio("speaker")
        self.voice.session = CallSession().preparing("4155550199").with_call_id("new")
        self.voice.provider = None
        operations[0]()
        self.assertEqual(routed, ["old"])

    def test_queued_hangup_keeps_original_transport(self):
        operations, ended = [], []
        self.voice._task = lambda operation, then, **kwargs: operations.append(operation)
        self.voice.transport = SimpleNamespace(hangup=lambda call_id: ended.append(call_id))
        self.voice.session = CallSession().preparing("5105550132").with_call_id("old")
        self.voice.control("hangup")
        self.voice.transport = SimpleNamespace(hangup=lambda _: self.fail("new transport used"))
        operations[0]()
        self.assertEqual(ended, ["old"])

    def test_repeated_delete_records_once_and_never_writes_unknown_call(self):
        self.voice._task = lambda operation, then, **kwargs: None
        with patch("prairie_apps.phone_control.CallStore", side_effect=AssertionError("no disk write in test")):
            self.voice._native_ended("unknown")
            self.assertEqual(self.voice._recorded, set())
            self.voice._native_added("native-1", "incoming", "+15105550132")
            self.voice._native_ended("native-1")
            self.voice._native_ended("native-1")
            self.assertEqual(self.voice._recorded, {"native-1"})

    def test_failed_call_log_backup_prevents_opening_the_writer(self):
        operations = []
        self.voice._task = lambda operation, then, **kwargs: operations.append(operation)
        self.voice._native_added("native-1", "incoming", "5550100")
        self.voice._native_ended("native-1")
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("os.environ", {"XDG_DATA_HOME": directory}), \
                patch("prairie_apps.phone_control.back_up_call_log", side_effect=OSError("backup failed")) as backup, \
                patch("prairie_apps.phone_control.CallStore") as writer:
            with self.assertRaisesRegex(OSError, "backup failed"):
                operations[0]()
            backup.assert_called_once_with(Path(directory) / "prairie/phone/calls.db")
            writer.assert_not_called()


class PhoneBlockListTests(unittest.TestCase):
    def test_read_creates_nothing_and_edit_preserves_fields_with_one_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phone/blocked.json"
            store = BlockList(path)
            self.assertFalse(store.contains("+15105550132"))
            self.assertFalse(path.parent.exists())
            store.set_blocked("+1 (510) 555-0132", True)
            document = json.loads(path.read_text())
            document["future"] = {"keep": True}
            path.write_text(json.dumps(document))
            original = path.read_bytes()
            store.set_blocked("6285550120", True)
            store.set_blocked("5105550999", True)
            self.assertEqual(path.with_suffix(".json.bak").read_bytes(), original)
            self.assertEqual(json.loads(path.read_text())["future"], {"keep": True})
            store.set_blocked("+15105550132", False)
            self.assertFalse(store.contains("+15105550132"))
            self.assertTrue(store.contains("6285550120"))
            self.assertTrue(store.contains("5105550999"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_malformed_existing_store_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blocked.json"
            path.write_text('{"numbers": "wrong"}')
            original = path.read_bytes()
            with self.assertRaises(ValueError):
                BlockList(path).set_blocked("5550100", True)
            self.assertEqual(path.read_bytes(), original)

    def test_failed_backup_leaves_no_partial_file_and_retry_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blocked.json"
            original = b'{"numbers":["5550100"],"keep":{"old":true}}'
            path.write_bytes(original)
            with patch("prairie_apps.phone_data.os.fsync", side_effect=OSError("disk failure")):
                with self.assertRaisesRegex(OSError, "disk failure"):
                    BlockList(path).set_blocked("5550101", True)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(path.with_suffix(".json.bak").exists())
            self.assertEqual(list(Path(directory).glob(".blocked-backup-*")), [])
            BlockList(path).set_blocked("5550101", True)
            self.assertEqual(path.with_suffix(".json.bak").read_bytes(), original)
            self.assertEqual(path.with_suffix(".json.bak").stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["keep"], {"old": True})
            self.assertTrue(BlockList(path).contains("5550101"))

    def test_invalid_or_symlink_backup_prevents_editing_the_existing_store(self):
        for kind in ("partial", "directory", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "blocked.json"
                original = b'{"numbers":["5550100"]}'
                path.write_bytes(original)
                backup = path.with_suffix(".json.bak")
                if kind == "partial":
                    backup.write_bytes(b'{"numbers":[')
                elif kind == "directory":
                    backup.mkdir()
                else:
                    target = Path(directory) / "unrelated.json"
                    target.write_bytes(original)
                    backup.symlink_to(target)
                with self.assertRaises(ValueError):
                    BlockList(path).set_blocked("5550101", True)
                self.assertEqual(path.read_bytes(), original)
                if kind == "symlink":
                    self.assertEqual(target.read_bytes(), original)

    def test_separate_writers_do_not_lose_numbers(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blocked.json"
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda number: BlockList(path).set_blocked(number, True),
                              ("5550100", "5550101", "5550102", "5550103")))
            self.assertEqual(set(json.loads(path.read_text())["numbers"]),
                             {"5550100", "5550101", "5550102", "5550103"})


if __name__ == "__main__":
    unittest.main()
