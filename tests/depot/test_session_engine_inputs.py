# SPDX-License-Identifier: Apache-2.0
"""Real archive safety controls; no synthetic fixture is engine proof."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'packaging/flatpak/apps/org.projectluma.Session/prepare-engine-inputs.py'
spec = importlib.util.spec_from_file_location('session_engine_inputs', HELPER)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class EngineInputControls(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def archive(self, members):
        output = self.root / ('input' + str(len(list(self.root.glob('*.tar')))) + '.tar')
        with tarfile.open(output, 'w') as package:
            for name, payload, link in members:
                item = tarfile.TarInfo(name)
                item.mode = 0o644
                if link is not None:
                    item.type = tarfile.SYMTYPE
                    item.linkname = link
                    package.addfile(item)
                else:
                    item.size = len(payload)
                    package.addfile(item, io.BytesIO(payload))
        return output

    def test_regular_bytes_and_unicode_sibling_alias_are_preserved(self):
        archive = self.archive([('upstream/sf2/original.sf2', b'owned-fixture', None),
                                ('upstream/sf2/■alias■.sf2', b'', 'original.sf2')])
        output = self.root / 'result'
        result = engine.extract(archive, output)
        self.assertEqual((output / 'sf2/■alias■.sf2').read_bytes(), b'owned-fixture')
        self.assertFalse((output / 'sf2/■alias■.sf2').is_symlink())
        self.assertEqual(result['files'], 2)
        self.assertEqual(result['materialized_aliases'][0]['sibling'], 'original.sf2')

    def test_parent_absolute_and_backslash_archive_paths_are_refused(self):
        for name in ['../escape', '/absolute/file', 'upstream/../escape', 'upstream/back\\slash']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                engine.extract(self.archive([(name, b'bad', None)]), self.root / 'result')
        self.assertFalse((self.root.parent / 'escape').exists())

    def test_external_chained_and_missing_aliases_are_refused(self):
        for link in ['../file', '/etc/passwd', 'subdir/file', 'missing']:
            with self.subTest(link=link), self.assertRaises((ValueError, FileNotFoundError)):
                engine.extract(self.archive([('root/alias', b'', link)]), self.root / 'result')
        archive = self.archive([('root/original', b'present', None),
                                ('root/second', b'', 'original'),
                                ('root/first', b'', 'second')])
        with self.assertRaises(FileNotFoundError):
            engine.extract(archive, self.root / 'chained')

    def test_expansion_bound_rejects_real_larger_member(self):
        previous = engine.MAX_EXPANDED
        self.addCleanup(setattr, engine, 'MAX_EXPANDED', previous)
        engine.MAX_EXPANDED = 2
        with self.assertRaisesRegex(ValueError, 'expansion exceeds'):
            engine.extract(self.archive([('root/file', b'abc', None)]), self.root / 'result')

    def test_duplicate_regular_members_and_multiple_roots_are_refused(self):
        for members in [[('root/file', b'original', None), ('root/file', b'changed', None)],
                        [('root/file', b'one', None), ('different/file', b'two', None)]]:
            with self.assertRaises((ValueError, FileExistsError)):
                engine.extract(self.archive(members), self.root / str(len(members)))

    def test_zip_symlink_is_refused(self):
        archive = self.root / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as package:
            item = zipfile.ZipInfo('root/link')
            item.external_attr = (0o120777 << 16)
            package.writestr(item, '/etc/passwd')
        with self.assertRaises(ValueError):
            engine.extract(archive, self.root / 'result')

    def test_digest_corruption_does_not_create_output(self):
        cache = self.root / 'cache'
        cache.mkdir()
        sources = []
        for index in range(7):
            content = ('trusted-' + str(index)).encode()
            digest = hashlib.sha256(content).hexdigest()
            (cache / digest).write_bytes(content if index else b'corrupted')
            sources.append({'module': 'session-fluidsynth', 'dest': str(index), 'sha256': digest})
        lock = self.root / 'lock.json'
        lock.write_text(json.dumps({'schema': 'org.projectluma.session-engine-inputs/v1', 'sources': sources}))
        output = self.root / 'output'
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            engine.prepare(lock, cache, output)
        self.assertFalse(output.exists())

    def test_empty_archive_is_not_prepared(self):
        with self.assertRaisesRegex(ValueError, 'no files'):
            engine.extract(self.archive([]), self.root / 'result')


if __name__ == '__main__':
    unittest.main()
