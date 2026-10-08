#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Creator preview persistence and real-provider response boundaries."""
import json
from pathlib import Path
import tempfile
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

from prairie_apps.clock_backend import ClockStore
from prairie_apps.weather_backend import LocationSearch
from prairie_apps.weather_model import Place
from prairie_apps.tasks_parse import parse_task
from prairie_apps.tasks_data import TasksData


class TaskTimeTests(unittest.TestCase):
    def test_repeat_choices_roundtrip_real_icalendar_and_keep_other_properties(self):
        from prairie_apps.tasks_backend import _modules, patch_task, read_task, value
        from prairie_apps.tasks_repeat import CHOICES, interval_rule
        _, _, ical = _modules()
        original = ical.Component.new_from_string('BEGIN:VTODO\r\nUID:repeat-test\r\nSUMMARY:Keep title\r\nDUE:20261006\r\nX-OWNER:keep\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Reminder\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\nEND:VTODO\r\n')
        for rule in [row[2] for row in CHOICES] + [interval_rule('3', 'MONTHLY')]:
            with self.subTest(rule=rule):
                updated = patch_task(original, {'repeat': rule})
                persisted = read_task(ical.Component.new_from_string(updated.as_ical_string()), 'personal')
                self.assertEqual(persisted.repeat, rule)
                self.assertEqual(persisted.title, 'Keep title')
                self.assertEqual(persisted.due, date(2026, 10, 6))
                self.assertEqual(value(updated, 'X-OWNER'), 'keep')
                self.assertIn('BEGIN:VALARM', updated.as_ical_string())
        with self.assertRaises(ValueError): patch_task(original, {'repeat': 'FREQ=INVALID'})

    def test_repeat_adapter_reaches_existing_store_and_reloads_the_rule(self):
        from prairie_apps.tasks_backend import _modules, patch_task, read_task, TaskList
        _, _, ical = _modules()
        component = ical.Component.new_from_string('BEGIN:VTODO\r\nUID:repeat-test\r\nSUMMARY:Repeat me\r\nDUE:20261006\r\nEND:VTODO\r\n')
        source = TasksData.__new__(TasksData)
        source.fixture = False; source.today = date(2026, 10, 5)
        source.records = {'task': read_task(component, 'personal')}
        writes = []
        def edit(record, fields):
            writes.append(patch_task(ical.Component.new_from_string(record.raw), fields))
            return record.raw
        source.repository = SimpleNamespace(edit=edit, undo=lambda _: None, me='', errors=(),
            registry=SimpleNamespace(ref_source=lambda _: None), comments_are_local=lambda _: True,
            load=lambda: ([TaskList('personal', 'Personal')], [read_task(writes[-1], 'personal')]))
        source.local = SimpleNamespace(read=lambda: {'tasks': {}})
        source.edit('task', {'repeat': 'FREQ=WEEKLY'})
        self.assertEqual(source.load()['tasks'][0]['repeat'], 'FREQ=WEEKLY')
        key = next(iter(source.records))
        source.edit(key, {'repeat': ''})
        self.assertEqual(source.load()['tasks'][0]['repeat'], '')

    def test_never_clears_imported_dates_and_preserves_exception_identity_and_undo(self):
        from prairie_apps.tasks_backend import _modules, patch_task, read_task, value
        _, _, ical = _modules()
        original = ical.Component.new_from_string('BEGIN:VTODO\r\nUID:imported-repeat\r\nSUMMARY:Keep title\r\nDUE:20261006\r\nRRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=12\r\nRDATE:20261007\r\nEXDATE:20261013\r\nX-LUMA-REPEAT-ANCHOR:20261006\r\nRECURRENCE-ID:20261006\r\nX-OWNER:keep\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Reminder\r\nTRIGGER:-PT15M\r\nEND:VALARM\r\nEND:VTODO\r\n')
        before = original.as_ical_string()
        updated = patch_task(original, {'repeat': ''})
        task = read_task(updated, 'personal')
        self.assertFalse(task.recurring)
        for name in ('RRULE', 'RDATE', 'EXDATE', 'X-LUMA-REPEAT-ANCHOR'):
            self.assertEqual(value(updated, name), '')
        self.assertEqual(task.recurrence_id, '20261006')
        self.assertEqual(task.due, date(2026, 10, 6))
        self.assertEqual(value(updated, 'X-OWNER'), 'keep')
        self.assertIn('BEGIN:VALARM', updated.as_ical_string())
        self.assertEqual(original.as_ical_string(), before)
        # The adapter returns the calendar repository's raw snapshot undo.
        source = TasksData.__new__(TasksData)
        source.fixture = False; source.today = date(2026, 10, 5)
        source.records = {'task': read_task(original, 'personal')}
        current = [original]
        def edit(record, fields):
            snapshot = current[0].as_ical_string()
            current[0] = patch_task(current[0], fields)
            return snapshot
        source.repository = SimpleNamespace(edit=edit,
            undo=lambda raw: current.__setitem__(0, ical.Component.new_from_string(raw)))
        undo = source.edit('task', {'repeat': ''})
        self.assertFalse(read_task(current[0], 'personal').recurring)
        undo()
        self.assertEqual(current[0].as_ical_string(), before)
        self.assertTrue(read_task(current[0], 'personal').recurring)
        # Imported RDATE-only tasks are still recurring even without an RRULE.
        dates_only = patch_task(original, {'repeat': ''})
        from prairie_apps.tasks_backend import replace_property
        replace_property(dates_only, 'RDATE', ['RDATE:20261008'])
        self.assertTrue(read_task(dates_only, 'personal').recurring)
        self.assertFalse(read_task(patch_task(dates_only, {'repeat': ''}), 'personal').recurring)

    def test_custom_repeat_keeps_weekdays_and_count_until_constraints(self):
        from prairie_apps.tasks_repeat import custom_rule
        from prairie_apps.tasks_backend import _modules, patch_task, read_task
        _, _, ical = _modules()
        original = ical.Component.new_from_string('BEGIN:VTODO\r\nUID:custom-repeat\r\nSUMMARY:Keep title\r\nDUE:20261006\r\nEND:VTODO\r\n')
        for rule in ('FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR',
                     'FREQ=WEEKLY;BYDAY=TU;COUNT=12',
                     'FREQ=MONTHLY;BYMONTHDAY=6;UNTIL=20271006'):
            with self.subTest(rule=rule):
                frequency = rule.split(';')[0].split('=')[1]
                self.assertEqual(custom_rule(rule, '1', frequency), rule)
                edited = custom_rule(rule, '3', frequency)
                self.assertEqual(edited, rule + ';INTERVAL=3')
                persisted = read_task(patch_task(original, {'repeat': edited}), 'personal').repeat
                # ICalGLib canonicalizes clause order; require every exact
                # field/value, including constraints, rather than text order.
                self.assertEqual(dict(part.split('=', 1) for part in persisted.split(';')),
                                 dict(part.split('=', 1) for part in edited.split(';')))
        # Changing the visible frequency also retains imported limits/days.
        self.assertEqual(custom_rule('FREQ=WEEKLY;BYDAY=TU;COUNT=12', '2', 'MONTHLY'),
                         'FREQ=MONTHLY;BYDAY=TU;COUNT=12;INTERVAL=2')

    def test_custom_repeat_rejects_invalid_intervals_and_frequency(self):
        from prairie_apps.tasks_repeat import interval_rule
        self.assertEqual(interval_rule(' 2 ', 'WEEKLY'), 'FREQ=WEEKLY;INTERVAL=2')
        for interval, unit in [('0', 'DAILY'), ('-1', 'WEEKLY'), ('1.5', 'MONTHLY'), ('366', 'YEARLY'), ('2', 'INVALID')]:
            with self.subTest(interval=interval, unit=unit), self.assertRaises(ValueError):
                interval_rule(interval, unit)

    def test_reported_sentence_roundtrips_real_icalendar_preserving_other_fields(self):
        from prairie_apps.tasks_backend import _modules, patch_task, read_task, value
        _, _, ical = _modules()
        original = ical.Component.new_from_string('BEGIN:VTODO\r\nUID:private-test\r\nSUMMARY:Before\r\nX-OWNER:keep\r\nEND:VTODO\r\n')
        for phrase in ('at3:00p.m.', 'at 3 PM', 'at3PM'):
            parsed = parse_task('take out trash tomorrow ' + phrase, today=date(2026, 10, 5))
            self.assertEqual(parsed.title, 'take out trash')
            updated = patch_task(original, {'title': parsed.title, 'due': parsed.due})
            persisted = read_task(ical.Component.new_from_string(updated.as_ical_string()), 'personal')
            self.assertEqual(persisted.due.astimezone().date(), date(2026, 10, 6))
            self.assertEqual((persisted.due.astimezone().hour, persisted.due.astimezone().minute), (15, 0))
            self.assertEqual(value(updated, 'X-OWNER'), 'keep')

    def test_explicit_time_edit_clears_and_validates_without_unrelated_edits(self):
        source = TasksData.__new__(TasksData)
        source.fixture = False
        source.today = date(2026, 10, 5)
        record = SimpleNamespace(due=date(2026, 10, 6), start=None)
        source.records = {'task': record}
        writes = []
        source.repository = SimpleNamespace(edit=lambda rec, fields: writes.append(fields), undo=lambda _: None)
        source.edit('task', {'time': '3:00 p.m.'})
        self.assertEqual(set(writes[-1]), {'due'})
        self.assertEqual((writes[-1]['due'].hour, writes[-1]['due'].minute), (15, 0))
        record.due = writes[-1]['due']
        source.edit('task', {'time': ''})
        self.assertEqual(writes[-1], {'due': date(2026, 10, 6)})
        with self.assertRaises(ValueError): source.edit('task', {'time': '25:60'})
        self.assertEqual(len(writes), 2)


