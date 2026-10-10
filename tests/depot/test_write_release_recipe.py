# SPDX-License-Identifier: Apache-2.0
"""Verify the standalone Write release inputs and install identity."""
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
RECIPE = ROOT / 'packaging/flatpak/apps/org.projectluma.Write'

class WriteRelease(unittest.TestCase):
    def test_public_source_and_complete_ui_assets_are_pinned(self):
        doc = json.loads((RECIPE / 'org.projectluma.Write.yml').read_text())
        self.assertEqual(doc['app-id'], 'org.projectluma.Write')
        git = [s for m in doc['modules'] for s in m['sources'] if s['type'] == 'git']
        self.assertEqual(len(git), 2)
        for source in git:
            self.assertEqual(source['url'], 'https://github.com/SimplyLuma/Write.git')
            self.assertEqual(source['commit'], '0cc01c320916d294e28d32c04a5157232eaea376')
        for source in [s for m in doc['modules'] for s in m['sources'] if 'url' in s and s['type'] != 'git']:
            self.assertRegex(source['sha256'], r'^[0-9a-f]{64}$')
            self.assertTrue(source['url'].startswith('https://github.com/SimplyLuma/Write/releases/download/v0.1.0/'))
        icons = next(m for m in doc['modules'] if m['name'] == 'luma-ui-icons')
        self.assertIn('lumaui-*-symbolic.svg', ' '.join(icons['build-commands']))
        self.assertFalse(doc['add-extensions']['org.projectluma.Platform.Office']['no-autodownload'])
        self.assertIn('--env=GSETTINGS_BACKEND=keyfile', doc['finish-args'])

    def test_real_media_declared_and_installed_with_matching_digest(self):
        xml = ET.parse(RECIPE / 'org.projectluma.Write.metainfo.xml').getroot()
        image = xml.find('./screenshots/screenshot/image')
        self.assertIsNotNone(image)
        name = image.text.rsplit('/', 1)[1]
        self.assertRegex(name, r'^[0-9a-f]{64}\.png$')
        doc = json.loads((RECIPE / 'org.projectluma.Write.yml').read_text())
        media = next(m for m in doc['modules'] if m['name'] == 'release-media')
        source = next(s for s in media['sources'] if 'sha256' in s)
        self.assertEqual(source['sha256'] + '.png', name)
        self.assertIn('/app/share/luma-app-media/' + name, ' '.join(media['build-commands']))

    def test_public_catalog_installs_qualified_stable_and_preserves_identity(self):
        catalog = json.loads((ROOT / 'src/luma-installer/data/depot-catalog-4.json').read_text())
        write = next(a for a in catalog['applications'] if a['id'] == 'write')
        self.assertEqual((write['app_id'],write['repository'],write['branch'],write['visibility']),
                         ('org.projectluma.Write','luma','stable','public'))
        self.assertEqual(write['architectures'], ['x86_64'])
        self.assertTrue(write['icon']['sha256'])
        self.assertTrue(write['screenshots'])
        registry = json.loads((ROOT / 'packaging/flatpak/first-party-updates.json').read_text())
        entry = next(a for a in registry['applications'] if a['id'] == write['app_id'])
        self.assertEqual(entry['source_repository'], 'https://github.com/SimplyLuma/Write.git')
        self.assertEqual(entry['producer'], 'creative-external')
        self.assertFalse(entry['required_host_services'])

if __name__ == '__main__': unittest.main()
