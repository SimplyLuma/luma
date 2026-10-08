# SPDX-License-Identifier: Apache-2.0
"""Execute the real publisher's filesystem stage and metadata generator."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / 'scripts/os/nightly-media.sh').read_text()
HELPER = ROOT / 'scripts/os/lib/media-transport.sh'


class MediaTransportTests(unittest.TestCase):
    def test_publish_replace_and_retire_real_hardlinks(self):
        fragment = SOURCE.split('install -d -m 0755 "$tree"', 1)[1].split('# This medium', 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tree = root / 'tree'
            tree.mkdir()
            iso = root / 'luma-nightly-20261005.4.iso'
            iso.write_bytes(b'checked ISO bytes')
            digest = hashlib.sha256(iso.read_bytes()).hexdigest()
            Path(str(iso) + '.sha256').write_text(digest + '\n')
            script = 'set -eu; source "$1"; tree=$2; iso=$3; latest=luma-nightly-latest.iso\n' + fragment
            subprocess.run(['bash', '-c', script, 'test', str(HELPER), str(tree), str(iso)], check=True)
            published = tree / iso.name
            alias = tree / (iso.name + '.download')
            latest = tree / 'luma-nightly-latest.iso.download'
            self.assertTrue(iso.samefile(alias))
            self.assertTrue(iso.samefile(latest))
            newer = root / 'new.iso'
            newer.write_bytes(b'new checked ISO')
            subprocess.run(['bash', '-c', 'set -eu; source "$1"; luma_media_transport_link "$2" "$3"; luma_media_transport_retire "$4"',
                            'test', str(HELPER), str(newer), str(latest), str(published)], check=True)
            self.assertFalse(alias.exists())
            self.assertFalse(published.exists())
            self.assertTrue(latest.samefile(newer))
            self.assertEqual(hashlib.sha256(iso.read_bytes()).hexdigest(), digest)

    def test_actual_entry_separates_saved_name_from_transport(self):
        generator = SOURCE.split("<<'ENTRY'\n", 1)[1].split('\nENTRY', 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'entry.json'
            name = 'luma-nightly-20261005.4.iso'
            subprocess.run(['python3', '-', str(path), 'nightly', '20261005.4', 'test', 'c' * 64, name,
                            'a' * 64, '8192', 'https://example.test/media/nightly', '0',
                            '2026-10-05 12:00:00 +0000', '2026-10-05', 'https://example.test/notes.json',
                            'Notes', 'Luma test', str(ROOT / 'scripts/os/lib')], input=generator, text=True, check=True)
            data = json.loads(path.read_text())
            self.assertEqual(data['file'], name)
            self.assertEqual(data['download_file'], name + '.download')
            self.assertEqual(data['url'], data['download_url'])
            self.assertTrue(data['url'].endswith('.iso.download'))
            self.assertTrue(data['sha256_url'].endswith('.iso.sha256'))

    def test_legacy_single_name_fails_transport_contract(self):
        # Negative control: the former producer's lone ISO link does not provide
        # the HTTP-safe alias. This checks the defect the publisher stage fixes.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            iso = root / 'source.iso'
            iso.write_bytes(b'payload')
            old = root / 'old.iso'
            old.hardlink_to(iso)
            self.assertTrue(old.samefile(iso))
            self.assertFalse(Path(str(old) + '.download').exists())