class SetupClockTests(unittest.TestCase):
    def test_setup_zone_seeds_once_and_removal_survives_reopening(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'state.json'
            # The alarm agent creates the store before the first window.
            ClockStore(path)
            store = ClockStore(path)
            self.assertTrue(store.seed_home_clock('Europe/Paris'))
            card, = store.world_clocks()
            self.assertEqual((card.label, card.zone), ('Paris', 'Europe/Paris'))
            self.assertFalse(store.seed_home_clock('America/Chicago'))
            self.assertEqual(store.world_clocks(), (card,))
            store.remove_world_clock(card.uid)
            reopened = ClockStore(path)
            self.assertFalse(reopened.seed_home_clock('Europe/Paris'))
            self.assertEqual(reopened.world_clocks(), ())

    def test_existing_cities_keep_order_and_are_not_duplicated(self):
        with tempfile.TemporaryDirectory() as root:
            store = ClockStore(Path(root) / 'state.json')
            first = store.add_world_clock('Kyoto', 'Asia/Tokyo')
            second = store.add_world_clock('London', 'Europe/London')
            self.assertFalse(store.seed_home_clock('America/Chicago'))
            self.assertEqual(store.world_clocks(), (first, second))

    def test_old_empty_lists_are_preserved_and_bad_zones_do_not_consume_setup(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'state.json'
            store = ClockStore(path)
            self.assertFalse(store.seed_home_clock('Not/A_Zone'))
            self.assertTrue(store.seed_home_clock('Asia/Kathmandu'))
            path.write_text(json.dumps({'world': [], 'alarms': [], 'timers': []}))
            self.assertFalse(ClockStore(path).seed_home_clock('America/Chicago'))
            self.assertEqual(ClockStore(path).world_clocks(), ())


class PostalResponseTests(unittest.TestCase):
    def test_zip_and_zip_plus_four_use_postal_city_and_real_nearby_zone(self):
        source = LocationSearch()
        source._index = [Place('', 'Chicago', latitude=41.85, longitude=-87.65,
                               timezone='America/Chicago')]
        postal = {'country': 'United States', 'places': [
            {'place name': 'Chicago', 'state': 'Illinois', 'latitude': '41.8858', 'longitude': '-87.6181'}]}
        for query in ('60601', ' 60601-1234 '):
            with self.subTest(query=query), patch('prairie_apps.weather_backend._request',
                                                  return_value=(postal, '')) as request:
                place, = source.lookup(query)
                request.assert_called_once_with('https://api.zippopotam.us/us/60601')
                self.assertEqual((place.name, place.region, place.country, place.timezone),
                                 ('Chicago', 'Illinois', 'United States', 'America/Chicago'))

    def test_no_match_is_distinct_from_failed_network(self):
        source = LocationSearch()
        with patch('prairie_apps.weather_backend._request', return_value=({}, '')):
            self.assertEqual(source.lookup('00000'), ())
        with patch('prairie_apps.weather_backend._request', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                source.lookup('60601')


if __name__ == '__main__':
    unittest.main(verbosity=2)
