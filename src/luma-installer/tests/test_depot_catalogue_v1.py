# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from luma_installer import depot_catalog, depot_catalogue_v1 as v1
from luma_installer.depot_catalog import Catalog, CatalogEntry

import minisign_signer as signer

COMMIT = 'a' * 64
OTHER_KEY_SEED = bytes.fromhex('4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb')


def app(app_id, **fields):
    row = {'id': app_id, 'kind': 'app', 'format': 'flatpak', 'name': app_id.rsplit('.', 1)[-1],
           'summary': 'Says hello', 'developer': {'id': 'dev_abc', 'name': 'Studio', 'verified': True},
           'arches': ['x86_64', 'aarch64'], 'version': '1.0',
           'permissions': [{'word': 'Internet', 'reason': 'Checks for news'}],
           'releases': [{'version': '1.0', 'arches': {'x86_64': COMMIT}, 'status': 'live'}],
           'first_party': False}
    row.update(fields)
    return row


def document(apps, generated='2026-11-07T23:08:38.142Z'):
    return json.dumps({'schema': 1, 'generated': generated, 'apps': apps,
                       'looks': [], 'tweaks': []}).encode()


def signed(content, seed=signer.TEST_SEED, key_id=signer.TEST_KEY_ID, comment=None):
    comment = comment or 'depot-catalogue v1 sha256=' + hashlib.sha256(content).hexdigest() + \
        ' signed=2026-11-07T23:08:39Z'
    return signer.signature_file(content, seed, key_id, trusted_comment=comment)


class Server:
    def __init__(self, content, signature):
        self.files = {v1.URL: content, v1.URL + '.minisig': signature}
        self.requests = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.requests.append(url)
        if url not in self.files:
            raise OSError('unreachable')
        body = self.files[url]

        class Response:
            def read(self, limit=None):
                return body[:limit]

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False
        return Response()


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        root = Path(self.directory.name)
        self.key = root / 'depot-catalogue-v1.pub'
        self.key.write_bytes(signer.public_key_file())
        self.cache = root / 'cache/luma/depot/catalogue-v1.json'
        patches = [mock.patch.object(v1, 'PUBLIC_KEY_PATH', self.key),
                   mock.patch.dict(os.environ, {'XDG_CACHE_HOME': str(root / 'cache')}),
                   mock.patch.object(v1, '_last_failure', 0.0)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.directory.cleanup)

    def serve(self, content, signature):
        server = Server(content, signature)
        patch = mock.patch('urllib.request.urlopen', server)
        patch.start()
        self.addCleanup(patch.stop)
        return server

    def keep(self, apps, generated='2026-11-07T23:08:38.142Z'):
        content = document(apps, generated)
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_bytes(content)
        self.cache.with_name(self.cache.name + '.minisig').write_bytes(signed(content))


class SignatureTests(Base):
    def test_a_good_signature_is_verified_and_kept(self):
        content = document([app('com.example.Hello')])
        self.serve(content, signed(content))
        catalogue = v1.current(refresh=True)
        self.assertEqual([a.app_id for a in catalogue.apps], ['com.example.Hello'])
        self.assertEqual(self.cache.read_bytes(), content)
        self.assertEqual(v1.load_cached().apps, catalogue.apps)

    def test_bad_signatures_are_refused_before_parsing_and_never_kept(self):
        content = document([app('com.example.Hello')])
        tampered = content.replace(b'Says hello', b'Says hullo')
        cases = {
            'altered content': (tampered, signed(content)),
            'another key': (content, signed(content, OTHER_KEY_SEED)),
            'another key id': (content, signed(content, key_id=bytes(8))),
            'another document type': (content, signed(content, comment='depot-catalogue v2 sha256='
                                                       + hashlib.sha256(content).hexdigest())),
            'another digest': (content, signed(content, comment='depot-catalogue v1 sha256=' + 'b' * 64)),
            'garbage': (content, b'not a signature'),
        }
        for name, (body, signature) in cases.items():
            with self.subTest(name):
                with mock.patch.object(v1, 'parse', side_effect=AssertionError('parsed')):
                    with self.assertRaises(v1.CatalogueError):
                        v1.verified(body, signature, self.key.read_bytes())
                self.serve(body, signature)
                self.assertIsNone(v1.current(refresh=True))
                self.assertFalse(self.cache.exists())

    def test_the_live_catalogue_key_is_pinned_in_the_package(self):
        shipped = Path(__file__).resolve().parents[1] / 'data/depot-catalogue-v1.pub'
        self.assertIn('E6B75A79572BD6E8', shipped.read_text())
        from luma_installer.depot_signature import parse_public_key
        self.assertEqual(parse_public_key(shipped.read_text()).key_id.hex().upper(), 'E8D62B57795AB7E6')

    def test_a_tampered_kept_copy_is_not_trusted(self):
        self.keep([app('com.example.Hello')])
        self.cache.write_bytes(self.cache.read_bytes().replace(b'Hello', b'Jello'))
        self.assertIsNone(v1.current(offline=True))

    def test_an_older_published_catalogue_cannot_roll_back_the_kept_one(self):
        self.keep([app('com.example.Hello')], generated='2026-11-08T00:00:00Z')
        old = document([], generated='2026-11-07T00:00:00Z')
        self.serve(old, signed(old))
        catalogue = v1.current(refresh=True)
        self.assertEqual([a.app_id for a in catalogue.apps], ['com.example.Hello'])

    def test_unreachable_catalogue_answers_with_the_kept_copy_then_nothing(self):
        self.serve(b'', b'')
        server = mock.patch('urllib.request.urlopen', side_effect=OSError('offline'))
        server.start()
        self.addCleanup(server.stop)
        self.assertIsNone(v1.current(refresh=True))
        self.keep([app('com.example.Hello')])
        self.assertEqual(len(v1.current(refresh=True).apps), 1)

    def test_no_installed_key_means_no_network_and_no_apps(self):
        self.key.unlink()
        server = self.serve(b'', b'')
        self.assertEqual(v1.third_party_apps(refresh=True), ())
        self.assertEqual(server.requests, [])


