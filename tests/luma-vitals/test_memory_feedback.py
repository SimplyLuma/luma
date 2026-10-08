# SPDX-License-Identifier: Apache-2.0
"""Structured evidence and notification decisions; native journal/bus gates are separate."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / 'src/luma-vitals'
if SOURCE.exists():
    sys.path.insert(0, str(SOURCE))
from luma_vitals.memory_feedback import MemoryFeedback, MemoryWatch, UNIT_FAILURE_MESSAGE_ID, SYSTEMD_EXECUTABLE, memory_query
from luma_vitals.detectors import Event


class State(dict):
    def set(self, key, value): self[key] = value


class MemoryWatchTests(unittest.TestCase):
    def setUp(self):
        self.state = State()
        self.watch = SimpleNamespace(state=self.state, uid=1000, boot_id='current')
        self.user = {'_BOOT_ID': 'current', '_COMM': 'systemd', '_UID': '1000',
                     '_EXE': SYSTEMD_EXECUTABLE, 'MESSAGE_ID': UNIT_FAILURE_MESSAGE_ID,
                     'USER_UNIT': 'app-gnome-test-1.scope', 'USER_INVOCATION_ID': 'invocation1',
                     'UNIT_RESULT': 'oom-kill', '__CURSOR': 'user1'}
        self.queries = []

    def read(self, records):
        def runner(query):
            self.queries.append(query)
            source = 'user' if '_COMM=systemd' in query else 'oomd'
            return '\n'.join(json.dumps(entry) for entry in records.get(source, []))
        return MemoryWatch(self.watch, runner).events()

    def test_verified_sources_and_persistent_invocation_cursor_dedup(self):
        events = self.read({'user': [self.user]})
        self.assertEqual([event.kind for event in events], ['out-of-memory'])
        self.assertEqual({event.unit for event in events}, {'app-gnome-test-1.scope'})
        self.assertEqual(self.read({'user': [self.user]}), [])
        newer_cursor_same_invocation = {**self.user, '__CURSOR': 'user2'}
        self.assertEqual(self.read({'user': [newer_cursor_same_invocation]}), [])
        next_invocation = {**self.user, '__CURSOR': 'user3', 'USER_INVOCATION_ID': 'invocation2'}
        self.assertEqual(len(self.read({'user': [next_invocation]})), 1)
        for query in self.queries:
            self.assertIn('-b', query)
            self.assertIn('--lines=256', query)
        self.assertTrue(any('--after-cursor=user1' in query for query in self.queries))

    def test_wrong_user_sender_executable_boot_or_partial_kill_never_claim_oom(self):
        bad_user = [{**self.user, key: value} for key, value in
                    [('_UID', '1001'), ('_COMM', 'a-browser'), ('_BOOT_ID', 'previous'),
                     ('_EXE', '/usr/bin/spoofed-systemd'), ('MESSAGE_ID', 'd989611b15e44c9dbf31e3c81256e4ed'),
                     ('UNIT_RESULT', 'core-dump'), ('USER_UNIT', '/home/person/file'),
                     ('USER_UNIT', 'bad\nname.service')]]
        self.assertEqual(self.read({'user': bad_user}), [])

    def test_unreadable_missing_cursor_and_flood_are_bounded(self):
        self.assertEqual(self.read({}), [])
        self.assertEqual(self.read({'user': [{**self.user, '__CURSOR': '', 'USER_INVOCATION_ID': ''}]}), [])
        records = [{**self.user, '__CURSOR': str(index), 'USER_INVOCATION_ID': str(index)}
                   for index in range(256)]
        self.assertEqual(len(self.read({'user': records})), 256)
        self.assertLessEqual(len(json.loads(self.state['memory-seen'])), 256)
        with patch('luma_vitals.memory_feedback.subprocess.run', side_effect=subprocess.TimeoutExpired('journalctl', 3)):
            self.assertEqual(memory_query(['journalctl']), '')


class MemoryFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.notifier = SimpleNamespace(notify=lambda *args: self.calls.append(args) or True)
        self.time = 0
        self.feedback = MemoryFeedback(self.notifier, clock=lambda: self.time)

    def event(self, kind='out-of-memory', unit='app-gnome-test-1.scope'):
        return Event(kind, unit, 'evidence', {})

    def test_verified_kill_sources_coalesce_without_claiming_recovery(self):
        self.feedback.observe([self.event(), self.event()])
        self.assertEqual(len(self.calls), 1)
        self.assertIn('memory ran out', self.calls[0][0])
        self.assertNotIn('running again', self.calls[0][1])
        self.time = 15
        self.feedback.observe([self.event()])
        self.assertEqual(len(self.calls), 1)
        self.time = 121
        self.feedback.observe([self.event()])
        self.assertEqual(len(self.calls), 2)

    def test_pressure_warns_before_kill_and_other_crashes_do_not_get_oom_copy(self):
        self.feedback.observe([self.event('memory-pressure', 'machine')])
        self.assertEqual(self.calls[0][0], 'Memory is under pressure')
        self.feedback.observe([self.event('crash'), self.event('launch-failure'), self.event('firmware-crash-record')])
        self.assertEqual(len(self.calls), 1)
        self.feedback.observe([self.event(), self.event('memory-pressure', 'machine')])
        self.assertEqual(len(self.calls), 2)

    def test_sustained_pressure_has_bounded_notice_frequency(self):
        event = self.event('memory-pressure', 'machine')
        for self.time in range(0, 300, 15):
            self.feedback.observe([event])
        self.assertEqual(len(self.calls), 1)
        self.time = 300
        self.feedback.observe([event])
        self.assertEqual(len(self.calls), 2)
        self.time = 301
        self.feedback.observe([self.event()])
        self.time = 315
        self.feedback.observe([event])
        self.assertEqual(len(self.calls), 3)

    def test_pressure_endpoint_failure_is_not_retried_every_sample(self):
        attempts = []
        self.notifier.notify = lambda *args: attempts.append(args) or False
        for self.time in range(0, 60, 15):
            self.feedback.observe([self.event('memory-pressure', 'machine')])
        self.assertEqual(len(attempts), 1)
        self.time = 60
        self.feedback.observe([self.event('memory-pressure', 'machine')])
        self.assertEqual(len(attempts), 2)

    def test_temporary_notification_failure_retries_without_a_new_kill(self):
        self.notifier.notify = lambda *args: False
        self.feedback.observe([self.event()])
        self.assertEqual(len(self.feedback.pending), 1)
        self.notifier.notify = lambda *args: self.calls.append(args) or True
        self.feedback.observe([])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.feedback.pending, {})

    def test_many_kills_send_one_notification(self):
        self.feedback.observe([self.event(unit=f'app-gnome-app{index}-1.scope') for index in range(200)])
        self.assertEqual(len(self.calls), 1)
        self.assertLess(len(self.calls[0][1]), 200)


if __name__ == '__main__': unittest.main()
