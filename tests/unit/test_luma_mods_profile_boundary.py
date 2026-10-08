#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Host bridge checks against real declarative state, catalog and lifecycle.

These focused source gates do not substitute for installed signed-caller proof.
"""
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src/luma-mods'))
from luma_mods.errors import LumaModsError
from luma_mods.lifecycle import PreferenceLifecycle
from luma_mods.paths import UserPaths
from luma_mods.profile import FilePreferenceBackend
from luma_mods.profile_host import MAX_INPUT, MAX_OUTPUT, Profiles, decode, encode
from luma_mods.registry import SUPPORTED_PREFERENCE_DOMAINS
from luma_mods.resolver import resolve_mod
from luma_mods.runtime import UserRuntime, load_catalog, profile_for
from luma_mods.state import StateStore

MOD = 'org.projectluma.mod.green-dock'
DOMAIN = 'org.luma.shell.dock.appearance'

class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.backend = FilePreferenceBackend(root / 'preferences.json', set(SUPPORTED_PREFERENCE_DOMAINS))
        self.original = {DOMAIN: {'surface': 'prairie-default'}}
        self.backend._write(self.original)
        store = StateStore(root / 'state')
        self.runtime = UserRuntime(UserPaths(root / 'state', self.backend.path), store,
                                   PreferenceLifecycle(store, self.backend))
        self.catalog_root, self.catalog = load_catalog(ROOT / 'src/luma-mods/catalog')
        self.bridge = Profiles(self.runtime, lambda: (self.catalog_root, self.catalog, None))

    def request(self, operation='install', generation=0):
        plan = resolve_mod(MOD, self.catalog, self.runtime.host_context('desktop'))
        profile = profile_for(self.catalog_root, MOD)
        return {'operation': operation, 'identifier': MOD, 'generation': generation,
                'presentation': 'desktop', 'composition': plan.composition_sha256,
                'host': plan.host_sha256, 'profile': profile.source_sha256,
                'snapshot': self.bridge._snapshot(self.catalog_root,self.catalog,None)['snapshot'],
                'confirmed_unverified': True}

    def test_native_roundtrip_restores_original_value_and_records_lifecycle(self):
        installed = self.bridge.dispatch(self.request())
        self.assertEqual(installed['generation'], 1)
        self.assertEqual(self.backend._read()[DOMAIN], {'surface': 'green-glow'})
        self.assertEqual(installed['installed'][MOD]['trust_level'], 'local-unverified')
        disabled = self.bridge.dispatch({'operation':'enable','identifier':MOD,'generation':1,'enabled':False})
        self.assertFalse(disabled['installed'][MOD]['enabled'])
        self.assertEqual(self.backend._read(), self.original)
        self.bridge.dispatch({'operation':'enable','identifier':MOD,'generation':2,'enabled':True})
        removed = self.bridge.dispatch({'operation':'remove','identifier':MOD,'generation':3})
        self.assertNotIn(MOD, removed['installed'])
        self.assertEqual(self.backend._read(), self.original)
        self.assertEqual([e['operation'] for e in removed['audit']], ['install','disable','enable','remove'])

    def test_replayed_generation_and_review_tampering_never_changes_state(self):
        initial = self.request()
        for field in ('composition','host','profile','snapshot'):
            with self.subTest(field=field):
                forged = dict(initial, **{field: '0'*64})
                with self.assertRaises(LumaModsError): self.bridge.dispatch(forged)
                self.assertEqual(self.backend._read(), self.original)
                self.assertEqual(self.runtime.store.read()['generation'], 0)
        self.bridge.dispatch(initial)
        after = self.runtime.store.read()
        with self.assertRaises(LumaModsError): self.bridge.dispatch(initial)
        self.assertEqual(self.runtime.store.read(), after)
        with self.assertRaises(LumaModsError):
            self.bridge.dispatch({'operation':'remove','identifier':MOD,'generation':0})
        self.assertEqual(self.runtime.store.read(), after)

    def test_dependency_removal_and_nonboolean_enable_preserve_installed_state(self):
        self.bridge.dispatch(self.request())
        before = self.runtime.store.read()
        with self.assertRaises(LumaModsError):
            self.bridge.dispatch({'operation':'remove','identifier':'org.projectluma.prairie.dock-style-api',
                                  'generation':1})
        for enabled in (0,1,'false',None,[]):
            with self.subTest(enabled=enabled):
                with self.assertRaises(LumaModsError):
                    self.bridge.dispatch({'operation':'enable','identifier':MOD,'generation':1,'enabled':enabled})
        self.assertEqual(self.runtime.store.read(),before)
        self.assertEqual(self.backend._read()[DOMAIN],{'surface':'green-glow'})

    def test_host_catalog_review_uses_snapshot_and_never_accepts_local_paths(self):
        snapshot = self.bridge.dispatch({'operation':'catalog'})
        self.assertIn(MOD,snapshot['manifests'])
        review = {'operation':'review','identifier':MOD,'presentation':'desktop','snapshot':snapshot['snapshot']}
        result = self.bridge.dispatch(review)
        self.assertEqual(result['plan']['target_id'],MOD)
        self.assertEqual(result['plan']['composition_sha256'],self.request()['composition'])
        with self.assertRaises(LumaModsError): self.bridge.dispatch(dict(review,snapshot='0'*64))
        with self.assertRaises(LumaModsError): self.bridge.dispatch(dict(review,path='/tmp/local-catalog'))
        self.assertEqual(self.runtime.store.read()['generation'],0)

    def test_catalog_snapshot_change_before_or_during_refresh_refuses_write(self):
        """Exercise real TufCatalogClient/TrustedCatalogRuntime at Updater seam.

        The byte provider is synthetic; this measures bridge freshness binding,
        not cryptographic TUF signature validation.
        """
        from luma_mods.catalog_update import TufCatalogClient
        from luma_mods.catalog_runtime import TrustedCatalogRuntime
        root = Path(self.directory.name) / 'tuf-transport'
        root.mkdir()
        bootstrap = root / 'root.json'
        bootstrap.write_text('{"test":"synthetic Updater seam"}')
        profile = (self.catalog_root / 'profiles' / (MOD+'.profile.json')).read_bytes()
        profile_sha = hashlib.sha256(profile).hexdigest()
        manifest = json.loads((self.catalog_root/'manifests'/(MOD+'.mod.json')).read_bytes())
        manifest['dependencies'] = []
        manifest['capabilities']['requires'] = []
        manifest['evidence']['source_digest'] = 'sha256:'+profile_sha
        manifest_path = 'mods/community/'+MOD+'/1.0.0/manifest.mod.json'
        evidence_path = 'evidence/community/sha256/'+profile_sha
        entry = {'id':MOD,'version':'1.0.0','manifest':manifest_path,'signature_bundles':[],
                 'evidence':[evidence_path],'payloads':[],'trust_role':'community'}
        payloads = {manifest_path:json.dumps(manifest).encode(), evidence_path:profile}
        def publish_index():
            sha = hashlib.sha256(json.dumps([entry],sort_keys=True,separators=(',',':')).encode()).hexdigest()
            payloads['catalog/index.json'] = json.dumps({'schema':'org.luma.mod-catalog/v0.1',
                'snapshot_id':'sha256:'+sha,'entries':[entry]}).encode()
        publish_index()
        mutation = [False]
        def alter_snapshot():
            extra = b'owned extra evidence changes catalog admission, not plan or preference bytes'
            path = 'evidence/community/sha256/'+hashlib.sha256(extra).hexdigest()
            entry['evidence'].append(path); payloads[path] = extra; publish_index()
        class ByteUpdater:
            def __init__(self, **kwargs): self.target_dir = Path(kwargs['target_dir'])
            def refresh(self):
                if mutation[0]: mutation[0] = False; alter_snapshot()
            def get_targetinfo(self, path):
                return SimpleNamespace(path=path,length=len(payloads[path])) if path in payloads else None
            def download_target(self, info):
                path = self.target_dir/info.path
                path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(payloads[info.path])
                return str(path)
        client = TufCatalogClient(bootstrap_root=bootstrap,cache_root=root/'cache',
            metadata_base_url='https://owned.invalid/metadata/',target_base_url='https://owned.invalid/targets/',
            updater_factory=ByteUpdater)
        trusted = TrustedCatalogRuntime(client,None)
        def loader():
            catalog = trusted.refresh()
            return trusted.snapshot.index_path.parent,catalog,trusted
        bridge = Profiles(self.runtime,loader)
        catalog_root, catalog, _ = loader()
        plan = trusted.plan(MOD,self.runtime.host_context('desktop')).plan
        request = dict(self.request(), composition=plan.composition_sha256, host=plan.host_sha256,
            profile=profile_sha, snapshot=bridge._snapshot(catalog_root,catalog,trusted)['snapshot'])
        # First refresh is part of catalog_loader; arm mutation only on the
        # subsequent prepare_transaction refresh to reach the second comparison.
        def loader_with_later_change():
            result = loader(); mutation[0] = True; return result
        bridge.catalog_loader = loader_with_later_change
        with self.assertRaisesRegex(LumaModsError,'while preparing'): bridge.dispatch(request)
        self.assertEqual(self.backend._read(),self.original)
        self.assertEqual(self.runtime.store.read()['generation'],0)
        bridge.catalog_loader = loader
        with self.assertRaisesRegex(LumaModsError,'after review'): bridge.dispatch(request)
        self.assertEqual(self.backend._read(),self.original)
        self.assertEqual(self.runtime.store.read()['generation'],0)

    def test_unverified_consent_and_client_auth_values_paths_are_rejected(self):
        request = self.request()
        with self.assertRaises(LumaModsError): self.bridge.dispatch(dict(request, confirmed_unverified=False))
        for field, value in [('values',{DOMAIN:{'surface':'green-glow'}}),('path','/etc/passwd'),
                             ('authorization',{'catalog_verified':True}),('profile_path','/tmp/user.profile')]:
            with self.subTest(field=field):
                with self.assertRaises(LumaModsError): self.bridge.dispatch(dict(request, **{field:value}))
        self.assertEqual(self.backend._read(), self.original)
        self.assertEqual(self.runtime.store.read()['generation'], 0)

    def test_type_confusion_and_unsupported_operations_are_refused(self):
        for generation in (True, False, -1, 1.5, '0', 2**53+1):
            with self.subTest(generation=generation):
                with self.assertRaises(LumaModsError): self.bridge.dispatch(dict(self.request(),generation=generation))
        for operation in ('stage','activate','rollback','execute','write'):
            with self.assertRaises(LumaModsError): self.bridge.dispatch({'operation':operation})
        for value in ('yes',0,1,None):
            with self.assertRaises(LumaModsError): self.bridge.dispatch(dict(self.request(),confirmed_unverified=value))
        self.assertEqual(self.runtime.store.read()['generation'],0)

    def test_failed_native_apply_recovers_journal_and_consumes_generation(self):
        original_apply = self.backend.apply
        def interrupted(values):
            original_apply(values)
            raise OSError('owned injected interrupted native backend')
        with patch.object(self.backend, 'apply', side_effect=interrupted):
            with self.assertRaises(LumaModsError): self.bridge.dispatch(self.request())
        state = self.runtime.store.read()
        self.assertIsNone(state['pending_transaction'])
        self.assertEqual(state['installed'], {})
        self.assertEqual(state['generation'],1)
        self.assertEqual(state['audit'][-1]['result'],'recovered')
        self.assertEqual(self.backend._read(),self.original)
        self.assertEqual(self.bridge.dispatch({'operation':'recover'}),state)

    def test_json_duplicate_oversize_nonfinite_and_output_limits(self):
        for value in ('{"operation":"read","operation":"recover"}',
                      '{"operation":NaN}', '{"operation":Infinity}', '[]', '{',
                      '{"x":"' + 'x'*MAX_INPUT + '"}'):
            with self.assertRaises(LumaModsError): decode(value)
        with self.assertRaises(LumaModsError): encode({'large':'x'*MAX_OUTPUT})
        self.assertEqual(decode(encode(self.runtime.store.read())),self.runtime.store.read())

if __name__ == '__main__': unittest.main(verbosity=2)
