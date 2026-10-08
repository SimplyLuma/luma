# SPDX-License-Identifier: Apache-2.0
from datetime import date, datetime, timezone
from pathlib import Path
import os
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/prairie-core"))
from prairie_apps.tasks_backend import Task, TaskList, TasksRepository, day, due_words, group_tasks, patch_task, read_task, select_tasks, value
try:
    from prairie_apps.tasks_backend import _modules
    _, _, I = _modules()
except (ImportError, ValueError): I = None

FIXTURE = """BEGIN:VTODO
UID:original
SUMMARY:Invoice
DUE;VALUE=DATE:20260922
RRULE:FREQ=WEEKLY;COUNT=3
PRIORITY:5
RELATED-TO;RELTYPE=DEPENDS-ON:waiting
CATEGORIES:Finance,Personal
X-SERVER-DATA:opaque
ATTENDEE;CN=Ada;PARTSTAT=ACCEPTED:mailto:ada@example.com
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
DESCRIPTION:Remember
END:VALARM
END:VTODO
""".replace("\n", "\r\n")


class ModelTests(unittest.TestCase):
    def test_date_words(self):
        today = date(2026, 9, 22)
        self.assertEqual(due_words(today, today), "Today")
        self.assertEqual(due_words(date(2026, 9, 21), today), "Yesterday")
        self.assertEqual(due_words(date(2026, 9, 25), today), "Friday")

    def test_views_span_sources_but_hide_subtasks(self):
        tasks = (Task("1", "a", "Overdue", date(2026, 9, 21)), Task("2", "b", "Today", date(2026, 9, 22)), Task("3", "a", "Step", date(2026, 9, 22), parent="1"), Task("4", "b", "Done", date(2026, 9, 22), completed=True))
        today = select_tasks(tasks, "today", today=date(2026, 9, 22))
        self.assertEqual(len(today), 2)
        self.assertEqual([name for name, _ in group_tasks(today, "today", today=date(2026, 9, 22))], ["Overdue", "Today"])
        self.assertEqual([t.uid for t in select_tasks(tasks, "b")], ["2"])


