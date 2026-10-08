# SPDX-License-Identifier: Apache-2.0
"""Delete and its actual Undo callback, with real iCalendar and a fake EDS client."""
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
from pathlib import Path
from types import MethodType, SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
if find_spec("prairie_apps") is None:
    sys.path.insert(0, str(ROOT / "src/prairie-core"))
from prairie_apps import calendar_backend as backend
from prairie_apps.calendar_data import Event
from prairie_apps.calendar_window import CalendarWindow

ICS = """BEGIN:VEVENT
UID:series
DTSTART:20260923T120000Z
DTEND:20260923T130000Z
SUMMARY:Original
DESCRIPTION:Original notes
RRULE:FREQ=WEEKLY;COUNT=5
X-PRIVATE:preserve
END:VEVENT
"""


class CalendarDeleteUndoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _data, cls.cal, cls.ical = backend._modules()

    def setUp(self):
        self.before = self.ical.Component.new_from_string(ICS)
        self.after = self.ical.Component.new_from_string(
            ICS.replace("COUNT=5", "UNTIL=20260930T115959Z"))
        self.current = [self.after.clone()]
        self.client = Mock()
        self.client.is_readonly.return_value = False
        self.client.get_object_list_sync.side_effect = lambda *_: (True, self.current)
        self.client.create_object_sync.side_effect = self.create
        self.client.modify_object_sync.return_value = True
        self.window = SimpleNamespace(fixture=None, selected_event=None, reload=Mock(), toast=Mock())
        for name in ("delete_with_backup", "_deleted", "restore_deleted"):
            setattr(self.window, name, MethodType(getattr(CalendarWindow, name), self.window))
        self.window.task = lambda work, done: done(work())
        self.snapshots = [self.envelope([self.before]), self.envelope([self.after])]
        patches = (
            patch.object(backend, "_client", return_value=(self.client, Mock(), self.cal, self.ical)),
            patch.object(backend, "component_text", side_effect=lambda *a, **k: self.snapshots.pop(0)),
            patch("prairie_apps.calendar_window.delete_event"),
            patch("prairie_apps.calendar_window.ensure_backup"),
        )
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        start = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
        self.event = Event("series", "source", "Original", start, start + timedelta(hours=1),
                           recurring=True, rid="20260930T120000Z", instance_start=start)

    def envelope(self, components):
        root = self.ical.Component.new_vcalendar()
        root.add_property(self.ical.Property.new_version("2.0"))
        for component in components:
            root.add_component(component.clone())
        return root.as_ical_string()

    def create(self, component, *_):
        if any(c.get_uid() == component.get_uid() for c in self.current):
            raise RuntimeError("Object already exists")
        return True, component.get_uid()

    def undo_after_delete(self, scope="future"):
        self.window.delete_with_backup(self.event, scope)
        return self.window.toast.call_args.kwargs["undo"]

    def test_future_undo_restores_rule_and_keeps_fresh_server_fields(self):
        undo = self.undo_after_delete()
        self.current[0].set_summary("Newer server title")
        self.current[0].set_description("Newer server notes")
        self.current[0] = self.ical.Component.new_from_string(self.current[0].as_ical_string().replace(
            "END:VEVENT", "ATTENDEE;PARTSTAT=ACCEPTED;X-PRIVATE=keep:mailto:alex@example.test\nEND:VEVENT"))
        undo()
        self.client.modify_object_sync.assert_called_once()
        restored = self.client.modify_object_sync.call_args.args[0].as_ical_string()
        self.assertIn("SUMMARY:Newer server title", restored)
        self.assertIn("DESCRIPTION:Newer server notes", restored)
        self.assertIn("COUNT=5", restored)
        self.assertIn("X-PRIVATE:preserve", restored)
        self.assertIn("PARTSTAT=ACCEPTED", restored)
        self.assertIn("X-PRIVATE=keep", restored)
        self.client.create_object_sync.assert_not_called()

    def test_changed_recurrence_since_delete_is_not_overwritten(self):
        undo = self.undo_after_delete()
        self.current[0] = self.ical.Component.new_from_string(ICS.replace("COUNT=5", "COUNT=9"))
        with self.assertRaisesRegex(ValueError, "changed"):
            undo()
        self.client.modify_object_sync.assert_not_called()
        self.client.create_object_sync.assert_not_called()

    def test_whole_series_undo_cannot_overwrite_a_reappeared_uid(self):
        self.snapshots[1] = self.envelope([])
        undo = self.undo_after_delete("all")
        self.current = [self.before.clone()]
        self.current[0].set_summary("Recreated elsewhere")
        with self.assertRaisesRegex(ValueError, "already"):
            undo()
        self.client.modify_object_sync.assert_not_called()
        self.client.create_object_sync.assert_not_called()

    def test_whole_series_undo_recreates_an_absent_event(self):
        self.snapshots[1] = self.envelope([])
        undo = self.undo_after_delete("all")
        self.current = []
        undo()
        self.client.create_object_sync.assert_called_once()
        restored = self.client.create_object_sync.call_args.args[0].as_ical_string()
        self.assertIn("SUMMARY:Original", restored)
        self.assertIn("X-PRIVATE:preserve", restored)
        self.client.modify_object_sync.assert_not_called()

    def test_restore_backup_failure_prevents_any_restore_write(self):
        undo = self.undo_after_delete()
        self.mocks[3].side_effect = OSError("backup failed")
        with self.assertRaisesRegex(OSError, "backup failed"):
            undo()
        self.client.modify_object_sync.assert_not_called()
        self.client.create_object_sync.assert_not_called()

    def test_fresh_read_failure_prevents_any_restore_write(self):
        undo = self.undo_after_delete()
        self.client.get_object_list_sync.side_effect = OSError("read failed")
        with self.assertRaisesRegex(OSError, "read failed"):
            undo()
        self.client.modify_object_sync.assert_not_called()
        self.client.create_object_sync.assert_not_called()

    def test_single_occurrence_undo_keeps_other_exclusions_and_fresh_notes(self):
        self.before = self.ical.Component.new_from_string(ICS.replace("END:VEVENT", "EXDATE:20261014T120000Z\nEND:VEVENT"))
        self.after = self.ical.Component.new_from_string(self.before.as_ical_string().replace(
            "END:VEVENT", "EXDATE:20260930T120000Z\nEND:VEVENT"))
        self.snapshots = [self.envelope([self.before]), self.envelope([self.after])]
        self.current = [self.after.clone()]
        undo = self.undo_after_delete("this")
        self.current[0].set_description("Fresh notes")
        undo()
        restored = self.client.modify_object_sync.call_args.args[0].as_ical_string()
        self.assertIn("DESCRIPTION:Fresh notes", restored)
        self.assertIn("EXDATE:20261014T120000Z", restored)
        self.assertNotIn("EXDATE:20260930T120000Z", restored)
        self.client.create_object_sync.assert_not_called()

    def test_master_is_recreated_before_its_detached_instance(self):
        override = self.ical.Component.new_from_string(ICS.replace("END:VEVENT", "RECURRENCE-ID:20260930T120000Z\nEND:VEVENT"))
        self.snapshots = [self.envelope([override, self.before]), self.envelope([])]
        undo = self.undo_after_delete("all")
        self.current = []
        order = []
        self.client.create_object_sync.side_effect = lambda *_: (order.append("master"), (True, "series"))[1]
        self.client.modify_object_sync.side_effect = lambda *args: order.append(("instance", args[1]))
        undo()
        self.assertEqual(order, ["master", ("instance", self.cal.ObjModType.THIS)])

    def test_partial_restore_failure_is_reported_without_stale_rollback(self):
        override = self.ical.Component.new_from_string(ICS.replace("END:VEVENT", "RECURRENCE-ID:20260930T120000Z\nEND:VEVENT"))
        self.snapshots = [self.envelope([self.before, override]), self.envelope([])]
        undo = self.undo_after_delete("all")
        self.current = []
        self.client.modify_object_sync.side_effect = OSError("provider failed")
        with self.assertRaisesRegex(RuntimeError, "restored 1 part.*backup is preserved"):
            undo()
        self.client.create_object_sync.assert_called_once()
        self.client.remove_object_sync.assert_not_called()
        self.assertEqual(self.client.modify_object_sync.call_count, 1)

    def test_unverified_post_delete_read_cannot_restore(self):
        self.mocks[1].side_effect = [self.snapshots[0], OSError("post-delete read failed")]
        undo = self.undo_after_delete()
        with self.assertRaisesRegex(ValueError, "post-delete state"):
            undo()
        self.client.modify_object_sync.assert_not_called()
        self.client.create_object_sync.assert_not_called()

    def test_false_fresh_read_result_is_not_an_empty_calendar(self):
        undo = self.undo_after_delete()
        self.client.get_object_list_sync.side_effect = None
        self.client.get_object_list_sync.return_value = False, []
        with self.assertRaises(backend.CalendarUnavailable):
            undo()
        self.client.modify_object_sync.assert_not_called()
        self.client.create_object_sync.assert_not_called()

    def test_read_only_calendar_cannot_restore(self):
        undo = self.undo_after_delete()
        self.client.is_readonly.return_value = True
        with self.assertRaisesRegex(ValueError, "read-only"):
            undo()
        self.client.modify_object_sync.assert_not_called()
        self.client.create_object_sync.assert_not_called()

    def test_equivalent_zoned_instance_cannot_overwrite_a_reappeared_override(self):
        override = self.ical.Component.new_from_string(ICS.replace(
            "END:VEVENT", "RECURRENCE-ID;TZID=America/Chicago:20260930T070000\nEND:VEVENT"))
        post = self.ical.Component.new_from_string(ICS.replace("END:VEVENT", "EXDATE:20260930T120000Z\nEND:VEVENT"))
        self.snapshots = [self.envelope([self.before, override]), self.envelope([post])]
        fresh = self.ical.Component.new_from_string(override.as_ical_string().replace(
            "RECURRENCE-ID;TZID=America/Chicago:20260930T070000", "RECURRENCE-ID:20260930T120000Z"))
        fresh.set_summary("Recreated override")
        self.current = [post, fresh]
        undo = self.undo_after_delete("this")
        with self.assertRaisesRegex(ValueError, "already"):
            undo()
        self.client.modify_object_sync.assert_not_called()
        self.client.create_object_sync.assert_not_called()


if __name__ == "__main__":
    unittest.main()