class ReservedIdTests(Base):
    def admitted(self, rows):
        self.keep(rows)
        return [a.app_id for a in v1.third_party_apps(offline=True)]

    def test_reserved_prefixes_are_refused(self):
        rows = [app(i) for i in ('org.projectluma.Notes', 'org.projectluma.NewThing', 'io.luma.Monitor2',
                                 'com.simplyluma.Thing', 'org.simplyluma.Thing', 'com.projectluma.Thing',
                                 'ORG.ProjectLuma.Upper', 'com.example.Fine')]
        self.assertEqual(self.admitted(rows), ['com.example.Fine'])

    def test_luma_app_ids_without_a_reserved_prefix_are_refused(self):
        rows = [app('com.rhyme.viola'), app('org.gnome.Nautilus'), app('io.github.nmcmil.Aura'),
                app('com.example.Fine')]
        # Aura is Luma's in the catalogue itself; Viola and Filer are maintained roles.
        first_party = {'id': 'io.github.nmcmil.Aura', 'kind': 'app', 'format': 'flatpak',
                       'first_party': True, 'developer': {'id': 'luma'}}
        self.assertEqual(self.admitted([first_party] + rows), ['com.example.Fine'])

    def test_a_luma_developer_or_missing_first_party_flag_is_never_third_party(self):
        rows = [app('com.example.A', developer={'id': 'luma', 'name': 'Luma'}),
                {k: v for k, v in app('com.example.B').items() if k != 'first_party'},
                app('com.example.C')]
        self.assertEqual(self.admitted(rows), ['com.example.C'])

    def test_only_live_flatpak_apps_for_known_arches_are_taken(self):
        rows = [app('com.example.Look', kind='look'), app('com.example.Deb', format='deb'),
                app('com.example.Gone', releases=[{'version': '1', 'arches': {'x86_64': COMMIT},
                                                   'status': 'withdrawn'}]),
                app('com.example.Bad', releases=[{'arches': {'x86_64': 'nope'}, 'status': 'live'}]),
                app('com.example.Riscv', arches=['riscv64'],
                    releases=[{'arches': {'riscv64': COMMIT}, 'status': 'live'}]),
                app('not-an-app-id'), app('com.example.Ok')]
        self.keep(rows)
        found = v1.third_party_apps(offline=True)
        self.assertEqual([a.app_id for a in found], ['com.example.Ok'])
        self.assertEqual(found[0].arches, ('x86_64',))
        self.assertEqual(found[0].refs, ('app/com.example.Ok/x86_64/stable',))
        self.assertEqual(found[0].permissions, (('Internet', 'Checks for news'),))


def base_catalog():
    entry = CatalogEntry(id='firefox', name='Firefox', backend='flatpak', source_id='org.mozilla.firefox',
                         repository='flathub', distribution='flathub', architectures=('x86_64',),
                         reference_url='https://flathub.org', qualification='pending',
                         qualification_reason='', app_id='org.mozilla.firefox', tier='verified')
    luma = CatalogEntry(id='notes', name='Notes', backend='flatpak', source_id='org.projectluma.Notes',
                        repository='luma', distribution='publisher', architectures=('x86_64',),
                        reference_url='https://simplyluma.com', qualification='pending',
                        qualification_reason='', app_id='org.projectluma.Notes', tier='luma')
    return Catalog('2026-11-07', (entry, luma), schema_version=4, verified=True)


