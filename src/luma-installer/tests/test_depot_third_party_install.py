# SPDX-License-Identifier: Apache-2.0
"""Install, update and remove a third-party app from a local signed ostree repo.

A throwaway GPG key signs a runtime and an app in a local Flatpak repository
that stands in for the Luma remote. The app is listed in a catalogue v1 signed
with the test minisign key, and Depot's own calls install it into a private
per-user installation: installation_for, resolve, transaction, then
verify_installed with the fingerprint pinned to the throwaway key. A build
signed by a second key that the remote also trusts must be refused and removed.

libflatpak caches its installation paths per process, so the work runs in a
child interpreter with FLATPAK_USER_DIR set before anything is loaded.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
APP_ID = 'com.example.DepotThirdParty'
RUNTIME = 'org.example.DepotTestPlatform'


def _tools_missing():
    for tool in ('flatpak', 'gpg'):
        if shutil.which(tool) is None:
            return f'{tool} is not installed'
    try:
        import gi
        gi.require_version('Flatpak', '1.0')
        gi.require_version('OSTree', '1.0')
        from gi.repository import Flatpak, OSTree  # noqa: F401
    except (ImportError, ValueError) as error:
        return f'libflatpak/libostree introspection is unavailable: {error}'
    return ''


def run(*argv, env=None):
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise AssertionError(f'{argv[:3]} failed: {result.stdout}\n{result.stderr}')
    return result.stdout


class Fixture:
    def __init__(self, root: Path):
        self.root = root
        self.repo = root / 'repo'
        self.gnupg = root / 'gnupg'
        self.gnupg.mkdir(mode=0o700)
        self.env = {**os.environ, 'GNUPGHOME': str(self.gnupg),
                    'FLATPAK_USER_DIR': str(root / 'user'), 'FLATPAK_SYSTEM_DIR': str(root / 'system'),
                    'FLATPAK_CONFIG_DIR': str(root / 'config'), 'FLATPAK_RUN_DIR': str(root / 'run'),
                    'FLATPAK_TRIGGERSDIR': str(root / 'triggers'), 'XDG_CACHE_HOME': str(root / 'cache'),
                    'XDG_DATA_HOME': str(root / 'data'), 'HOME': str(root / 'home')}
        for name in ('system', 'config', 'run', 'triggers', 'home'):
            (root / name).mkdir()
        self.arch = run('flatpak', '--default-arch', env=self.env).strip()
        self.trusted = self.key('Depot Test Luma')
        self.other = self.key('Depot Test Other')

    def key(self, name):
        run('gpg', '--batch', '--passphrase', '', '--quick-gen-key', f'{name} <test@example.invalid>',
            'ed25519', 'sign', 'never', env=self.env)
        listing = run('gpg', '--batch', '--with-colons', '--list-secret-keys', name, env=self.env)
        return next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))

    def sign_args(self, fingerprint):
        return [f'--gpg-sign={fingerprint}', f'--gpg-homedir={self.gnupg}']

    def runtime(self):
        tree = self.root / 'runtime'
        (tree / 'usr/bin').mkdir(parents=True)
        (tree / 'usr/bin/true').write_text('#!/bin/sh\n')
        (tree / 'metadata').write_text(
            f'[Runtime]\nname={RUNTIME}\nruntime={RUNTIME}/{self.arch}/1\nsdk={RUNTIME}/{self.arch}/1\n')
        run('flatpak', 'build-export', '--runtime', *self.sign_args(self.trusted), '--no-update-summary',
            str(self.repo), str(tree), '1', env=self.env)

    def app(self, version, fingerprint):
        tree = self.root / f'app-{version}'
        (tree / 'files/bin').mkdir(parents=True)
        (tree / 'export').mkdir()
        (tree / 'files/bin/hello').write_text(f'#!/bin/sh\necho {version}\n')
        (tree / 'metadata').write_text(
            f'[Application]\nname={APP_ID}\nruntime={RUNTIME}/{self.arch}/1\n'
            f'sdk={RUNTIME}/{self.arch}/1\ncommand=hello\n')
        run('flatpak', 'build-export', *self.sign_args(fingerprint), '--no-update-summary',
            str(self.repo), str(tree), 'stable', env=self.env)
        run('flatpak', 'build-update-repo', *self.sign_args(self.trusted), str(self.repo), env=self.env)

    def remote(self):
        keyring = self.root / 'keys.gpg'
        with keyring.open('wb') as stream:
            stream.write(subprocess.run(['gpg', '--batch', '--export', self.trusted, self.other],
                                        env=self.env, capture_output=True, check=True).stdout)
        run('flatpak', '--user', 'remote-add', f'--gpg-import={keyring}', 'luma',
            self.repo.as_uri() + '/', env=self.env)

    def catalogue(self):
        sys.path.insert(0, str(HERE))
        import minisign_signer as signer
        row = {'id': APP_ID, 'kind': 'app', 'format': 'flatpak', 'name': 'Third Party', 'summary': 'Test',
               'developer': {'id': 'dev_test', 'name': 'Test Studio', 'verified': True},
               'arches': [self.arch], 'permissions': [], 'first_party': False,
               'releases': [{'version': '1', 'arches': {self.arch: 'c' * 64}, 'status': 'live'}]}
        content = json.dumps({'schema': 1, 'generated': '2026-11-07T23:00:00Z', 'apps': [row]}).encode()
        comment = 'depot-catalogue v1 sha256=' + hashlib.sha256(content).hexdigest()
        cache = self.root / 'cache/luma/depot'
        cache.mkdir(parents=True)
        (cache / 'catalogue-v1.json').write_bytes(content)
        (cache / 'catalogue-v1.json.minisig').write_bytes(
            signer.signature_file(content, trusted_comment=comment))
        key = self.root / 'depot-catalogue-v1.pub'
        key.write_bytes(signer.public_key_file())
        return key


CHILD = r'''
import json, sys
from luma_installer import depot_catalogue_v1, depot_flatpak, native_app_roles
options = json.loads(sys.argv[1])
depot_catalogue_v1.PUBLIC_KEY_PATH = __import__('pathlib').Path(options['key'])
depot_flatpak.REMOTES['luma'] = depot_flatpak.RemotePolicy(
    'luma', 'Luma', frozenset({options['url']}), None)
native_app_roles.SIGNING_FINGERPRINTS = frozenset({options['fingerprint']})
import gi
gi.require_version('Flatpak', '1.0')
from gi.repository import Flatpak
app_id, arch = options['app_id'], options['arch']
installation, remote = depot_flatpak.installation_for(app_id)
assert remote == 'luma' and installation.get_is_user(), (remote, installation.get_path().get_path())
source = depot_flatpak.resolve(installation, app_id, arch, None, remote, 'stable')
result = {'ref': source.ref}
try:
    if options['action'] == 'install':
        tx = depot_flatpak.transaction(installation, source)
        tx.run(None)
    else:
        installed = depot_flatpak.installed_ref(installation, source)
        assert [r.get_name() for _i, r in depot_flatpak.pending_updates([installation])] == [app_id]
        tx = depot_flatpak.transaction(installation, source, update=True,
                                       expected_installed_commit=installed.get_commit())
        tx.run(None)
    installation.drop_caches(None)
    depot_flatpak.verify_installed(installation, source)
    result['verified'] = True
except depot_flatpak.SourceUnavailable as error:
    result['refused'] = str(error)
installation.drop_caches(None)
ref = depot_flatpak.installed_ref(installation, source)
result['installed'] = ref.get_commit() if ref is not None else None
if options.get('remove') and ref is not None:
    depot_flatpak.removal_transaction(installation, ref).run(None)
    installation.drop_caches(None)
    result['after_remove'] = depot_flatpak.installed_ref(installation, source) is not None
print(json.dumps(result))
'''


@unittest.skipIf(_tools_missing(), _tools_missing())
class ThirdPartyInstallTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fixture = Fixture(Path(self.directory.name))
        self.fixture.runtime()
        self.key = self.fixture.catalogue()

    def child(self, action, *, remove=False):
        options = {'key': str(self.key), 'url': self.fixture.repo.as_uri() + '/',
                   'fingerprint': self.fixture.trusted, 'app_id': APP_ID, 'arch': self.fixture.arch,
                   'action': action, 'remove': remove}
        env = {**self.fixture.env, 'PYTHONPATH': os.pathsep.join(
            filter(None, (str(PACKAGE), os.environ.get('PYTHONPATH', '')))),
               'LUMA_DEPOT_DATA_DIRECTORY': str(PACKAGE / 'data')}
        return json.loads(run(sys.executable, '-c', CHILD, json.dumps(options), env=env).splitlines()[-1])

    def test_a_luma_signed_third_party_app_installs_updates_and_uninstalls(self):
        self.fixture.app('1', self.fixture.trusted)
        self.fixture.remote()
        first = self.child('install')
        self.assertTrue(first.get('verified'), first)
        self.assertEqual(first['ref'], f'app/{APP_ID}/{self.fixture.arch}/stable')
        self.assertIsNotNone(first['installed'])
        self.fixture.app('2', self.fixture.trusted)
        second = self.child('update', remove=True)
        self.assertTrue(second.get('verified'), second)
        self.assertNotEqual(second['installed'], first['installed'])
        self.assertFalse(second['after_remove'])

    def test_a_build_signed_by_another_trusted_key_is_removed(self):
        self.fixture.app('1', self.fixture.other)
        self.fixture.remote()
        result = self.child('install')
        self.assertIn('could not be verified', result.get('refused', ''), result)
        self.assertIsNone(result['installed'])


if __name__ == '__main__':
    unittest.main()
