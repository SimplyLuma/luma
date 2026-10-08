# SPDX-License-Identifier: Apache-2.0
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import sys
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/prairie-core'))
from prairie_apps.tasks_data import TasksData, day_label, task_groups, visible_tasks, suggested_tasks, quick_chips
from prairie_apps.tasks_parse import Choice, parse_task
from prairie_apps.tasks_backend import Task


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.path = ROOT / 'tests/fixtures/tasks-v70.json'
        with patch.dict(os.environ, {'LUMA_TASKS_FIXTURE': str(self.path)}):
            self.source = TasksData()
        self.data = self.source.load()
        self.tasks = self.data['tasks']

    def ids(self, view): return [t['id'] for t in visible_tasks(self.tasks, view)]

    def test_v70_smart_views(self):
        self.assertEqual(self.ids('today'), ['t100', 't101', 't102', 't103'])
        self.assertEqual(self.ids('upcoming'), ['t104', 't105', 't106', 't107', 't110', 't111'])
        self.assertEqual(self.ids('mine'), ['t101', 't103', 't105'])
        self.assertEqual(self.ids('flag'), ['t100', 't105'])
        self.assertEqual(self.ids('done'), ['t114', 't115'])
        self.assertEqual(self.ids('launch'), ['t100', 't101', 't104', 't105', 't111', 't112'])

    def test_today_separates_overdue_without_reordering(self):
        groups = task_groups(self.tasks, self.data['lists'], 'today')
        self.assertEqual([name for name, _ in groups], ['Overdue', 'Today'])
        self.assertEqual([t['id'] for t in groups[0][1]], ['t103'])
        self.assertEqual([t['id'] for t in groups[1][1]], ['t100', 't101', 't102'])

    def test_upcoming_dates_are_grouped_in_order(self):
        groups = task_groups(self.tasks, self.data['lists'], 'upcoming', today=self.source.today)
        self.assertEqual([name for name, _ in groups], ['Tomorrow', 'Saturday', 'Monday', 'Wed, Sep 30', 'Fri, Oct 2'])
        self.assertEqual([t['id'] for t in suggested_tasks(self.tasks)], ['t104', 't106', 't107'])

    def test_sections_keep_empty_sections_and_search_matches_title_only(self):
        groups = task_groups(self.tasks, self.data['lists'], 'launch', 'walkthrough')
        self.assertEqual([name for name, _ in groups], ['Before launch', 'Launch day', 'After'])
        self.assertEqual([len(tasks) for _, tasks in groups], [1, 0, 0])
        self.assertEqual(visible_tasks(self.tasks, 'today', 'pricing'), [])
        self.assertEqual(visible_tasks(self.tasks, 'today', 'no matching task'), [])

    def test_due_labels_anchor_to_fixture_date(self):
        self.assertEqual(self.source.today, date(2026, 9, 23))
        self.assertEqual(day_label(None, self.source.today), '')
        self.assertEqual(day_label(-2, self.source.today), '2 days ago')
        self.assertEqual(day_label(0, self.source.today), 'Today')

    def test_fixture_edits_and_undo_preserve_disk_and_unedited_fields(self):
        before = self.path.read_bytes()
        original = self.source.load()['tasks'][0]
        undo = self.source.edit('t100', {'t': 'Updated title'})
        changed = self.source.load()['tasks'][0]
        self.assertEqual(changed['t'], 'Updated title')
        for key, value in original.items():
            if key != 't': self.assertEqual(changed[key], value)
        undo()
        self.assertEqual(self.source.load()['tasks'][0], original)
        self.assertEqual(self.path.read_bytes(), before)

    def test_steps_comments_delete_and_undo_never_reach_backend(self):
        before = self.path.read_bytes()
        with patch.object(self.source.repository, 'edit', side_effect=AssertionError('Backend touched')), \
             patch.object(self.source.repository, 'delete', side_effect=AssertionError('Backend touched')):
            undo_step = self.source.step('t100', 3)
            self.assertTrue(self.source.load()['tasks'][0]['subs'][3][1])
            undo_step()
            self.assertFalse(self.source.load()['tasks'][0]['subs'][3][1])
            self.source.comment('t100', 'A fixture comment')
            self.assertEqual(self.source.load()['tasks'][0]['act'][-1][2], 'A fixture comment')
            undo_delete = self.source.delete('t100')
            self.assertNotIn('t100', [t['id'] for t in self.source.load()['tasks']])
            undo_delete()
            self.assertEqual(self.source.load()['tasks'][0]['id'], 't100')
        self.assertEqual(self.path.read_bytes(), before)

    def test_v70_quick_entry_chips_describe_explicit_tokens(self):
        def chips(draft):
            parsed = parse_task(draft, today=self.source.today, view="today", source_uid="personal",
                                lists=tuple(Choice(s["id"], s["n"]) for s in self.data["lists"]),
                                people=tuple(Choice(k, p["n"]) for k, p in self.data["people"].items()))
            return quick_chips(draft, parsed, self.source.today, self.data["lists"], self.data["people"])
        self.assertEqual(chips(""), [])
        self.assertEqual(chips("Call Theo"), [])
        self.assertEqual(chips("Call Theo tomorrow 3pm !high #Launch @Priya"),
                         [("calendar", "Tomorrow, 3:00 PM"), ("flag", "High"), ("list", "Launch"), ("user", "Priya")])

    def test_loaded_snapshot_is_separate_from_fixture_memory(self):
        snapshot = self.source.load()
        snapshot['tasks'][0]['t'] = 'Changed by caller'
        self.assertEqual(self.source.load()['tasks'][0]['t'], 'Finish the walkthrough deck')


