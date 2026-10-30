# SPDX-License-Identifier: Apache-2.0
"""Connect exchanges against Tide's real SQLite model and credential class.

Only libsecret storage and the Hub transport are in-memory fixtures. Credential
helper requests execute the production helper, including Tide's schema migration;
model writes run the actual subprocess and its real LibraryStore migrations.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from luma_tide.credentials import LEGACY_SCHEMA_NAME, SCHEMA_NAME, SecretServiceStore
from luma_tide.model import LibraryStore, SourceState
from prairie_apps import connect_tide as tide
from prairie_apps import connect_sync


class SecretError(Exception):
    pass


class Vault:
    SchemaFlags = types.SimpleNamespace(NONE=0)
    SchemaAttributeType = types.SimpleNamespace(STRING=0)
    COLLECTION_DEFAULT = 'default'
    Schema = types.SimpleNamespace(new=lambda name, *_: name)
    ServiceFlags = types.SimpleNamespace(OPEN_SESSION=1)
    SearchFlags = types.SimpleNamespace(ALL=1)

    def __init__(self):
        self.values = {}
        self.locked = False
        self.locked_keys = set()
        self.Service = types.SimpleNamespace(get_sync=lambda *_: self)

    def search_sync(self, schema, attributes, flags, _cancellable):
        assert flags == self.SearchFlags.ALL, 'background reads must never request unlock/load'
        key = schema, attributes['reference']
        if key not in self.values:
            return []
        return [types.SimpleNamespace(get_locked=lambda: self.locked or key in self.locked_keys,
            load_secret_sync=lambda _: None,
            get_secret=lambda: types.SimpleNamespace(get_text=lambda: self.values[key]))]

    def password_lookup_sync(self, schema, attributes, *_):
        if self.locked:
            raise SecretError('private-provider-detail')
        return self.values.get((schema, attributes['reference']))

    def password_store_sync(self, schema, attributes, _collection, _label, secret, *_):
        if self.locked:
            raise SecretError('private-provider-detail')
        self.values[(schema, attributes['reference'])] = secret
        return True

    def password_clear_sync(self, schema, attributes, *_):
        self.values.pop((schema, attributes['reference']), None)


class Hub:
    def __init__(self):
        self.items, self.secrets = {}, {}
        self.revision = 0

    def get_json(self, url, **_kwargs):
        if '/collections/' in url:
            return {'revision': self.revision, 'items': copy.deepcopy(list(self.items.values()))}
        if url.endswith('/secrets'):
            return {'items': [{'ref': ref, 'updated_by': row[1]} for ref, row in self.secrets.items()]}
        ref = url.rsplit('/', 1)[1]
        if ref not in self.secrets:
            raise connect_sync.HubResponseError(404, 'Not found')
        return {'secret': self.secrets[ref][0]}

    def post_json(self, url, payload, **kwargs):
        for change in payload['changes']:
            uid = change['uid']
            if change['op'] == 'delete':
                self.items.pop(uid, None)
            else:
                self.items[uid] = {'uid': uid, 'data': copy.deepcopy(change['data'])}
        self.revision += 1
        return self.get_json(url, **kwargs)

    def put_json(self, url, payload, *, token):
        self.secrets[url.rsplit('/', 1)[1]] = payload['secret'], token


class Integration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hub, self.states, self.vaults = Hub(), {}, {}
        self.environments = {}
        for name in ('phone', 'tablet'):
            home = self.root / name
            code = str(home) + os.pathsep + str(Path(__file__).resolve().parents[2] / 'src/luma-tide')
            self.environments[name] = {'HOME': str(home), 'XDG_DATA_HOME': str(home / 'data'),
                'LUMA_TIDE_LIBRARY': str(home / 'data/luma-tide/library.db'), 'LUMA_TIDE_PYTHONPATH': code}
            self.states[name] = {}
            self.vaults[code] = Vault()
        self.real_run = subprocess.run
        self.run_patch = patch.object(tide.subprocess, 'run', self.run_process)
        self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def run_process(self, command, **kwargs):
        sandbox = command[0] == 'flatpak'
        if sandbox:
            self.assertEqual(command[:4], ['flatpak', 'run', '--command=python3', 'org.projectluma.Tide'])
            self.assertNotIn('--filesystem=host', command)
            self.assertTrue(json.loads(kwargs['input'])['sandbox'])
        if command[-1] != tide._KEYRING_SCRIPT:
            if sandbox:
                env = dict(kwargs['env'])
                env['XDG_DATA_HOME'] = str(self.database(self.sandbox_device).parent.parent)
                kwargs['env'] = env
                command = [sys.executable, '-c', command[-1]]
            return self.real_run(command, **kwargs)
        # Run the actual bridge and actual SecretServiceStore with an isolated
        # schema-aware libsecret backend for this simulated enrolled device.
        if sandbox:
            vault = self.vault(self.sandbox_device)
        else:
            code = next(code for code in self.vaults if kwargs['env']['PYTHONPATH'].startswith(code))
            vault = self.vaults[code]
        gi, repository = types.ModuleType('gi'), types.ModuleType('gi.repository')
        gi.require_version = lambda *_: None
        repository.GLib, repository.Secret = types.SimpleNamespace(Error=SecretError), vault
        output, errors, status = io.StringIO(), io.StringIO(), 0
        with patch.dict(sys.modules, {'gi': gi, 'gi.repository': repository}), \
                patch.object(sys, 'stdin', io.StringIO(kwargs['input'])), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors), \
                (patch.object(SecretServiceStore, 'lookup_background', None) if sandbox else contextlib.nullcontext()):
            try:
                exec(command[-1], {})
            except SystemExit as error:
                status = error.code
        return subprocess.CompletedProcess(command, status, output.getvalue(), errors.getvalue())

    def env(self, name):
        return self.environments[name]

    def database(self, name):
        return Path(self.env(name)['LUMA_TIDE_LIBRARY'])

    def vault(self, name):
        return self.vaults[self.env(name)['LUMA_TIDE_PYTHONPATH']]

    def sync(self, name, remote_changed=True):
        return tide.sync_tide(address='https://hub.test', token=name, http=self.hub,
            state=self.states[name], scope='https://hub.test|' + name,
            environment=self.env(name), remote_changed=remote_changed)

    def seed(self, schema=SCHEMA_NAME):
        with LibraryStore(self.database('phone')) as store:
            source = store.add_source('Music', 'subsonic', 'https://music.example.test', local=False,
                account='listener', auth_ref='secret-service:opaque-reference')
        self.vault('phone').values[schema, source.auth_ref] = 'synthetic-password'
        return source

    def test_two_devices_receive_sources_and_current_credentials_without_opening_tide(self):
        source = self.seed()
        self.sync('phone')
        self.assertFalse(self.database('tablet').exists())
        self.sync('tablet')
        with LibraryStore(self.database('tablet')) as store:
            received = store.source(source.id)
            self.assertEqual(received.account, 'listener')
        self.assertEqual(self.vault('tablet').values[SCHEMA_NAME, received.auth_ref], 'synthetic-password')
        # A remote rename must retain the originating device's opaque auth ref.
        self.hub.items[source.id]['data']['name'] = 'Renamed'
        self.hub.revision += 1
        self.sync('phone')
        with LibraryStore(self.database('phone')) as store:
            self.assertEqual(store.source(source.id).auth_ref, source.auth_ref)
        # Lost local credentials can be restored even without a new Hub revision.
        self.vault('tablet').values.clear()
        self.sync('tablet', remote_changed=False)
        self.assertEqual(self.vault('tablet').values[SCHEMA_NAME, received.auth_ref], 'synthetic-password')

    def test_legacy_sign_in_remains_usable_and_deletion_preserves_files(self):
        source = self.seed(LEGACY_SCHEMA_NAME)
        self.sync('phone')
        self.assertEqual(self.vault('phone').values[LEGACY_SCHEMA_NAME, source.auth_ref], 'synthetic-password')
        self.assertEqual(self.hub.secrets[tide.secret_ref(source.id)][0], 'synthetic-password')
        self.sync('tablet')
        download = self.database('tablet').parent / 'download.flac'
        download.write_bytes(b'synthetic-media')
        with LibraryStore(self.database('phone')) as store:
            store.remove_source(source.id)
        self.sync('phone'); self.sync('tablet')
        with LibraryStore(self.database('tablet')) as store:
            self.assertEqual(store.sources(), [])
        self.assertTrue(download.exists())
        self.assertFalse(self.vault('tablet').values)

    def test_flatpak_receiver_uses_sandbox_model_and_legacy_credential_api(self):
        source = self.seed(); self.sync('phone')
        env = self.env('tablet')
        native = Path(env['XDG_DATA_HOME']) / 'luma-tide/library.db'
        with LibraryStore(native) as store:
            store.add_source('Local only', 'local-folder', self.root / 'music', local=True)
        sandbox_home = Path(env['HOME']) / '.var/app/org.projectluma.Tide/data'
        sandbox_home.mkdir(parents=True)
        env['LUMA_TIDE_LIBRARY'] = str(sandbox_home / 'luma-tide/library.db')
        self.sandbox_device = 'tablet'
        from prairie_apps.app_installs import FLATPAK
        installation = types.SimpleNamespace(kind=FLATPAK, data_home=sandbox_home)
        selected_env = {k: v for k, v in env.items() if not k.startswith('LUMA_TIDE_')}
        with patch('prairie_apps.app_installs.resolve', return_value=installation):
            tide.sync_tide(address='https://hub.test', token='tablet', http=self.hub,
                state=self.states['tablet'], scope='https://hub.test|tablet',
                environment=selected_env, remote_changed=True)
        with LibraryStore(self.database('tablet')) as store:
            received = store.source(source.id)
        self.assertEqual(self.vault('tablet').values[SCHEMA_NAME, received.auth_ref], 'synthetic-password')
        with LibraryStore(native) as store:
            self.assertTrue(all(s.local for s in store.sources()))

    def test_metadata_arriving_before_password_is_retried_without_another_revision(self):
        source = self.seed(); self.sync('phone')
        saved = self.hub.secrets.pop(tide.secret_ref(source.id))
        with self.assertRaisesRegex(tide.TideSyncError, 'saved sign-in'):
            self.sync('tablet')
        self.assertTrue(self.database('tablet').exists())
        self.hub.secrets[tide.secret_ref(source.id)] = saved
        self.sync('tablet', remote_changed=False)
        self.assertTrue(self.vault('tablet').values)

    def test_locked_keyring_reports_failure_without_leaking_provider_detail(self):
        self.seed(); self.vault('phone').locked = True
        with self.assertRaises(tide.TideSyncError) as caught:
            self.sync('phone')
        self.assertNotIn('private-provider-detail', str(caught.exception))
        self.assertFalse(self.hub.secrets)

    def test_locked_current_sign_in_never_falls_back_to_stale_preview_password(self):
        source = self.seed()
        vault = self.vault('phone')
        vault.values[LEGACY_SCHEMA_NAME, source.auth_ref] = 'stale-synthetic-password'
        vault.locked_keys.add((SCHEMA_NAME, source.auth_ref))
        with self.assertRaises(tide.TideSyncError):
            self.sync('phone')
        self.assertFalse(self.hub.secrets)

    def test_source_marker_changes_for_server_edits_and_not_playback_state(self):
        source = self.seed()
        marker = tide.source_marker(self.env('phone'))
        first = marker.stat().st_mtime_ns
        with LibraryStore(self.database('phone')) as store:
            store.set_source_state(source.id, SourceState.OFFLINE)
            self.assertEqual(marker.stat().st_mtime_ns, first)
            # Make the marker older so timestamp resolution cannot hide the edit.
            os.utime(marker, ns=(1, 1))
            store.add_source('Renamed', source.kind, source.uri, local=False, source_id=source.id)
            self.assertGreater(marker.stat().st_mtime_ns, 1)
        self.assertGreater(connect_sync.source_stamp(self.env('phone')), 1)

    def test_native_library_wins_over_newer_retained_preview(self):
        self.seed()
        env = {k: v for k, v in self.env('phone').items() if not k.startswith('LUMA_TIDE_')}
        preview = Path(env['XDG_DATA_HOME']) / 'luma-dev/preview'
        (preview / 'src/luma_tide').mkdir(parents=True)
        (preview / 'src/luma_tide/model.py').touch()
        with LibraryStore(preview / 'data/luma-tide/library.db'):
            pass
        self.assertEqual(tide.library(env), (self.database('phone'), ''))


if __name__ == '__main__':
    unittest.main(verbosity=2)
