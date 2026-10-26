# SPDX-License-Identifier: Apache-2.0
"""Source admission refuses altered inputs and unsafe paths before compilation."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('kernel_source', ROOT / 'scripts/kernel/prepare-source.py')
source = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(source)


class KernelSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache = self.root / 'cache'
        self.cache.mkdir()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.output = self.root / 'prepared'
        self.lock = {'schema': 'org.projectluma.kernel-source/v1',
                     'release': '1.0-test', 'sources': {}, 'patches': []}
        for name in ('linux', 'overlay'):
            path = self.cache / (name + '.tar.gz')
            with tarfile.open(path, 'w:gz') as archive:
                data = b'old\n'
                member = tarfile.TarInfo(name + '/input.txt')
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            self.lock['sources'][name] = {'name': path.name,
                'url': 'https://example.org/' + path.name, 'sha256': source.digest(path)}
        self.lock_path = self.repo / 'source.json'

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self):
        self.lock_path.write_text(json.dumps(self.lock))
        source.prepare(self.lock_path, self.cache, self.output, self.repo, True)

    def test_exact_offline_patch_replay(self):
        patch = self.repo / 'fix.patch'
        patch.write_text('--- a/input.txt\n+++ b/input.txt\n@@ -1 +1 @@\n-old\n+new\n')
        self.lock['patches'] = [{'source': 'repository', 'path': 'fix.patch',
                                 'sha256': source.digest(patch)}]
        self.prepare()
        self.assertEqual((self.output / 'linux/linux/input.txt').read_text(), 'new\n')
        receipt = json.loads((self.output / 'PREPARED.json').read_text())
        self.assertEqual(receipt['patch_count'], 1)
        self.assertEqual(receipt['lock_sha256'], source.digest(self.lock_path))

    def test_corrupt_cached_source_is_refused(self):
        (self.cache / 'linux.tar.gz').write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_missing_offline_source_is_refused(self):
        (self.cache / 'linux.tar.gz').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing cached'):
            self.prepare()

    def test_existing_source_is_never_repatched(self):
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.prepare()

    def test_patch_cannot_escape_repository(self):
        self.lock['patches'] = [{'source': 'repository', 'path': '../secret', 'sha256': 'a' * 64}]
        with self.assertRaisesRegex(ValueError, 'Unsafe relative'):
            self.prepare()

    def test_changed_patch_is_refused(self):
        (self.repo / 'fix.patch').write_text('changed')
        self.lock['patches'] = [{'source': 'repository', 'path': 'fix.patch', 'sha256': 'a' * 64}]
        with self.assertRaisesRegex(ValueError, 'Patch hash/path mismatch'):
            self.prepare()
        self.assertFalse((self.output / 'PREPARED.json').exists())

    def test_archive_escape_is_refused(self):
        path = self.cache / 'linux.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            member = tarfile.TarInfo('../../escape')
            member.size = 1
            archive.addfile(member, io.BytesIO(b'x'))
        self.lock['sources']['linux']['sha256'] = source.digest(path)
        with self.assertRaises(tarfile.FilterError):
            self.prepare()
        self.assertFalse((self.root / 'escape').exists())

    def test_real_lock_is_valid(self):
        source.validate(json.loads((ROOT / 'config/kernel/surface-pro12-7.3.json').read_text()))