class RealWriteGuardTests(unittest.TestCase):
    def setUp(self):
        self.source = TasksData.__new__(TasksData)
        self.source.fixture = False
        self.source.repository = Mock()
        self.source.records = {"task": Task("task", "source", "Title")}
        self.source.today = date(2026, 9, 23)

    def test_unsupported_and_mixed_changes_fail_before_any_write(self):
        for changes in ({"done": True, "flag": True}, {"done": True, "t": "Changed"},
                        {"t": "Changed", "who": ["me", "other"]}):
            with self.assertRaises(ValueError): self.source.edit("task", changes)
            self.source.repository.assert_not_called()
            self.assertEqual(self.source.repository.method_calls, [])

    def test_date_picker_preserves_a_timed_task_clock(self):
        from datetime import datetime
        record = Task("task", "source", "Title", due=datetime(2026, 9, 23, 17, 30).astimezone())
        self.source.records["task"] = record
        self.source.edit("task", {"due": 1})
        result = self.source.repository.edit.call_args.args[1]
        self.assertEqual(set(result), {"due"})
        self.assertEqual(result["due"].astimezone().date(), date(2026, 9, 24))
        self.assertEqual((result["due"].astimezone().hour, result["due"].minute), (17, 30))

    def test_title_edit_sends_only_the_edited_field(self):
        self.source.edit("task", {"t": "Changed"})
        self.source.repository.edit.assert_called_once_with(self.source.records["task"], {"title": "Changed"})

    def test_explicit_time_saves_and_clears_only_due_timestamp(self):
        from datetime import datetime
        record = Mock(due=date(2026, 9, 24), start=None)
        self.source.records['task'] = record
        self.source.edit('task', {'time': '3:00 p.m.'})
        due = self.source.repository.edit.call_args.args[1]['due']
        self.assertEqual(due.date(), date(2026, 9, 24))
        self.assertEqual((due.hour, due.minute), (15, 0))
        record.due = due
        self.source.edit('task', {'time': ''})
        self.assertEqual(self.source.repository.edit.call_args.args[1], {'due': date(2026, 9, 24)})
        before = self.source.repository.edit.call_count
        with self.assertRaises(ValueError): self.source.edit('task', {'time': '25:60'})
        self.assertEqual(self.source.repository.edit.call_count, before)

    def test_time_and_day_edit_are_order_independent(self):
        record = Task("task", "source", "Title")
        self.source.records['task'] = record
        for changes in ({'time': '15:30', 'due': 1}, {'due': 1, 'time': '15:30'}):
            self.source.edit('task', changes)
            due = self.source.repository.edit.call_args.args[1]['due']
            self.assertEqual(due.date(), date(2026, 9, 24))
            self.assertEqual((due.hour, due.minute), (15, 30))
        self.source.repository.complete.assert_not_called()


class LocalReadTests(unittest.TestCase):
    def test_local_flags_assignments_and_my_comments_are_read_without_eds_writes(self):
        from prairie_apps.tasks_backend import Task, TaskList, Comment
        repository = Mock(me="nick@example.com", errors=())
        repository.load.return_value = ((TaskList("source", "Personal"),),
                                       (Task("task", "source", "Title", assignee="remote@example.com",
                                             comments=(Comment("Mine", "nick@example.com", "now"),)),))
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_DATA_HOME": directory}), \
             patch('prairie_apps.tasks_data.source_from_environment', return_value=repository):
            source = TasksData(); original = source.load()["tasks"][0]
            self.assertEqual(original["who"], ["remote@example.com"])
            self.assertEqual(original["act"][0][0], "me")
            undo = source.edit(original["id"], {"flag": True, "who": ["me", "other"]})
            changed = TasksData().load()["tasks"][0]
            self.assertTrue(changed["flag"]); self.assertEqual(changed["who"], ["me", "other"])
            repository.edit.assert_not_called(); repository.complete.assert_not_called()
            undo(); self.assertEqual(source.load()["tasks"][0]["who"], ["remote@example.com"])


if __name__ == '__main__': unittest.main()
