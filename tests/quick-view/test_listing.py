# SPDX-License-Identifier: GPL-2.0-or-later
"""Run with QUICK_VIEW_LISTING_BACKEND pointing to the patched source file."""
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_loader('listing', SourceFileLoader('listing', os.environ['QUICK_VIEW_LISTING_BACKEND']))
listing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(listing)

class Listings(unittest.TestCase):
    def test_folder_sort_size_and_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'Z folder').mkdir(); (root / 'a').write_bytes(b'123')
            (root / 'Z folder' / 'child').write_bytes(b'45')
            (root / 'link').symlink_to(root, target_is_directory=True)
            data = listing.folder(root)
            self.assertEqual(data['rows'][0]['name'], 'Z folder')
            self.assertEqual(data['size'], 5)
            self.assertEqual(data['count'], 3)

    def test_archive_lists_traversal_as_text_without_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'test.tar'
            with tarfile.open(path, 'w') as archive:
                entry = tarfile.TarInfo('../escape'); entry.size = 3
                archive.addfile(entry, io.BytesIO(b'123'))
            data = listing.archive(path)
            self.assertEqual(data['rows'][-1]['name'], '../escape')
            self.assertEqual(data['size'], 3)
            self.assertEqual(list(Path(temporary).iterdir()), [path])

    def test_archive_implicit_directories_and_sort(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'test.tar'
            with tarfile.open(path, 'w') as archive:
                for name in ('z/second', 'a/first', 'z/first'):
                    entry = tarfile.TarInfo(name); entry.size = 1
                    archive.addfile(entry, io.BytesIO(b'x'))
            data = listing.archive(path)
            self.assertEqual([r['display'] for r in data['rows']], ['a', 'first', 'z', 'first', 'second'])
            self.assertEqual([r['depth'] for r in data['rows']], [0, 1, 0, 1, 1])
            self.assertEqual(data['rows'][2]['items'], 2)
            self.assertEqual(data['size'], 3)
            self.assertEqual(data['count'], 3)

    def test_zip_and_compressed_tar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'test.zip'
            with zipfile.ZipFile(path, 'w') as archive: archive.writestr('folder/file.txt', b'hello')
            self.assertEqual(listing.archive(path)['size'], 5)
            for suffix, mode in [('gz', 'w:gz'), ('bz2', 'w:bz2'), ('xz', 'w:xz')]:
                with self.subTest(format=suffix):
                    path = root / ('test.tar.' + suffix)
                    with tarfile.open(path, mode) as archive:
                        entry = tarfile.TarInfo('file'); entry.size = 5
                        archive.addfile(entry, io.BytesIO(b'hello'))
                    self.assertEqual(listing.archive(path)['size'], 5)

    def test_malformed_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'bad.zip'; path.write_bytes(b'not an archive')
            with self.assertRaises(ValueError): listing.archive(path)

    def test_folder_limit_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            for index in range(4): (Path(temporary) / str(index)).touch()
            before = listing.MAX_ROWS; listing.MAX_ROWS = 3
            try:
                with self.assertRaises(ValueError): listing.folder(temporary)
            finally: listing.MAX_ROWS = before

    def test_fifo_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'pipe'; os.mkfifo(path)
            with self.assertRaises(ValueError): listing.archive(path)

if __name__ == '__main__': unittest.main()
