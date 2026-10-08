#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Real catalog/filesystem export controls; renderer seam is not pixel proof."""
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from prairie_apps.photos_backend import PhotoLibrary
from prairie_apps.photos_adjustments import AdjustmentStore, has_edits


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>2I5B', 2, 2, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b'\0' + b'\xff\0\0' * 2 + b'\0' + b'\0\xff\0' * 2)) + chunk(b'IEND', b''))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        source = self.root / 'Pictures'
        source.mkdir()
        self.photo = source / 'Original.png'
        self.before = png()
        self.photo.write_bytes(self.before)
        self.library = PhotoLibrary(self.root / 'library.sqlite3')
        entry = self.library.add_source(source)
        self.assertEqual(self.library.scan_source(entry.id).added, 1)
        self.asset = self.library.assets()[0]
        self.adjustments = AdjustmentStore(self.library.database)
        self.output = self.root / 'export'

    def edit(self):
        self.adjustments.update(self.asset.id, {'crop_rect': [.25, .25, .5, .5]})

    def test_unedited_and_reverted_settings_preserve_exact_original_bytes(self):
        def forbidden(*_):
            raise AssertionError('Neutral settings must not re-encode the original')
        for settings in ({}, {'rot': 0, 'e': 0, 'pre': '', 'crop_rect': [0, 0, 1, 1]}):
            self.adjustments.update(self.asset.id, settings)
            result = self.library.export_assets([self.asset.id], self.output, render_photo=forbidden)
            self.assertFalse(result.errors)
            self.assertEqual(result.completed[0].read_bytes(), self.before)
        self.assertEqual(self.photo.read_bytes(), self.before)

    def test_saved_edits_are_rendered_and_original_remains_byte_exact(self):
        self.edit()
        seen = []
        def renderer(path, values):
            seen.append((path, values))
            return png()
        result = self.library.export_assets([self.asset.id], self.output, render_photo=renderer)
        self.assertFalse(result.errors)
        self.assertEqual(seen, [(self.photo, {'crop_rect': [.25, .25, .5, .5]})])
        self.assertEqual(result.completed[0].suffix, '.png')
        self.assertEqual(self.photo.read_bytes(), self.before)
        self.assertEqual(AdjustmentStore(self.library.database).load(self.asset.id), seen[0][1])

    def test_missing_or_failed_renderer_does_not_export_unedited_pixels(self):
        self.edit()
        for renderer in (None, lambda *_: (_ for _ in ()).throw(RuntimeError('Decoder failure'))):
            result = self.library.export_assets([self.asset.id], self.output, render_photo=renderer)
            self.assertFalse(result.completed)
            self.assertTrue(result.errors)
            self.assertEqual(list(self.output.iterdir()), [])

    def test_invalid_encoded_result_and_cancel_leave_no_published_copy(self):
        self.edit()
        result = self.library.export_assets([self.asset.id], self.output, render_photo=lambda *_: b'not PNG')
        self.assertTrue(result.errors)
        self.assertFalse(result.completed)
        canceled = [False]
        def renderer(*_):
            canceled[0] = True
            return png()
        result = self.library.export_assets([self.asset.id], self.output,
                    render_photo=renderer, cancelled=lambda: canceled[0])
        self.assertFalse(result.completed)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_source_changed_during_render_is_refused(self):
        self.edit()
        def changed(*_):
            self.photo.write_bytes(self.before + b'changed')
            return png()
        result = self.library.export_assets([self.asset.id], self.output, render_photo=changed)
        self.assertFalse(result.completed)
        self.assertTrue(result.errors)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_collision_between_selection_and_publication_never_replaces_existing_file(self):
        self.edit()
        from prairie_apps import photos_backend
        original = photos_backend._unique_destination
        def race(directory, name):
            path = original(directory, name)
            path.write_bytes(b'Another writer owns this file')
            return path
        with patch.object(photos_backend, '_unique_destination', race):
            result = self.library.export_assets([self.asset.id], self.output, render_photo=lambda *_: png())
        self.assertFalse(result.completed)
        self.assertTrue(result.errors)
        self.assertEqual((self.output / 'Original.png').read_bytes(), b'Another writer owns this file')
        self.assertEqual(list(self.output.glob('*.partial')), [])

    def test_failed_file_flush_never_publishes_partial_output(self):
        self.edit()
        with patch('prairie_apps.photos_backend.os.fsync', side_effect=OSError('Injected flush failure')):
            result = self.library.export_assets([self.asset.id], self.output, render_photo=lambda *_: png())
        self.assertFalse(result.completed)
        self.assertTrue(result.errors)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_unknown_future_edit_is_not_silently_treated_as_original(self):
        self.assertTrue(has_edits({'future-colour': 1}))
        self.assertFalse(has_edits({'crop_rect': (0, 0, 1, 1), 'crop': 'original'}))


if __name__ == '__main__':
    unittest.main()