@unittest.skipIf(I is None, "ICalGLib not installed")
class ICalendarTests(unittest.TestCase):
    def setUp(self): self.component = I.Component.new_from_string(FIXTURE)

    def test_interval_roundtrip_preserves_endpoints_and_other_client_fields(self):
        begin = datetime(2026, 10, 7, 15, tzinfo=timezone.utc)
        end = datetime(2026, 10, 7, 17, tzinfo=timezone.utc)
        updated = patch_task(self.component, {'start': begin, 'due': end})
        read = read_task(updated, 'list')
        self.assertEqual(read.start, begin)
        self.assertEqual(read.due, end)
        self.assertIn('BEGIN:VALARM', read.raw)
        self.assertEqual(value(updated, 'X-SERVER-DATA'), 'opaque')
        cleared = read_task(patch_task(updated, {'start': None}), 'list')
        self.assertIsNone(cleared.start)
        self.assertEqual(cleared.due, end)

    def test_roundtrip_preserves_server_properties(self):
        task = patch_task(self.component, {"completed": True})
        task = patch_task(task, {"completed": False, "title": "Renamed"})
        for prop in ("RRULE", "PRIORITY", "RELATED-TO", "CATEGORIES", "ATTENDEE", "X-SERVER-DATA"):
            self.assertEqual(value(task, prop), value(self.component, prop), prop)
        self.assertIn("BEGIN:VALARM", task.as_ical_string())
        self.assertIn("PARTSTAT=ACCEPTED", task.as_ical_string())
        self.assertEqual(task.get_uid(), "original")

    def test_all_day_is_date_in_both_hemispheres(self):
        import time
        original = os.environ.get("TZ")
        try:
            for zone in ("America/Los_Angeles", "Pacific/Auckland"):
                os.environ["TZ"] = zone; time.tzset()
                updated = patch_task(self.component, {"due": date(2026, 9, 25)})
                self.assertIn("DUE;VALUE=DATE:20260925", updated.as_ical_string())
                self.assertEqual(read_task(updated, "list").due, date(2026, 9, 25))
        finally:
            if original is None: os.environ.pop("TZ", None)
            else: os.environ["TZ"] = original
            time.tzset()

    def test_notes_escape_and_restore(self):
        notes = "First, next; end\\line\nNew line"
        updated = patch_task(self.component, {"notes": notes})
        self.assertEqual(read_task(updated, "list").notes, notes)

    def test_no_due_date(self):
        updated = patch_task(self.component, {"due": None})
        self.assertIsNone(read_task(updated, "list").due)

    def test_repeat_edit(self):
        updated = patch_task(self.component, {"repeat": "FREQ=MONTHLY;BYDAY=1MO"})
        self.assertEqual(value(updated, "RRULE"), "FREQ=MONTHLY;BYDAY=1MO")

    def test_waiting_does_not_destroy_parent(self):
        self.component.add_property(I.Property.new_from_string("RELATED-TO:parent"))
        updated = patch_task(self.component, {"waiting": "other"})
        task = read_task(updated, "list")
        self.assertEqual(task.parent, "parent")
        self.assertEqual(task.waiting, "other")

    def repository(self):
        repo = TasksRepository()
        store = {"original": self.component.clone()}
        repo._get = lambda source, uid, rid="": store[uid].clone()
        repo._write = lambda source, c: store.__setitem__(c.get_uid(), c.clone())
        def create(source, c):
            if c.get_uid() in store: raise RuntimeError("UID already exists")
            store[c.get_uid()] = c.clone()
            return c.get_uid()
        repo._create = create
        repo._delete = lambda source, uid, rid="": store.pop(uid)
        return repo, store

    def test_complete_repeat_and_undo(self):
        repo, store = self.repository()
        undo, message = repo.complete(read_task(self.component, "list"), True)
        self.assertEqual(len(store), 2)
        self.assertEqual(read_task(store["original"], "list").due, date(2026, 9, 29))
        completed = read_task(store[undo.created_uid], "list")
        self.assertTrue(completed.completed)
        self.assertFalse(completed.repeat)
        repo.undo(undo)
        self.assertEqual(len(store), 1)
        self.assertEqual(store["original"].as_ical_string(), self.component.as_ical_string())

    def test_exception_date_advances_to_next_valid_occurrence(self):
        self.component.add_property(I.Property.new_from_string("EXDATE;VALUE=DATE:20260929"))
        repo, store = self.repository()
        repo.complete(read_task(self.component, "list"), True)
        self.assertEqual(read_task(store["original"], "list").due, date(2026, 10, 6))

    def test_weekly_repeat_keeps_wall_time_across_dst(self):
        self.component = I.Component.new_from_string("BEGIN:VTODO\r\nUID:original\r\nSUMMARY:Test\r\nDUE;TZID=America/New_York:20261030T090000\r\nRRULE:FREQ=WEEKLY\r\nEND:VTODO\r\n")
        repo, store = self.repository()
        repo.complete(read_task(self.component, "list"), True)
        self.assertIn("DUE;TZID=America/New_York:20261106T090000", store["original"].as_ical_string())

    def test_count_does_not_restart_each_completion(self):
        repo, store = self.repository()
        for expected in (date(2026, 9, 29), date(2026, 10, 6)):
            repo.complete(read_task(store["original"], "list"), True)
            self.assertEqual(read_task(store["original"], "list").due, expected)
        repo.complete(read_task(store["original"], "list"), True)
        self.assertTrue(read_task(store["original"], "list").completed)
        self.assertEqual(len(store), 3)

    def test_delete_undo_same_uid(self):
        repo, store = self.repository()
        action = repo.delete(read_task(self.component, "list"))
        self.assertNotIn("original", store)
        repo.undo(action)
        self.assertEqual(store["original"].as_ical_string(), self.component.as_ical_string())

    def test_undo_rejects_newer_changes(self):
        repo, store = self.repository()
        action = repo.edit(read_task(self.component, "list"), {"title": "Rename"})
        store["original"].set_summary("Changed elsewhere")
        with self.assertRaisesRegex(RuntimeError, "changed"): repo.undo(action)
        self.assertEqual(store["original"].get_summary(), "Changed elsewhere")

    def test_comments_roundtrip_author_time(self):
        repo, store = self.repository()
        repo.comment(read_task(self.component, "list"), "Looks good; ship it", "Ada Verlaine")
        comments = read_task(store["original"], "list").comments
        self.assertEqual(comments[0].text, "Looks good; ship it")
        self.assertEqual(comments[0].author, "Ada Verlaine")
        self.assertTrue(comments[0].created)

if __name__ == "__main__": unittest.main()
