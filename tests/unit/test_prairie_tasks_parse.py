# SPDX-License-Identifier: Apache-2.0
from datetime import date, datetime
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src/prairie-core"))
from prairie_apps.tasks_parse import Choice, parse_task


class QuickAddTests(unittest.TestCase):
    def test_explicit_range_keeps_both_endpoints_and_clean_title(self):
        for wording in ('from 3:00 to 5:00', '3 to 5pm', 'from 3 PM to 5 PM'):
            result = self.parse('Lunch tomorrow ' + wording)
            self.assertEqual(result.title, 'Lunch')
            self.assertEqual((result.start.hour, result.due.hour), (15, 17))
            self.assertEqual(result.start.date(), date(2026, 9, 23))

    def test_range_can_cross_midnight(self):
        result = self.parse('Maintenance tomorrow from 11pm to 1am')
        self.assertEqual(result.title, 'Maintenance')
        self.assertEqual(result.due.date(), date(2026, 9, 24))
        self.assertEqual(result.start.hour, 23)

    def parse(self, text, **kw):
        return parse_task(text, today=date(2026, 9, 22), **kw)

    def test_handoff_sentence(self):
        result = self.parse("Send invoice fri 3pm #finance @ada !!", lists=(Choice("finance", "Finance"),), people=(Choice("ada@example.com", "Ada Verlaine"),))
        self.assertEqual(result.title, "Send invoice")
        self.assertEqual(result.due.date(), date(2026, 9, 25))
        self.assertEqual(result.due.hour, 15)
        self.assertEqual(result.priority, 5)
        self.assertEqual(result.assignee, "ada@example.com")
        self.assertEqual(result.source_uid, "finance")
        self.assertEqual(len(result.chips), 4)

    def test_v70_named_priority_and_next_week(self):
        for token, priority in (("!high", 1), ("!medium", 5), ("!med", 5), ("!low", 9)):
            result = self.parse("Prepare NEXT week " + token)
            self.assertEqual(result.title, "Prepare")
            self.assertEqual(result.due, date(2026, 9, 29))
            self.assertEqual(result.priority, priority)
        self.assertEqual(self.parse("Next weekly report !higher").title, "Next weekly report !higher")

    def test_weekday_never_today(self):
        self.assertEqual(self.parse("Next tue").due, date(2026, 9, 29))

    def test_date_only_is_not_a_midnight_timestamp(self):
        self.assertIs(type(self.parse("Milk tomorrow").due), date)

    def test_time_without_date(self):
        result = self.parse("Call 12:30am")
        self.assertEqual((result.due.day, result.due.hour, result.due.minute), (22, 0, 30))

    def test_creator_report_compact_at_and_punctuated_clock(self):
        for token in ('at3:00p.m.', 'at 3:00 p.m.', '3 PM', 'at3PM'):
            with self.subTest(token=token):
                result = self.parse('take out trash tomorrow ' + token)
                self.assertEqual(result.title, 'take out trash')
                self.assertEqual(result.due.date(), date(2026, 9, 23))
                self.assertEqual((result.due.hour, result.due.minute), (15, 0))
        self.assertEqual(self.parse('Call at25pm tomorrow').title, 'Call at25pm')

    def test_invalid_times_stay_in_title(self):
        self.assertEqual(self.parse("Call 25pm 3:70pm").title, "Call 25pm 3:70pm")

    def test_ambiguous_list_is_not_silently_chosen(self):
        result = self.parse("Buy #ho", lists=(Choice("1", "Home"), Choice("2", "House")))
        self.assertIn("#ho", result.title)
        self.assertTrue(result.warnings)

    def test_personal_list_does_not_assign(self):
        self.assertEqual(self.parse("Email @ada").assignee, "")

    def test_smart_view_defaults(self):
        self.assertEqual(self.parse("Buy milk", view="today").due, date(2026, 9, 22))
        self.assertEqual(self.parse("Fix", view="mine", me="me@example.com").assignee, "me@example.com")

    def test_normalized_list(self):
        self.assertEqual(self.parse("Publish #launch1", lists=(Choice("id", "Launch 1.0"),)).source_uid, "id")

    def test_priority_and_no_substring_matches(self):
        for token, priority in (("!", 9), ("!!", 5), ("!!!", 1)):
            self.assertEqual(self.parse("Buy " + token).priority, priority)
        self.assertEqual(self.parse("Sunday's report! todayish").title, "Sunday's report! todayish")

if __name__ == "__main__": unittest.main()
