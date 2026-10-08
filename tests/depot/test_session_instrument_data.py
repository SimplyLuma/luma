import hashlib
import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('session_instrument_data', ROOT / 'packaging/flatpak/apps/org.projectluma.Session/prepare-instrument-data.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

class DataChildren(unittest.TestCase):
    def test_literal_regular_data_digest_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'sample.flac'
            path.write_bytes(b'owned sample bytes')
            self.assertEqual(api.checked_file(root, 'sample.flac', api.digest(path), 18), path)
            for digest, size in [('0' * 64, 18), (api.digest(path), 19)]:
                with self.assertRaises(ValueError): api.checked_file(root, 'sample.flac', digest, size)

    def test_traversal_absolute_and_linked_data_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'sample.flac'; path.write_bytes(b'owned')
            (root / 'alias').symlink_to(path)
            (root / 'linked-parent').symlink_to(root, target_is_directory=True)
            for name in ('../sample.flac', '/sample.flac', 'alias', 'linked-parent/sample.flac'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    api.checked_file(root, name, api.digest(path))

    def test_nonregular_and_expanded_limit_refused_before_hashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'directory').mkdir()
            with self.assertRaises(ValueError): api.checked_file(root, 'directory', '0' * 64)
            with (root / 'sparse').open('wb') as f: f.truncate(160 * 1024**2 + 1)
            with self.assertRaises(ValueError): api.checked_file(root, 'sparse', '0' * 64)

    def test_child_deterministic_fixed_modes_and_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source'; source.write_bytes(b'owned sampled audio')
            entries = [('share/luma-session/instruments/core/a.flac', source, api.digest(source))]
            first, second = root / 'a.tar', root / 'b.tar'
            self.assertEqual(api.write_child(entries, first), api.write_child(entries, second))
            with tarfile.open(first) as archive:
                member = archive.getmembers()[0]
                self.assertTrue(member.isfile()); self.assertEqual(member.mode, 0o644)
                self.assertEqual(member.uid, 0); self.assertEqual(member.mtime, api.EPOCH)
                self.assertEqual(archive.extractfile(member).read(), source.read_bytes())

    def test_no_overwrite_no_partial_on_source_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'source'; source.write_bytes(b'owned')
            entries = [('share/luma-session/instruments/core/a.flac', source, api.digest(source))]
            archive = root / 'data.tar'; archive.write_bytes(b'prior admitted bytes')
            with self.assertRaises(ValueError): api.write_child(entries, archive)
            self.assertEqual(archive.read_bytes(), b'prior admitted bytes')
            target = root / 'new.tar'
            with self.assertRaises(ValueError): api.write_child([(entries[0][0], source, '0' * 64)], target)
            self.assertFalse(target.exists()); self.assertEqual(list(root.glob('.session-data-*')), [])

    def test_canonical_receipt_digests_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); index = root / 'index.json'; index.write_text('[]')
            receipt = root / 'receipt.json'; receipt.write_text('{}')
            with self.assertRaises(ValueError): api.inventory(root, index, root, receipt)

if __name__ == '__main__': unittest.main()
