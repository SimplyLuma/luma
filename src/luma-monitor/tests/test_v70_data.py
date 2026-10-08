# SPDX-License-Identifier: Apache-2.0
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from luma_monitor.v70_data import FixtureSource, activity_matches, format_value, sorted_apps, fixture_process_properties, fixture_totals

FIXTURE = Path(__file__).resolve().parents[3] / 'tests/fixtures/monitor-v70.json'


class V70DataTests(unittest.TestCase):
    def test_spec_inventory(self):
        source = FixtureSource(FIXTURE)
        self.assertEqual(len(source.apps), 10)
        self.assertEqual([r[0] for r in source.resources], ['cpu', 'mem', 'disk', 'net', 'en'])
        self.assertEqual(sum(g['total'] for g in source.groups), 683)
        self.assertEqual(sum(len(a['procs']) for a in source.apps), 21)
        self.assertEqual(source.apps[0]['sub'], '3 windows · 14 tabs')
        self.assertEqual(source.totals, {'mem': 16384, 'disk': 512})

    def test_value_boundaries(self):
        for value, resource, expected in [
            (None, 'cpu', '—'), (16, 'cpu', '16%'), (.6, 'cpu', '0.6%'),
            (10.5, 'cpu', '11%'), (1024, 'mem', '1.0 GB'), (380, 'mem', '380 MB'),
            (0, 'net', '—'), (.004, 'net', '—'), (.0045, 'net', '5 KB/s'),
            (1.4, 'net', '1.4 MB/s'), (38, 'en', 'High'), (22, 'en', 'Moderate'),
            (3, 'en', 'Low'), (0, 'en', 'Very low')]:
            with self.subTest(value=value, resource=resource):
                self.assertEqual(format_value(value, resource), expected)

    def test_network_total_matches_v70_sequential_rounding(self):
        totals=fixture_totals(FixtureSource(FIXTURE))
        self.assertEqual(format_value(totals['up'],'net'),'418 KB/s')

    def test_process_name_search_and_casefold(self):
        source = FixtureSource(FIXTURE)
        self.assertEqual([a['id'] for a in sorted_apps(source.apps, 'mem', query='RUSTC')], ['terminal'])
        self.assertTrue(activity_matches(source.apps[0], '  simplyluma.com  '))
        self.assertFalse(activity_matches(source.apps[0], 'unknown'))
        self.assertEqual(sorted_apps(source.apps, 'cpu', query='missing'), [])

    def test_sort_does_not_modify_input(self):
        source = FixtureSource(FIXTURE)
        before = copy.deepcopy(source.apps)
        self.assertEqual(sorted_apps(source.apps, 'disk')[0]['id'], 'filer')
        self.assertEqual(sorted_apps(source.apps, 'cpu', key='n', descending=False)[0]['n'], 'Calendar')
        self.assertEqual(source.apps, before)

    def test_fixture_operations_never_call_host_or_write(self):
        source = FixtureSource(FIXTURE)
        before = FIXTURE.read_bytes()
        with patch('os.kill', side_effect=AssertionError('host signal')), patch('pathlib.Path.write_text', side_effect=AssertionError('store write')):
            source.pause('viola')
            self.assertEqual(source.apps[0]['cpu'], 0)
            source.resume('viola')
            self.assertEqual(source.apps[0]['cpu'], 16)
            source.set_priority('viola', '5')
            self.assertEqual(source.priority['viola'], '5')
            source.quit('viola')
            self.assertEqual(len(source.visible_apps()), 9)
            source.reopen('viola')
            self.assertEqual(len(source.visible_apps()), 10)
        self.assertEqual(FIXTURE.read_bytes(), before)

    def test_bad_fixture_refuses_without_live_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'fixture.json'
            path.write_text(json.dumps({'apps': []}))
            with self.assertRaises(ValueError): FixtureSource(path)
            with self.assertRaises(OSError): FixtureSource(Path(temp) / 'missing.json')

    def test_unknown_targets_and_priority_refused(self):
        source = FixtureSource(FIXTURE)
        with self.assertRaises(KeyError): source.quit('missing')
        with self.assertRaises(ValueError): source.set_priority('viola', '-20')

    def test_raw_process_controls_stay_in_memory_and_reject_kernel(self):
        source = FixtureSource(FIXTURE)
        with patch('os.kill', side_effect=AssertionError('host signal')):
            source.pause_process(3113)
            self.assertIn('p3113', source.stopped)
            source.resume_process(3113)
            self.assertNotIn('p3113', source.stopped)
            source.set_process_priority(3113, '5')
            self.assertEqual(source.priority['p3113'], '5')
            with self.assertRaises(ValueError): source.pause_process(245)
            with self.assertRaises(ValueError): source.set_process_priority(245, '5')
            with self.assertRaises(KeyError): source.pause_process(99999999)

    def test_raw_process_properties_follow_controls(self):
        source=FixtureSource(FIXTURE);group=source.groups[0];process=next(p for p in group['p'] if p[1]==3113)
        self.assertEqual(dict(fixture_process_properties(source,process,group))['Status'],'Sleeping')
        source.pause_process(3113);source.set_process_priority(3113,'5')
        facts=dict(fixture_process_properties(source,process,group))
        self.assertEqual(facts['Status'],'Stopped (SIGSTOP)')
        self.assertEqual(facts['Priority'],'Low · nice 5')
        source.resume_process(3113)
        self.assertEqual(dict(fixture_process_properties(source,process,group))['Status'],'Sleeping')