class MergeTests(Base):
    def test_third_party_apps_join_after_every_first_party_entry(self):
        self.keep([app('com.example.Hello'), app('org.mozilla.firefox'), app('com.example.Twice'),
                   app('com.example.Twice', name='Impostor')])
        merged = v1.merge(base_catalog())
        self.assertEqual([e.id for e in merged.applications], ['firefox', 'notes', 'com.example.Hello'])
        hello = merged.applications[-1]
        self.assertEqual((hello.backend, hello.repository, hello.source_id, hello.branch, hello.app_id),
                         ('flatpak', 'luma', 'com.example.Hello', 'stable', 'com.example.Hello'))
        self.assertEqual(hello.flatpak.repository, 'luma')
        self.assertIs(merged.find('com.example.Hello'), hello)
        self.assertTrue(merged.verified)

    def test_local_and_current_catalogue_carry_third_party_apps(self):
        self.keep([app('com.example.Hello')])
        with mock.patch.object(depot_catalog, '_local_first_party', return_value=base_catalog()):
            self.assertIsNotNone(depot_catalog.local_catalog().find('com.example.Hello'))
        with mock.patch.object(depot_catalog, '_current_catalog', return_value=base_catalog()), \
                mock.patch.object(v1, 'fetch', side_effect=v1.CatalogueError('offline')):
            self.assertIsNotNone(depot_catalog.current_catalog(refresh=True).find('com.example.Hello'))

    def test_a_missing_or_broken_catalogue_changes_nothing(self):
        base = base_catalog()
        self.assertIs(v1.merge(base), base)
        self.keep([app('com.example.Hello')])
        self.cache.with_name(self.cache.name + '.minisig').write_bytes(b'broken')
        self.assertIs(v1.merge(base), base)
        with mock.patch.object(v1, 'merge', side_effect=RuntimeError('bug')), \
                mock.patch.object(depot_catalog, '_local_first_party', return_value=base):
            self.assertIs(depot_catalog.local_catalog(), base)

    def test_flatpak_sources_route_third_party_apps_to_the_luma_remote(self):
        self.keep([app('com.example.Hello')])
        from luma_installer import depot_flatpak
        with mock.patch.object(depot_catalog, '_local_first_party', return_value=base_catalog()):
            depot_flatpak._refresh()
        self.addCleanup(depot_flatpak._refresh)
        self.assertEqual(depot_flatpak.APPLICATION_SOURCES['com.example.Hello'], 'com.example.Hello')
        self.assertEqual(depot_flatpak.SOURCE_REMOTES['com.example.Hello'], ('luma', 'stable'))
        self.assertEqual(depot_flatpak.SOURCE_REMOTES['org.mozilla.firefox'], ('flathub', 'stable'))
        self.assertTrue(depot_flatpak.third_party('com.example.Hello'))
        self.assertFalse(depot_flatpak.third_party('org.projectluma.Notes'))


class VerifyInstalledTests(Base):
    def source(self, app_id, remote='luma'):
        from luma_installer.depot_flatpak import ResolvedSource
        return ResolvedSource(app_id, 'x86_64', f'app/{app_id}/x86_64/stable', COMMIT, remote)

    def test_first_party_and_flathub_apps_are_left_alone(self):
        from luma_installer import depot_flatpak
        self.keep([app('com.example.Hello')])
        installation = mock.Mock()
        depot_flatpak.verify_installed(installation, self.source('org.projectluma.Notes'))
        depot_flatpak.verify_installed(installation, self.source('com.example.Hello', 'flathub'))
        installation.assert_not_called()
        self.assertEqual(installation.method_calls, [])

    def test_an_unverifiable_third_party_deployment_is_removed(self):
        from luma_installer import depot_flatpak
        self.keep([app('com.example.Hello')])
        ref = mock.Mock(**{'get_origin.return_value': 'luma', 'get_commit.return_value': COMMIT})
        removal = mock.Mock()
        with mock.patch.object(depot_flatpak, 'installed_ref', return_value=ref), \
                mock.patch('luma_installer.native_app_roles.verify_deployed_commit',
                           side_effect=ValueError('no valid maintained Luma signature')) as verify, \
                mock.patch.object(depot_flatpak, 'removal_transaction', return_value=removal):
            with self.assertRaises(depot_flatpak.SourceUnavailable):
                depot_flatpak.verify_installed(mock.Mock(), self.source('com.example.Hello'))
        verify.assert_called_once()
        self.assertEqual(verify.call_args.kwargs, {'commit': COMMIT,
                                                   'exact_ref': 'app/com.example.Hello/x86_64/stable'})
        removal.run.assert_called_once()

    def test_a_verified_third_party_deployment_is_kept(self):
        from luma_installer import depot_flatpak
        self.keep([app('com.example.Hello')])
        ref = mock.Mock(**{'get_origin.return_value': 'luma', 'get_commit.return_value': COMMIT})
        with mock.patch.object(depot_flatpak, 'installed_ref', return_value=ref), \
                mock.patch('luma_installer.native_app_roles.verify_deployed_commit') as verify, \
                mock.patch.object(depot_flatpak, 'removal_transaction') as removal:
            depot_flatpak.verify_installed(mock.Mock(), self.source('com.example.Hello'))
        verify.assert_called_once()
        removal.assert_not_called()


if __name__ == '__main__':
    unittest.main()
