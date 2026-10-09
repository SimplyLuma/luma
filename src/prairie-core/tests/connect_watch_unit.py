#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Live cursor and service isolation using a loopback Hub and private profiles."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlsplit

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
        class Client:
            def get_json(inner, url, **kw):
                self.events.append(url)
                if url.endswith('/account'):
                    raise sync.HubResponseError(401, 'Revoked')
                return {'revision': 5}
        http = Client()
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


class SharingAccessTests(unittest.TestCase):
    """Real HTTP denials must not turn a valid registration into a logout."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {'HOME': str(self.root/'home'), 'XDG_DATA_HOME': str(self.root/'data'),
                    'XDG_DATA_DIRS': str(self.root/'system-data')}
        self.requests, self.posts = [], []
        self.account_status = self.events_status = self.personal_status = 200
        self.shared_status = 401
        self.notes_ready = self.tasks_ready = True
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass  # Never log an Authorization header or a fixture's contents.

            def reply(self, status, value):
                encoded = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def authorized(self):
                parent.requests.append((self.command, urlsplit(self.path).path))
                if self.headers.get('Authorization') != 'Bearer isolated-fixture-token':
                    self.reply(401, {'error': 'Invalid fixture registration'})
                    return False
                return True

            def do_GET(self):
                if not self.authorized():
                    return
                path = urlsplit(self.path).path
                if path.endswith('/events'):
                    self.reply(parent.events_status, {'revision': 5})
                elif path.endswith('/account'):
                    self.reply(parent.account_status, {'account': {'name': 'Fixture'},
                        'overview': {'devices': []}})
                elif path.endswith('/collaboration/documents'):
                    self.reply(parent.shared_status, {'error': 'Shared documents unavailable'})
                elif path.endswith('/capabilities'):
                    self.reply(200, {'notes_delta': 0, 'notes_blobs': 0})
                else:
                    self.reply(404, {'error': 'Unexpected fixture route'})

            def do_POST(self):
                if not self.authorized():
                    return
                path = urlsplit(self.path).path
                if path not in ('/api/hub/sync/notes', '/api/hub/sync/contacts'):
                    self.reply(405, {'error': 'Shared writes must remain refused'})
                    return
                value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                parent.posts.append((path, value))
                self.reply(parent.personal_status, {'accepted': len(value['items']), 'deleted': 0})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.identity = sync.DeviceIdentity('isolated-device', 'isolated-fixture-token',
            f'http://127.0.0.1:{self.server.server_port}', 'Fixture', 'now')
        sync.save_identity(self.identity, self.env)
        self.identity_bytes = sync.device_file(self.env).read_bytes()
        self.scope = f'{self.identity.hub}|{self.identity.device_id}'
        from prairie_apps.notes_backend import NotesStore
        from prairie_apps.eds_backend import ContactRecord
        self.contact = ContactRecord('owned-contact', 'Fixture')
        self.notes_path = self.root/'notes.sqlite3'
        with contextlib.closing(NotesStore(self.notes_path)) as store:
            self.note = store.create_note(title='Owned page', body='Keep this local content')
        self.notes_bytes = self.notes_path.read_bytes()
        sync._save_state({self.scope+'|revision': 2, self.scope+'|collaboration-revision': 2}, self.env)
        self.enabled = {'notes': True, 'contacts': True, 'calendar': False, 'photos': False,
            'world-clocks': False, 'weather-places': False, 'messages': False, 'calls': False,
            'tide-sources': False, 'leaf-books': False}
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch('prairie_apps.connect_services.enabled_services',
            side_effect=lambda _directory: self.enabled.copy()))
        stack.enter_context(mock.patch('prairie_apps.collaboration_ownership.profile_ready',
            side_effect=lambda app, _environment=None: self.notes_ready
                if app == 'org.projectluma.Notes' else self.tasks_ready))
        # EDS is outside this authentication fixture. Both personal snapshot
        # POSTs still travel through the real production urllib transport.
        stack.enter_context(mock.patch('prairie_apps.connect_contacts.sync_contacts',
            return_value='contacts: connected'))

    def close_server(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def push(self, **kwargs):
        return sync.push(environment=self.env, notes_path=self.notes_path,
            contacts_loader=lambda: (self.contact,), out=io.StringIO(), **kwargs)

    def unchanged_local_data(self):
        self.assertEqual(sync.device_file(self.env).read_bytes(), self.identity_bytes)
        self.assertEqual(self.notes_path.read_bytes(), self.notes_bytes)
        self.assertEqual(sync.load_identity(self.env), self.identity)
        self.assertEqual(sync.device_file(self.env).stat().st_mode & 0o777, 0o600)

    def test_notes_and_tasks_denied_but_personal_snapshots_are_accepted(self):
        with self.assertRaises(sync.PartialSyncError) as caught:
            self.push()
        self.assertNotIsInstance(caught.exception, sync.AuthorisationError)
        self.assertEqual(caught.exception.observed_revision, 5)
        self.assertIn('Shared Notes', str(caught.exception))
        self.assertIn('still connected', str(caught.exception))
        self.assertNotIn('Re-enrol', str(caught.exception))
        self.assertEqual(self.requests.count(('GET', '/api/hub/sync/account')), 2)
        self.assertEqual(self.requests.count(('GET', '/api/hub/sync/collaboration/documents')), 2)
        self.assertEqual([path for path, _body in self.posts],
            ['/api/hub/sync/contacts', '/api/hub/sync/notes'])
        self.assertEqual(self.posts[1][1]['items'][0]['id'], self.note.id)
        self.assertFalse(any(method == 'POST' and '/collaboration/' in path
            for method, path in self.requests))
        state = sync._load_state(self.env)
        self.assertEqual(state[self.scope+'|notes']['count'], 1)
        self.assertIn(self.scope+'|contacts', state)
        self.assertEqual(state[self.scope+'|revision'], 2)
        self.assertEqual(state[self.scope+'|collaboration-revision'], 2)
        self.unchanged_local_data()

    def test_tasks_denial_alone_does_not_require_reconnection(self):
        self.notes_ready = False
        with self.assertRaises(sync.PartialSyncError) as caught:
            self.push()
        self.assertIn('Shared Tasks', str(caught.exception))
        self.assertEqual(self.requests.count(('GET', '/api/hub/sync/account')), 1)
        self.assertEqual([path for path, _body in self.posts], ['/api/hub/sync/contacts'])
        self.assertEqual(sync._load_state(self.env)[self.scope+'|collaboration-revision'], 2)
        self.unchanged_local_data()

    def test_collaboration_only_failure_never_stamps_success_or_sends_personal_data(self):
        with self.assertRaises(sync.PartialSyncError):
            self.push(collaboration_only=True, observed_revision=5)
        self.assertEqual(self.posts, [])
        self.assertEqual(sync._load_state(self.env),
            {self.scope+'|revision': 2, self.scope+'|collaboration-revision': 2})
        self.unchanged_local_data()

    def test_account_revoked_after_event_still_fails_hard(self):
        self.account_status = 401
        with self.assertRaises(sync.AuthorisationError):
            self.push()
        self.assertEqual(self.posts, [])
        self.assertEqual(self.requests.count(('GET', '/api/hub/sync/account')), 1)
        self.unchanged_local_data()

    def test_unavailable_account_check_is_not_claimed_as_connected(self):
        self.account_status = 503
        with self.assertRaises(sync.ConnectError) as caught:
            self.push()
        self.assertNotIsInstance(caught.exception, sync.AuthorisationError)
        self.assertNotIn('still connected', str(caught.exception))
        self.assertEqual(self.posts, [])
        self.unchanged_local_data()

    def test_required_events_or_personal_snapshot_denial_still_revokes_authority(self):
        for stage in ('events', 'personal'):
            with self.subTest(stage=stage):
                self.events_status = 401 if stage == 'events' else 200
                self.personal_status = 401 if stage == 'personal' else 200
                with self.assertRaises(sync.AuthorisationError):
                    self.push()
                self.unchanged_local_data()

    def test_cli_partial_denial_exits_retry_not_reenrol(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.env), \
             mock.patch.object(sync, 'notes_store_path', return_value=self.notes_path), \
             mock.patch.object(sync, 'load_contacts', return_value=(self.contact,)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sync.main(['push'])
        self.assertEqual(code, sync.EXIT_RETRY)
        self.assertIn('Shared Notes', err.getvalue())
        self.assertIn('still connected', err.getvalue())
        self.assertNotIn('Re-enrol', err.getvalue())
        self.assertNotIn(self.identity.token, out.getvalue()+err.getvalue())
        self.unchanged_local_data()


if __name__ == '__main__':
    unittest.main(verbosity=2)
