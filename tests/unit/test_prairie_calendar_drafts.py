# SPDX-License-Identifier: Apache-2.0
"""Exercise real draft callbacks; mock only UI construction and EDS delivery."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from importlib.util import find_spec
from pathlib import Path
from types import MethodType, SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
if find_spec("prairie_apps") is None:
    sys.path.insert(0, str(ROOT / "src/prairie-core"))
from prairie_apps.calendar_data import CalendarAttendee, CalendarSource, Event
from prairie_apps.calendar_window import CalendarWindow


class CalendarHoldDraftTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 9, 23)
        self.window = SimpleNamespace(
            fixture=None, sources=(CalendarSource("personal", "Personal", "blue", ""),),
            draft=None, editing_event=None, duplicate_origin=None, draft_source="",
            real_invitees={}, invitees_changed=False, _saving=False,
            build_editor=Mock(), center=Mock(), toast=Mock(), _saved=Mock(), show_quick_entry=Mock())
        for name in ("edit_event", "duplicate", "hold", "save_draft", "discard_draft", "restore_draft"):
            setattr(self.window, name, MethodType(getattr(CalendarWindow, name), self.window))
        self.window.task = lambda work, done: work()
        self.save_patch = patch("prairie_apps.calendar_window.save_meeting", return_value="new-hold")
        self.save = self.save_patch.start()
        self.addCleanup(self.save_patch.stop)
        self.zone_patch = patch("prairie_apps.calendar_window.local_tzid", return_value="UTC")
        self.zone_patch.start()
        self.addCleanup(self.zone_patch.stop)

    def test_hold_after_duplicate_saves_only_the_new_timed_event(self):
        start = datetime(2026, 9, 23, tzinfo=timezone.utc)
        original = Event("old", "personal", "Trip", start, start + timedelta(days=3),
                         all_day=True, description="Private trip notes", url="https://example.test/call",
                         tzid="UTC", attendees=(CalendarAttendee("Alex", "alex@example.test"),))
        self.window.duplicate(original)
        self.assertIs(self.window.duplicate_origin, original)
        self.window.hold(self.day, 690, 810)
        self.window.save_draft()
        self.save.assert_called_once()
        saved = self.save.call_args.args[0]
        self.assertEqual(saved.summary, "Focus time")
        self.assertEqual((saved.start.hour, saved.start.minute), (11, 30))
        self.assertEqual(saved.end - saved.start, timedelta(hours=1))
        self.assertFalse(saved.all_day)
        self.assertEqual((saved.uid, saved.description, saved.url), ("", "", ""))
        self.assertIsNone(saved.attendees)
        self.assertIsNone(saved.changed_fields)

    def test_hold_clears_previous_quick_entry_text(self):
        self.window.draft_source = "Lunch with Alex tomorrow noon"
        self.window.hold(self.day, 690, 810)
        self.assertEqual(self.window.draft_source, "")
        self.assertEqual(self.window.draft.title, "Focus time")

    def test_hold_without_personal_calendar_uses_writable_source(self):
        self.window.sources = (CalendarSource("holidays", "Holidays", "green", "", writable=False),
                               CalendarSource("work", "Work", "blue", ""))
        self.window.hold(self.day, 690, 720)
        self.window.save_draft()
        saved = self.save.call_args.args[0]
        self.assertEqual(saved.source_uid, "work")
        self.assertEqual(saved.end - saved.start, timedelta(minutes=30))

    def test_hold_skips_a_read_only_personal_calendar(self):
        self.window.sources = (CalendarSource("personal", "Personal", "green", "", writable=False),
                               CalendarSource("work", "Work", "blue", ""))
        self.window.hold(self.day, 690, 750)
        self.window.save_draft()
        self.assertEqual(self.save.call_args.args[0].source_uid, "work")

    def test_hold_without_writable_calendar_keeps_existing_draft(self):
        self.window.hold(self.day, 690, 750)
        existing = self.window.draft
        self.window.build_editor.reset_mock()
        self.window.sources = (CalendarSource("holidays", "Holidays", "green", "", writable=False),)
        self.window.hold(self.day + timedelta(days=1), 900, 960)
        self.assertIs(self.window.draft, existing)
        self.window.build_editor.assert_not_called()
        self.window.toast.assert_called_once_with("No writable calendar", kind="warning")

    def test_discard_undo_restores_original_edit_and_invitees_after_another_draft(self):
        start = datetime(2026, 9, 23, 11, 30, tzinfo=timezone.utc)
        original = Event("existing", "personal", "Lunch", start, start + timedelta(hours=1),
                         tzid="UTC", description="Retain server notes")
        self.window.edit_event(original)
        self.window.draft = replace(self.window.draft, title="Changed lunch")
        attendee = CalendarAttendee("Alex", "alex@example.test")
        self.window.real_invitees = {attendee.email: attendee}
        self.window.invitees_changed = True
        self.window.draft_source = "Original edit text"
        self.window.discard_draft()
        undo = self.window.toast.call_args.kwargs["undo"]
        self.window.hold(self.day + timedelta(days=1), 900, 960)
        undo()
        self.window.save_draft()
        saved = self.save.call_args.args[0]
        self.assertEqual(saved.uid, original.uid)
        self.assertEqual(saved.summary, "Changed lunch")
        self.assertEqual(saved.description, original.description)
        self.assertEqual(saved.attendees, (attendee,))
        self.assertEqual(saved.changed_fields, frozenset(("summary", "attendees")))
        self.assertEqual(self.window.draft_source, "Original edit text")

    def test_discard_undo_restores_duplicate_template_after_another_draft(self):
        start = datetime(2026, 9, 23, tzinfo=timezone.utc)
        original = Event("existing", "personal", "Trip", start, start + timedelta(days=2),
                         all_day=True, tzid="UTC", description="Trip notes", url="https://example.test/trip")
        self.window.duplicate(original)
        self.window.discard_draft()
        undo = self.window.toast.call_args.kwargs["undo"]
        self.window.hold(self.day, 690, 750)
        undo()
        self.window.save_draft()
        saved = self.save.call_args.args[0]
        self.assertEqual(saved.uid, "")
        self.assertTrue(saved.all_day)
        self.assertEqual(saved.description, original.description)
        self.assertEqual(saved.url, original.url)

    def test_new_draft_undo_cannot_overwrite_a_later_edited_event(self):
        self.window.hold(self.day, 690, 750)
        self.window.discard_draft()
        undo = self.window.toast.call_args.kwargs["undo"]
        start = datetime(2026, 9, 23, 15, tzinfo=timezone.utc)
        original = Event("unrelated", "personal", "Meeting", start, start + timedelta(hours=1), tzid="UTC")
        self.window.edit_event(original)
        undo()
        self.window.save_draft()
        saved = self.save.call_args.args[0]
        self.assertEqual(saved.uid, "")
        self.assertEqual(saved.summary, "Focus time")
        self.assertIsNone(saved.changed_fields)


if __name__ == "__main__":
    unittest.main()
