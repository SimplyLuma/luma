#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Live cursor and service-isolation regressions; no network or real profile."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

os.environ['PRAIRIE_EDS_MODE'] = 'disabled'
from prairie_apps import connect_sync as sync
from prairie_apps.connect_calendar import CalendarSyncError


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.env = {'HOME': str(root), 'XDG_DATA_HOME': str(root / 'data')}
        self.identity = sync.DeviceIdentity('11111111-2222-4333-8444-555555555555', 'private-fixture-token',
                                            'https://hub.example', 'Fixture', 'now')
        sync.save_identity(self.identity, self.env)
        self.scope = f'{self.identity.hub}|{self.identity.device_id}'
        self.sleeps, self.events, self.updates = [], [], []

    def run_watch(self, http, rounds):
        with contextlib.redirect_stderr(io.StringIO()):
            sync.watch(environment=self.env, http=http, out=io.StringIO(),
                       sleep=lambda value: self.sleeps.append(value) or False, rounds=rounds)

    def seams(self, calendar=None, notes=None):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        enabled = {'notes': True, 'calendar': True, 'contacts': False, 'photos': False,
                   'world-clocks': False, 'weather-places': False, 'messages': False,
                   'calls': False, 'tide-sources': False, 'leaf-books': False}
        stack.enter_context(mock.patch('prairie_apps.connect_services.enabled_services', return_value=enabled))
        stack.enter_context(mock.patch('prairie_apps.collaboration_ownership.profile_ready', return_value=True))
        stack.enter_context(mock.patch('prairie_apps.collaboration.sync_notes_collaboration',
                                       side_effect=notes or (lambda **kw: self.updates.append('note') or {'updated': 1, 'conflicts': 0})))
        stack.enter_context(mock.patch('prairie_apps.tasks_collaboration.sync_tasks_collaboration', return_value=0))
        delta = mock.Mock()
        delta.supported.return_value = True
        delta.run.return_value = 'notes: unchanged'
        delta.revision = None
        delta.record = {'notes': {}}
        stack.enter_context(mock.patch('prairie_apps.connect_notes.NotesDeltaSync', return_value=delta))
        cal = stack.enter_context(mock.patch('prairie_apps.connect_calendar.sync_calendar',
                                              side_effect=calendar or CalendarSyncError('Calendar password refused')))
        return cal

    def client(self, replies):
        parent = self
        class Client:
            def get_json(self, url, **kw):
                parent.events.append(url)
                return next(replies)
        return Client()

    def test_calendar_failure_does_not_delay_live_edit_or_revoke(self):
        cal = self.seams()
        http = self.client(iter([{'revision': 5}, {'revision': 6, 'services': ['collaboration']},
                                 {'revision': 7, 'services': ['collaboration']}]))
        self.run_watch(http, 3)
        self.assertEqual(len(self.updates), 3)
        self.assertEqual(cal.call_count, 1, 'document events never retry DAV first')
        self.assertEqual(self.sleeps, [0.25, 0.25], 'no exponential whole-account quiet')
        self.assertTrue(self.events[1].endswith('after=5'))
        self.assertTrue(self.events[2].endswith('after=6'))
        self.assertNotIn(self.scope + '|revision', sync._load_state(self.env), 'failed DAV is not marked complete')
        self.assertEqual(sync._load_state(self.env)[self.scope + '|collaboration-revision'], 7)

    def test_failed_service_still_retries_at_idle_after_live_success(self):
        cal = self.seams()
        http = self.client(iter([{'revision': 5}, {'revision': 6, 'services': ['collaboration']},
                                 {'revision': 6, 'services': []}]))
        self.run_watch(http, 3)
        self.assertEqual(cal.call_count, 2)
        self.assertEqual(len(self.updates), 3)
        self.assertEqual(self.sleeps, [0.25, 0.25])

    def test_successful_idle_retry_clears_pending_service(self):
        cal = self.seams(calendar=[CalendarSyncError('refused'), 'calendar: successful'])
        http = self.client(iter([{'revision': 5}, {'revision': 5, 'services': []}, {'revision': 5, 'services': []}]))
        self.run_watch(http, 3)
        self.assertEqual(cal.call_count, 2)
        self.assertEqual(len(self.updates), 2, 'idle success does not become a polling loop')
        self.assertEqual(sync._load_state(self.env)[self.scope + '|revision'], 5)

    def test_capture_precedes_document_pull_so_mid_pull_event_is_not_lost(self):
        self.seams(calendar=lambda **kw: 'calendar: successful')
        class Client:
            revision = 5
            def get_json(inner, url, **kw):
                self.events.append((url, inner.revision))
                return {'revision': inner.revision, 'services': ['collaboration']}
        http = Client()
        def changed_during_pull(**kw):
            self.assertEqual(self.events[-1][1], 5)
            http.revision = 6
            return {'updated': 1, 'conflicts': 0}
        with mock.patch('prairie_apps.collaboration.sync_notes_collaboration', side_effect=changed_during_pull):
            sync.push(environment=self.env, http=http, out=io.StringIO())
        self.assertEqual(sync._load_state(self.env)[self.scope + '|revision'], 5)
        self.assertEqual(len(self.events), 1, 'no late read that acknowledges unseen event 6')
        self.assertEqual(http.revision, 6)

    def test_rate_limit_in_document_pull_preserves_real_retry_after(self):
        cal = self.seams(notes=sync.HubResponseError(429, 'Busy', retry_after=90))
        http = self.client(iter([{'revision': 5}]))
        with mock.patch.object(sync.time, 'time', return_value=1000):
            self.run_watch(http, 1)
        self.assertEqual(cal.call_count, 0)
        self.assertEqual(self.sleeps, [90])
        self.assertEqual(sync._load_state(self.env)[self.scope + '|backoff-until'], 1090)

    def test_revoked_device_fails_hard_instead_of_advancing_cursor(self):
        cal = self.seams(notes=sync.HubResponseError(401, 'Revoked'))
        http = self.client(iter([{'revision': 5}]))
        with self.assertRaises(sync.AuthorisationError):
            self.run_watch(http, 1)
        self.assertEqual(cal.call_count, 0)
        self.assertEqual(self.sleeps, [])

    def test_partial_without_authenticated_revision_keeps_failure_backoff(self):
        def failed(**kw):
            raise sync.PartialSyncError('No trusted event cursor', None)
        with mock.patch.object(sync, 'push', side_effect=failed):
            self.run_watch(self.client(iter([])), 3)
        self.assertEqual(self.sleeps, [5, 10, 20])

    def test_legacy_event_without_service_hint_runs_all_services(self):
        cal = self.seams()
        self.run_watch(self.client(iter([{'revision': 5}, {'revision': 6}])), 2)
        self.assertEqual(cal.call_count, 2)

    def test_invalid_cursor_never_overwrites_a_checkpoint(self):
        for value in (-1, True, '7', None):
            with self.subTest(value=value), self.assertRaises(sync.ConnectError):
                sync.push(environment=self.env, observed_revision=value, http=object(),
                          collaboration_only=True, transport=lambda *a, **kw: None)
        self.assertEqual(sync._load_state(self.env), {})

    def test_live_path_rejects_stub_and_dry_run_transports(self):
        with self.assertRaises(sync.ConnectError):
            sync.push(environment=self.env, collaboration_only=True, dry_run=True)
        with self.assertRaises(sync.ConnectError):
            sync.push(environment=self.env, collaboration_only=True, transport=lambda *a, **kw: None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
