# SPDX-License-Identifier: Apache-2.0
"""Fixture isolation and Calendar data behavior, without GTK or real EDS."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/prairie-core"))
from prairie_apps.calendar_data import (CalendarAttendee, Event, EventDraft, sms_uri,
    same_occurrence, participant_rows, contact_for_person)
from prairie_apps.calendar_fixture import (CalendarFixture, duration_text, events_on,
    free_gaps, minute_text, month_days, overlap_lanes, parse_event)

FIXTURE = ROOT / "tests/fixtures/calendar-v70.json"
TODAY = date(2026, 9, 23)


def timed(uid, start, end):
    origin = datetime(2026, 9, 23, tzinfo=timezone.utc)
    return Event(str(uid), "work", "Meeting", origin + timedelta(minutes=start), origin + timedelta(minutes=end))


class CalendarDataTests(unittest.TestCase):
    def test_repeated_attendee_names_keep_individual_addresses(self):
        event=replace(timed("people",600,660),participants=(("Alex","Going"),("Alex","Maybe")),
                      attendees=(CalendarAttendee("Alex","first@example.test"),
                                 CalendarAttendee("Alex","second@example.test")))
        self.assertEqual(participant_rows(event),(("Alex","Going","first@example.test"),
                                                  ("Alex","Maybe","second@example.test")))

    def test_addressless_or_mismatched_records_do_not_omit_people(self):
        event=replace(timed("people",600,660),participants=(("Alex","Going"),("Sam","Maybe")),
                      attendees=(CalendarAttendee("Other","other@example.test"),))
        self.assertEqual(participant_rows(event),(("Alex","Going",""),("Sam","Maybe","")))

    def test_contact_identity_uses_address_instead_of_display_name(self):
        from types import SimpleNamespace
        first=SimpleNamespace(name="Alex",email="first@example.test",phone="111")
        second=SimpleNamespace(name="Renamed",email="second@example.test",phone="222")
        self.assertIs(contact_for_person((first,second),"Alex"," SECOND@example.test "),second)
        self.assertIsNone(contact_for_person((first,),"Alex","missing@example.test"))

    def test_name_only_and_duplicate_addresses_require_unique_contacts(self):
        from types import SimpleNamespace
        first=SimpleNamespace(name="Alex",email="first@example.test")
        second=SimpleNamespace(name="Alex",email="second@example.test")
        duplicate=SimpleNamespace(name="Other",email="first@example.test")
        self.assertIs(contact_for_person((first,),"Alex"),first)
        self.assertIsNone(contact_for_person((first,second),"Alex"))
        self.assertIsNone(contact_for_person((first,duplicate),"Alex","first@example.test"))

    def test_message_link_preserves_contact_number(self):
        for raw, expected in (("+1 (415) 555-0123", "sms:+14155550123"),
                              ("020 7946 0123", "sms:02079460123"),
                              ("tel:+44%207700%20900123", "sms:+447700900123"),
                              ("123", "sms:123")):
            with self.subTest(raw=raw):
                self.assertEqual(sms_uri(raw), expected)

    def test_message_link_rejects_ambiguous_or_invalid_numbers(self):
        for raw in ("", "12", "1234567890123456", "+1 415 555 0123 ext 9", "++123", "someone@example.com"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                sms_uri(raw)

    def test_quick_range_preserves_title_and_both_endpoints(self):
        parsed = parse_event('Lunch tomorrow from 3:00 to 5:00', today=TODAY, now_minute=600)
        self.assertEqual((parsed.title, parsed.day, parsed.start, parsed.end),
                         ('Lunch', TODAY + timedelta(days=1), 900, 1020))
        overnight = parse_event('Work tomorrow from 11pm to 1am', today=TODAY, now_minute=600)
        self.assertEqual((overnight.start, overnight.end), (1380, 1500))
        with self.assertRaises(ValueError):
            parse_event('Lunch from 3 to 3', today=TODAY, now_minute=600)

    def setUp(self):
        self.fixture = CalendarFixture(FIXTURE)

    def test_selection_identifies_one_occurrence_and_source(self):
        event=timed("series",600,660)
        self.assertTrue(same_occurrence(event,replace(event,summary="Changed")))
        self.assertFalse(same_occurrence(event,replace(event,source_uid="personal")))
        self.assertFalse(same_occurrence(event,replace(event,start=event.start+timedelta(days=1))))
        recurring=replace(event,rid="20260923T100000Z",instance_start=event.start)
        moved=replace(recurring,start=event.start+timedelta(hours=2))
        self.assertTrue(same_occurrence(recurring,moved))
        self.assertFalse(same_occurrence(recurring,replace(moved,rid="20260924T100000Z")))
        generated=replace(recurring,rid="")
        self.assertTrue(same_occurrence(generated,moved))

    def test_spec_inventory_and_ids(self):
        self.assertEqual(len(self.fixture.events), 42)
        self.assertEqual(sum(e.summary == "Standup" for e in self.fixture.events), 20)
        self.assertEqual([e.uid for e in self.fixture.events], [str(i) for i in range(1, 43)])
        self.assertEqual(self.fixture.today, TODAY)
        self.assertEqual([s.name for s in self.fixture.sources], ["Work", "Personal", "Family", "Holidays"])

    def test_opening_day_has_exact_spec_events(self):
        events = events_on(self.fixture.events, TODAY)
        self.assertEqual([e.summary for e in events], ["Standup", "Press call · The Verge", "Launch walkthrough", "Call Dad"])
        self.assertEqual(events[0].participants, (("Priya Raman", "Going"), ("Nora Feld", "Going"), ("Sam Kaur", "Maybe")))

    def test_parsed_calendar_resolves_real_identifiers_and_readonly_fallback(self):
        from prairie_apps.calendar_fixture import resolve_calendar
        from prairie_apps.calendar_data import CalendarSource
        sources=(CalendarSource("work-uuid","Work","blue","",True),CalendarSource("holiday-uuid","Holidays","amber","",False),CalendarSource("personal-uuid","Personal","green","",True,is_default=True))
        self.assertEqual(resolve_calendar("work",sources),"work-uuid")
        self.assertEqual(resolve_calendar("holiday-uuid",sources),"personal-uuid")
        self.assertEqual(resolve_calendar("unknown",sources),"personal-uuid")
        self.assertEqual(resolve_calendar("work",()),"")

    def test_calendar_filter_preserves_other_events(self):
        shown = events_on(self.fixture.events, TODAY, {"work"})
        self.assertEqual([e.summary for e in shown], ["Call Dad"])
        self.assertEqual(len(self.fixture.events), 42)

    def test_all_day_exclusive_end(self):
        event = next(e for e in self.fixture.events if e.summary == "First day of autumn")
        self.assertEqual(event.days, (date(2026, 9, 22),))
        self.assertEqual(event.end.date(), date(2026, 9, 23))
        self.assertFalse(event.editable)

    def test_range_boundaries(self):
        start = datetime(2026, 9, 23, tzinfo=timezone.utc)
        selected = self.fixture.read(start, start + timedelta(days=1))
        self.assertEqual(len(selected), 4)
        self.assertNotIn("First day of autumn", [e.summary for e in selected])

    def test_month_has_only_required_weeks(self):
        days = month_days(TODAY)
        self.assertEqual((days[0], days[-1], len(days)), (date(2026, 8, 31), date(2026, 10, 4), 35))
        self.assertEqual(len(month_days(date(2021, 2, 1))), 28)
        self.assertEqual(len(month_days(date(2026, 3, 1))), 42)
        with self.assertRaises(ValueError):
            month_days(TODAY, 7)

    def test_free_time_merges_overlaps_and_clips_workday(self):
        events = (timed(1, 570, 660), timed(2, 630, 720), timed(3, 1140, 1200))
        self.assertEqual(free_gaps(TODAY, events, today=TODAY, now_minute=540), ((720, 1080),))

    def test_free_time_rounds_now_and_requires_an_hour(self):
        self.assertEqual(free_gaps(TODAY, (), today=TODAY, now_minute=1021), ())
        self.assertEqual(free_gaps(TODAY, (), today=TODAY, now_minute=901), ((915, 1080),))
        self.assertEqual(free_gaps(date(2026, 9, 26), (), today=TODAY, now_minute=1300), ((600, 1080),))

    def test_free_time_ignores_all_day(self):
        day = date(2026, 9, 22)
        event = next(e for e in self.fixture.events if e.summary == "First day of autumn")
        self.assertEqual(free_gaps(day, (event,), today=TODAY, now_minute=900), ((540, 1080),))

    def test_multiday_busy_time(self):
        event = timed(1, 540, 1620)
        self.assertEqual(free_gaps(TODAY, (event,), today=TODAY, now_minute=540), ())

    def test_overlap_lanes_and_adjacent_events(self):
        rows = overlap_lanes((timed(1, 600, 660), timed(2, 630, 690), timed(3, 690, 720)))
        self.assertEqual([(lane, count) for _, lane, count in rows], [(0, 2), (1, 2), (0, 1)])

    def test_parse_example(self):
        parsed = parse_event("Lunch with Priya Fri 1pm at Nopa for 90m", today=TODAY, now_minute=600,
                             people={"PR": "Priya Raman"})
        self.assertEqual((parsed.title, parsed.day, parsed.start, parsed.end, parsed.people, parsed.location, parsed.calendar),
                         ("Lunch with Priya", date(2026, 9, 25), 780, 870, ("PR",), "Nopa", "personal"))

    def test_parse_relative_days_and_family(self):
        parsed = parse_event("Dinner with Dad tomorrow 7pm", today=TODAY, now_minute=600, people={"MO": "Dad"})
        self.assertEqual((parsed.day, parsed.start, parsed.calendar), (date(2026, 9, 24), 1140, "family"))
        self.assertEqual(parse_event("Review Wed 10am", today=TODAY, now_minute=600).day, date(2026, 9, 30))

    def test_parse_clock_and_duration_validation(self):
        self.assertEqual(parse_event("Breakfast 12am", today=TODAY, now_minute=600).start, 0)
        self.assertEqual(parse_event("Lunch 12pm", today=TODAY, now_minute=600).start, 720)
        self.assertIsNone(parse_event("  ", today=TODAY, now_minute=600))
        for text in ("Lunch 1:99pm", "Lunch 25pm", "Lunch 1pm for 0m"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_event(text, today=TODAY, now_minute=600)

    def test_plain_entry_default_start_matches_spec(self):
        self.assertEqual(parse_event("Lunch", today=TODAY, now_minute=570).start, 600)
        self.assertEqual(parse_event("Lunch", today=TODAY, now_minute=571).start, 660)
        self.assertEqual(parse_event("Lunch", today=TODAY, now_minute=1400).start, 1260)

    def test_formatting(self):
        self.assertEqual([minute_text(m) for m in (0, 570, 720, 1050)], ["12 AM", "9:30 AM", "12 PM", "5:30 PM"])
        self.assertEqual([duration_text(m) for m in (45, 60, 90)], ["45m", "1h", "1h 30m"])

    def test_memory_save_delete_never_changes_json_or_other_fields(self):
        before = FIXTURE.read_bytes()
        event = self.fixture.events[0]
        draft = EventDraft(event.source_uid, "Updated", event.start, event.end, uid=event.uid,
                           location=event.location, tzid=event.tzid)
        with patch.object(Path, "write_text", side_effect=AssertionError("fixture wrote to disk")):
            uid = self.fixture.save(draft)
            changed = next(e for e in self.fixture.events if e.uid == uid)
            self.assertEqual(changed.participants, event.participants)
            self.assertEqual(changed.summary, "Updated")
            self.fixture.delete(changed)
        self.assertEqual(FIXTURE.read_bytes(), before)
        self.assertEqual(len(CalendarFixture(FIXTURE).events), 42)

    def test_readonly_and_invalid_saves(self):
        event = next(e for e in self.fixture.events if not e.editable)
        with self.assertRaises(ValueError):
            self.fixture.delete(event)
        draft = EventDraft("hol", "Changed holiday", event.start, event.end, uid=event.uid)
        with self.assertRaises(ValueError):
            self.fixture.save(draft)
        with self.assertRaises(ValueError):
            self.fixture.save(replace(draft, source_uid="work", summary=""))

    def test_bad_fixture_does_not_fall_back_to_real_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text("{}")
            with self.assertRaises(KeyError):
                CalendarFixture(path)


if __name__ == "__main__":
    unittest.main()
