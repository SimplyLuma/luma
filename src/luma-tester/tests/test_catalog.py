# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import tempfile
import unittest

from luma_tester.catalog import resolve_asset
from luma_tester.core import TesterError


class AssetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = self.root / 'AppDir'
        self.base.mkdir()
        self.manifest = {'image': {'sha256': 'a' * 64, 'bytes': 4,
            'url': 'https://images.example.invalid/release.qcow2'},
            'external_image': 'images/' + 'a' * 64 + '.qcow2'}

    def test_sidecar_survives_app_relocation_and_metadata_is_not_mutated(self):
        image = self.root / self.manifest['external_image']
        image.parent.mkdir()
        image.write_bytes(b'test')
        result = resolve_asset(self.manifest, self.base, self.root / 'Tester.AppImage')
        self.assertEqual(result['image']['url'], image.as_uri())
        self.assertNotIn('external_image', result)
        self.assertTrue(self.manifest['image']['url'].startswith('https:'))
        self.assertEqual(resolve_asset(self.manifest, self.base)['image']['url'], image.as_uri())

    def test_missing_sidecar_keeps_published_https_download(self):
        self.assertEqual(resolve_asset(self.manifest, self.base)['image']['url'],
                         self.manifest['image']['url'])

    def test_external_paths_are_checksum_scoped(self):
        for path in ('../secret', '/etc/passwd', 'images/other.qcow2'):
            with self.subTest(path=path), self.assertRaises(TesterError):
                resolve_asset(dict(self.manifest, external_image=path), self.base)

    def test_bundled_escape_and_ambiguous_modes_are_rejected(self):
        with self.assertRaises(TesterError):
            resolve_asset(dict(self.manifest, bundled_image='images/disk'), self.base)
        with self.assertRaises(TesterError):
            resolve_asset({'image': {}, 'bundled_image': '../secret'}, self.base)

    def test_external_symlink_cannot_escape_asset_root(self):
        image = self.root / self.manifest['external_image']
        image.parent.mkdir()
        image.symlink_to(self.root.parent / 'outside')
        with self.assertRaises(TesterError):
            resolve_asset(self.manifest, self.base, self.root / 'Tester.AppImage')
