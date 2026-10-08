# SPDX-License-Identifier: Apache-2.0
"""Real SQLite cache/adapter controls; no network or authorization mock claim."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from prairie_apps.collaboration import CollaborationCache, sync_notes_collaboration
from prairie_apps.tasks_collaboration import sync_tasks_collaboration


class Generation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {'HOME': self.tmp.name, 'XDG_DATA_HOME': self.tmp.name + '/data'}
        self.client = SimpleNamespace(address='https://owned.invalid', identity=SimpleNamespace(device_id='owned-device'))
        self.snapshot = {'id': 'owned-document', 'kind': 'list', 'revision': 0, 'role': 'owner',
                         'sync_generation': 7, 'content': {'title': 'Owned list', 'tasks': []},
                         'members': [], 'comments': []}
        self.document = {key: self.snapshot[key] for key in ('id', 'kind', 'revision', 'role', 'sync_generation')}
        self.document.update(accepted=True, owner='owned-account')
        self.cache = CollaborationCache(self.env)
        self.addCleanup(self.cache.close)
        self.cache.remember(self.client, self.snapshot, 'local-owned')

    def test_only_exact_fresh_generation_role_revision_kind_and_context_reuse(self):
        self.assertEqual(self.cache.unchanged_snapshot(self.client, self.document)['content'], self.snapshot['content'])
        for key, value in [('sync_generation', 8), ('role', 'view'), ('revision', 1), ('kind', 'task')]:
            self.assertIsNone(self.cache.unchanged_snapshot(self.client, self.document | {key: value}))
        for generation in (None, True, -1, 1.0, 9007199254740992):
            self.assertIsNone(self.cache.unchanged_snapshot(self.client, self.document | {'sync_generation': generation}))
        for client in (SimpleNamespace(address='https://foreign.invalid', identity=self.client.identity),
                       SimpleNamespace(address=self.client.address, identity=SimpleNamespace(device_id='foreign-device'))):
            self.assertIsNone(self.cache.unchanged_snapshot(client, self.document))

    def test_reuse_does_not_extend_full_snapshot_age_or_hide_corrupt_cache(self):
        cached = self.cache.unchanged_snapshot(self.client, self.document)
        stamp = cached['_sync_received_unix']
        with patch('prairie_apps.collaboration.time.time', return_value=stamp + 30):
            self.cache.remember(self.client, cached, 'local-owned')
        with patch('prairie_apps.collaboration.time.time', return_value=stamp + 61):
            self.assertIsNone(self.cache.unchanged_snapshot(self.client, self.document))
        with patch('prairie_apps.collaboration.time.time', return_value=stamp - 1):
            self.assertIsNone(self.cache.unchanged_snapshot(self.client, self.document))
        with self.cache.db:
            self.cache.db.execute('UPDATE snapshots SET snapshot=?', ('not JSON',))
        self.assertIsNone(self.cache.unchanged_snapshot(self.client, self.document))

    def test_tasks_fresh_listing_skips_unchanged_history_but_fetches_comment_and_role(self):
        reads = []
        self.client.documents = lambda: [copy.deepcopy(self.document)]
        self.client.read = lambda document: (reads.append(document), copy.deepcopy(self.snapshot))[1]
        with patch('prairie_apps.collaboration_ownership.profile_ready', return_value=True), \
             patch('prairie_apps.app_installs.app_environment', side_effect=lambda _app, env: env), \
             patch('prairie_apps.tasks_collaboration.CollaborationClient', return_value=self.client):
            sync_tasks_collaboration(environment=self.env)
            self.assertEqual(reads, [])
            self.snapshot['sync_generation'] = self.document['sync_generation'] = 8
            self.snapshot['comments'] = [{'id': 'owned-comment', 'body': 'Delivered', 'account': 'owned-peer', 'created_at': 1}]
            sync_tasks_collaboration(environment=self.env)
            self.assertEqual(reads, ['owned-document'])
            self.snapshot['sync_generation'] = self.document['sync_generation'] = 9
            self.snapshot['role'] = self.document['role'] = 'view'
            sync_tasks_collaboration(environment=self.env)
            self.assertEqual(reads, ['owned-document', 'owned-document'])
            self.client.documents = lambda: []
            sync_tasks_collaboration(environment=self.env)
        self.assertEqual(self.cache.db.execute('SELECT role FROM documents').fetchone()[0], 'revoked')

    def test_legacy_hub_never_skips_and_each_mutation_remains_real_transport(self):
        del self.document['sync_generation']
        reads = []
        self.client.documents = lambda: [self.document]
        self.client.read = lambda document: (reads.append(document), copy.deepcopy(self.snapshot))[1]
        with patch('prairie_apps.collaboration_ownership.profile_ready', return_value=True), \
             patch('prairie_apps.app_installs.app_environment', side_effect=lambda _app, env: env), \
             patch('prairie_apps.tasks_collaboration.CollaborationClient', return_value=self.client):
            sync_tasks_collaboration(environment=self.env)
            sync_tasks_collaboration(environment=self.env)
        self.assertEqual(reads, ['owned-document', 'owned-document'])

    def test_notes_unchanged_history_reuses_content_and_unsent_edit_still_uses_cas(self):
        from prairie_apps.notes_backend import NotesStore
        path = Path(self.tmp.name) / 'notes.sqlite3'
        store = NotesStore(path)
        note = store.create_note(title='Owned note', body='Original')
        snapshot = self.snapshot | {'id': 'owned-note', 'kind': 'note', 'content': {'title': 'Owned note', 'body': 'Original', 'runs': []}}
        self.cache.remember(self.client, snapshot, note.id)
        doc = self.document | {'id': 'owned-note', 'kind': 'note'}
        reads, commits = [], []
        self.client.documents = lambda: [doc]
        self.client.read = lambda document: (reads.append(document), copy.deepcopy(snapshot))[1]
        def operation(document, action, data):
            commits.append((action, data))
            return snapshot | {'content': data['content'], 'revision': 1, 'sync_generation': 8}
        self.client.operation = operation
        with patch('prairie_apps.collaboration_ownership.profile_ready', return_value=True), \
             patch('prairie_apps.app_installs.app_environment', side_effect=lambda _app, env: env), \
             patch('prairie_apps.collaboration.CollaborationClient', return_value=self.client):
            sync_notes_collaboration(environment=self.env, store_path=path)
            self.assertEqual(reads, [])
            store.update_note(note.id, title=note.title, body='Actual unsent edit', expected=note)
            sync_notes_collaboration(environment=self.env, store_path=path)
        store.close()
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0][0], 'content')
        self.assertEqual(commits[0][1]['revision'], 0)
        self.assertEqual(commits[0][1]['content']['body'], 'Actual unsent edit')


if __name__ == '__main__':
    unittest.main(verbosity=2)
