# SPDX-License-Identifier: Apache-2.0
"""A publisher's signed APT repository: index, version choice, verification."""

import gzip
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from luma_installer import depot_deb_repository as apt
from luma_installer.depot_catalog import DebRepositorySource

SOURCE = DebRepositorySource('anthropic', 'Anthropic', 'claude-desktop',
                             'https://downloads.claude.ai/claude-desktop/apt/stable',
                             'https://downloads.claude.ai/claude-desktop/key.asc',
                             ('31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE',))
PACKAGES = """Package: claude-desktop
Version: 1.17180.0
Architecture: amd64
Filename: pool/main/c/claude-desktop/claude-desktop_1.17180.0_amd64.deb
Size: 3
SHA256: {old}
Description: Desktop application for Claude.ai
 Desktop application for Claude.ai

Package: claude-desktop
Version: 1.17282.0
Architecture: amd64
Filename: pool/main/c/claude-desktop/claude-desktop_1.17282.0_amd64.deb
Size: 3
SHA256: {new}

Package: something-else
Version: 9.0
Architecture: amd64
Filename: pool/x.deb
"""
DEB = b'deb'


def release(packages: bytes, valid='Fri, 25 Sep 2099 07:31:48 UTC'):
    gz = gzip.compress(packages)
    return (f"Origin: Anthropic\nSuite: stable\nValid-Until: {valid}\nSHA256:\n"
            f" {hashlib.sha256(packages).hexdigest()} {len(packages)} main/binary-amd64/Packages\n"
            f" {hashlib.sha256(gz).hexdigest()} {len(gz)} main/binary-amd64/Packages.gz\n")


class Index(unittest.TestCase):
    def packages(self):
        return PACKAGES.format(old='a' * 64, new=hashlib.sha256(DEB).hexdigest()).encode()

    def fetcher(self, packages, release_text, deb=DEB):
        def fetch(url, limit, progress=None):
            if url.endswith('key.asc'):
                return b'key'
            if url.endswith('InRelease'):
                return release_text.encode()
            if url.endswith('/Packages'):
                return packages
            if url.endswith('.deb'):
                return deb
            raise AssertionError(url)
        return fetch

    def test_newest_verified_version_is_chosen(self):
        packages = self.packages()
        with mock.patch.object(apt, 'verify_release', lambda text, key, pins, gpg=None: text.decode()):
            resolved = apt.resolve(SOURCE, 'x86_64', fetcher=self.fetcher(packages, release(packages)))
        self.assertEqual(resolved.version, '1.17282.0')
        self.assertTrue(resolved.url.endswith('claude-desktop_1.17282.0_amd64.deb'))

    def test_a_package_list_that_does_not_match_the_signed_index_is_refused(self):
        packages = self.packages()
        tampered = packages.replace(b'1.17282.0', b'9.99999.0')
        with mock.patch.object(apt, 'verify_release', lambda text, key, pins, gpg=None: text.decode()):
            with self.assertRaises(apt.RepositoryError):
                apt.resolve(SOURCE, 'x86_64', fetcher=self.fetcher(tampered, release(packages)))

    def test_an_expired_index_is_refused(self):
        packages = self.packages()
        with mock.patch.object(apt, 'verify_release', lambda text, key, pins, gpg=None: text.decode()):
            with self.assertRaises(apt.RepositoryError):
                apt.resolve(SOURCE, 'x86_64', fetcher=self.fetcher(
                    packages, release(packages, valid='Fri, 25 Sep 2020 07:31:48 UTC')))

    def test_unknown_processor_is_refused(self):
        with self.assertRaises(apt.RepositoryError):
            apt.resolve(SOURCE, 'riscv64', fetcher=self.fetcher(b'', ''))

    def test_download_checks_the_hash(self):
        resolved = apt.Resolved('https://x/p.deb', '1', 3, hashlib.sha256(DEB).hexdigest())
        with tempfile.TemporaryDirectory() as tmp:
            path = apt.download(resolved, directory=tmp, fetcher=lambda url, limit, progress=None: DEB)
            self.assertEqual(path.read_bytes(), DEB)
            bad = apt.Resolved('https://x/q.deb', '1', 3, 'b' * 64)
            with self.assertRaises(apt.RepositoryError):
                apt.download(bad, directory=tmp, fetcher=lambda url, limit, progress=None: DEB)

    def test_versions_order_numerically(self):
        self.assertGreater(apt._version_key('1.10.0'), apt._version_key('1.9.9'))
        self.assertGreater(apt._version_key('1:0.1'), apt._version_key('9.9'))

    def test_records_and_carrying_data(self):
        records = [{'format': 'deb', 'package': 'claude-desktop', 'version': '1.2', 'sha256': 'a' * 64,
                    'application_id': 'old'},
                   {'format': 'deb', 'package': 'claude-desktop', 'version': '1.10', 'sha256': 'b' * 64,
                    'application_id': 'new'},
                   {'format': 'rpm', 'package': 'claude-desktop'}]
        found = apt.installed_records('claude-desktop', records=records)
        self.assertEqual([r['application_id'] for r in found], ['old', 'new'])
        with tempfile.TemporaryDirectory() as home:
            base = Path(home) / '.local/share/luma/installer/data'
            (base / ('a' * 64)).mkdir(parents=True)
            (base / ('a' * 64) / 'signed-in').write_text('yes')
            apt.carry_data(records[0], records[1], home=home)
            self.assertEqual((base / ('b' * 64) / 'signed-in').read_text(), 'yes')


@unittest.skipUnless(Path(apt.GPG).exists(), 'needs gpg')
class Signatures(unittest.TestCase):
    """A real key, a real clear-signed index, and a key that is not pinned."""

    def setUp(self):
        import subprocess
        import os
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        os.chmod(self.home, 0o700)
        env = {'GNUPGHOME': self.home, 'PATH': '/usr/bin:/bin'}
        subprocess.run([apt.GPG, '--batch', '--passphrase', '', '--quick-gen-key', 'Test Repo <t@example.org>',
                        'ed25519', 'sign', 'never'], env=env, check=True, capture_output=True)
        listing = subprocess.run([apt.GPG, '--batch', '--with-colons', '--fingerprint', '--list-keys'], env=env,
                                 capture_output=True, text=True).stdout
        self.fingerprint = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr'))
        self.key = subprocess.run([apt.GPG, '--batch', '--armor', '--export'], env=env,
                                  capture_output=True).stdout
        self.signed = subprocess.run([apt.GPG, '--batch', '--clearsign'], input=b'Origin: Test\n', env=env,
                                     capture_output=True).stdout

    def tearDown(self):
        self.tmp.cleanup()

    def test_good_signature_by_the_pinned_key(self):
        self.assertIn('Origin: Test', apt.verify_release(self.signed, self.key, [self.fingerprint]))

    def test_key_not_pinned_is_refused(self):
        with self.assertRaises(apt.RepositoryError):
            apt.verify_release(self.signed, self.key, ['0' * 40])

    def test_tampered_index_is_refused(self):
        with self.assertRaises(apt.RepositoryError):
            apt.verify_release(self.signed.replace(b'Origin: Test', b'Origin: Evil'), self.key, [self.fingerprint])


if __name__ == '__main__':
    unittest.main()
