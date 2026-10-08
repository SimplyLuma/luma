import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from luma_tide.sharing import local_files, save_copies


class SharingTests(unittest.TestCase):
    def test_actual_bytes_collision_and_whole_album(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            first = root / 'song.flac'; first.write_bytes(b'actual FLAC payload')
            second_dir = root / 'second'; second_dir.mkdir()
            second = second_dir / 'song.flac'; second.write_bytes(b'other album track')
            existing = root / 'Tide music'; existing.mkdir()
            keep = existing / 'keep'; keep.write_bytes(b'untouched')
            copies = {'a': [SimpleNamespace(source_local=True, uri=first.as_uri(), offline_ready=False)],
                      'b': [SimpleNamespace(source_local=False, uri='https://server/secret-stream',
                                            offline_ready=True, offline_path=str(second))]}
            store = SimpleNamespace(copies_for_track=lambda key: copies[key])
            files = local_files(store, ('a', 'b'))
            self.assertEqual(files, (first, second))
            output = save_copies(files, root)
            self.assertEqual((output / 'song.flac').read_bytes(), first.read_bytes())
            self.assertEqual((output / 'song 2.flac').read_bytes(), second.read_bytes())
            self.assertEqual(keep.read_bytes(), b'untouched')

    def test_never_exports_remote_uri_or_partial_album(self):
        remote = SimpleNamespace(source_local=False, offline_ready=False,
                                 uri='https://private-token.example/track')
        store = SimpleNamespace(copies_for_track=lambda key: [remote])
        with self.assertRaises(FileNotFoundError) as error:
            local_files(store, ('track',))
        self.assertNotIn('private-token', str(error.exception))
        with self.assertRaises(FileNotFoundError):
            local_files(store, ())

    def test_failed_copy_removes_only_its_new_directory(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            preserved = root / 'Tide music'; preserved.mkdir()
            with self.assertRaises(FileNotFoundError):
                save_copies((root / 'missing.flac',), root)
            self.assertTrue(preserved.is_dir())
            self.assertFalse((root / 'Tide music 2').exists())
