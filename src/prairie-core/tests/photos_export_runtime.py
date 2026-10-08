#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Actual shared GSK crop/rotation/colour PNGs reopened through GdkPixbuf.

Run on a real private GTK display. No fake image renderer or provider is used.
This is a source/native pixel proof, not installed signed Photos acceptance.
"""
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Gsk', '4.0')
from gi.repository import GdkPixbuf, GLib, Gsk, Gtk
from prairie_apps.photos_backend import PhotoLibrary
from prairie_apps.photos_adjustments import AdjustmentStore
from prairie_apps.photos_content import decode_export_photo, edited_photo_node


def pixels(image):
    raw = image.get_pixels()
    return [[tuple(raw[y * image.get_rowstride() + x * image.get_n_channels():
                       y * image.get_rowstride() + x * image.get_n_channels() + 3])
             for x in range(image.get_width())] for y in range(image.get_height())]


class ActualExportTests(unittest.TestCase):
    def setUp(self):
        Gtk.init()
        self.window = Gtk.Window()
        self.window.present()
        self.assertIsNotNone(self.window.get_surface())
        self.renderer = Gsk.CairoRenderer.new()
        self.assertTrue(self.renderer.realize(self.window.get_surface()))
        self.addCleanup(self.renderer.unrealize)
        self.addCleanup(self.window.close)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        source = self.root / 'pictures'
        source.mkdir()
        self.photo = source / 'Known pixels.png'
        rgb = bytes(v for y in range(6) for x in range(8) for v in (x * 20, y * 30, 40))
        self.image = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(rgb), GdkPixbuf.Colorspace.RGB, False, 8, 8, 6, 24)
        self.assertTrue(self.image.savev(str(self.photo), 'png', [], []))
        self.original = self.photo.read_bytes()
        self.library = PhotoLibrary(self.root / 'catalog.sqlite3')
        self.source = self.library.add_source(source)
        self.assertEqual(self.library.scan_source(self.source.id).added, 1)
        self.asset = self.library.assets()[0]

    def render(self, path, values):
        node, bounds = edited_photo_node(decode_export_photo(path), values)
        texture = self.renderer.render_texture(node, bounds)
        return bytes(texture.save_to_png_bytes().get_data())

    def export(self, values):
        AdjustmentStore(self.library.database).update(self.asset.id, values)
        result = self.library.export_assets([self.asset.id], self.root / 'exports', render_photo=self.render)
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(len(result.completed), 1)
        self.assertEqual(self.photo.read_bytes(), self.original)
        return GdkPixbuf.Pixbuf.new_from_file(str(result.completed[0]))

    def assert_pixels_equal(self, actual, expected, tolerance=1):
        self.assertEqual((actual.get_width(), actual.get_height()), (expected.get_width(), expected.get_height()))
        a, b = pixels(actual), pixels(expected)
        self.assertTrue(all(abs(x - y) <= tolerance for ar, br in zip(a, b)
                            for ap, bp in zip(ar, br) for x, y in zip(ap, bp)), (a, b))

    def test_free_crop_and_every_rotation_flip_reopen_correct_pixels(self):
        crop = self.image.new_subpixbuf(2, 1, 4, 3)
        rotations = {0: None, 90: GdkPixbuf.PixbufRotation.CLOCKWISE,
                     180: GdkPixbuf.PixbufRotation.UPSIDEDOWN, 270: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE}
        for rotation, kind in rotations.items():
            for flip in (False, True):
                with self.subTest(rotation=rotation, flip=flip):
                    expected = crop.flip(True) if flip else crop
                    if kind is not None:
                        expected = expected.rotate_simple(kind)
                    self.assert_pixels_equal(self.export({'crop_rect': [.25, 1 / 6, .5, .5], 'rot': rotation, 'flip': flip}), expected)

    def test_square_crop_uses_existing_preview_region_without_ui_border(self):
        exported = self.export({'crop': 'square'})
        self.assert_pixels_equal(exported, self.image.new_subpixbuf(1, 0, 6, 6))

    def test_colour_matrix_is_the_actual_shared_preview_and_not_original(self):
        # e=100 doubles source RGB; alpha/UI backgrounds must not enter PNG.
        exported = self.export({'e': 100})
        expected = [[tuple(min(255, channel * 2) for channel in p) for p in row]
                    for row in pixels(self.image)]
        actual = pixels(exported)
        self.assertEqual((exported.get_width(), exported.get_height()), (8, 6))
        self.assertTrue(all(abs(x - y) <= 1 for ar, er in zip(actual, expected)
                            for ap, ep in zip(ar, er) for x, y in zip(ap, ep)), (actual, expected))
        self.assertNotEqual(actual, pixels(self.image))

    def test_exported_edit_survives_catalog_reopen_and_original_is_exact(self):
        values = {'crop_rect': [.25, 1 / 6, .5, .5], 'rot': 90, 'flip': True}
        first = self.export(values)
        reopened = PhotoLibrary(self.library.database)
        result = reopened.export_assets([self.asset.id], self.root / 'second', render_photo=self.render)
        self.assertFalse(result.errors)
        second = GdkPixbuf.Pixbuf.new_from_file(str(result.completed[0]))
        self.assert_pixels_equal(first, second, tolerance=0)
        self.assertEqual(self.photo.read_bytes(), self.original)
        self.assertEqual(AdjustmentStore(reopened.database).load(self.asset.id), values)

    def test_future_pixel_setting_is_refused_by_installed_shared_renderer(self):
        pixbuf = decode_export_photo(self.photo)
        with self.assertRaisesRegex(ValueError, 'unknown image adjustments'):
            edited_photo_node(pixbuf, {'future-colour': 1})
        self.assertEqual(self.photo.read_bytes(), self.original)

    def test_actual_photos_window_worker_handoff_exports_and_reports_renderer_error(self):
        from prairie_apps.photos import PhotosApplication, PhotosWindow
        from unittest.mock import patch
        app = PhotosApplication()
        self.assertTrue(app.register(None))
        window = PhotosWindow(app, library=self.library)
        window.present()
        self.addCleanup(window.close)
        pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(pool.shutdown)
        def complete(future):
            loop = GLib.MainLoop()
            outcome = {'timed_out': False}
            def observe():
                if future.done():
                    loop.quit()
                    return GLib.SOURCE_REMOVE
                return GLib.SOURCE_CONTINUE
            timer = GLib.timeout_add(20, observe)
            def timeout():
                outcome['timed_out'] = True
                loop.quit()
                return GLib.SOURCE_REMOVE
            guard = GLib.timeout_add_seconds(10, timeout)
            loop.run()
            if outcome['timed_out']:
                GLib.source_remove(timer)
            else:
                GLib.source_remove(guard)
            self.assertFalse(outcome['timed_out'], 'Real GTK worker handoff deadline')
            return future.result()
        # Import/save/load is real catalog state; the product worker calls the
        # real window renderer while its real GTK main loop handles completion.
        values = {'crop_rect': [.25, 1 / 6, .5, .5], 'rot': 90}
        AdjustmentStore(self.library.database).update(self.asset.id, values)
        future = pool.submit(self.library.export_assets, [self.asset.id], self.root / 'handoff',
                             render_photo=window._render_export_copy)
        result = complete(future)
        self.assertFalse(result.errors, result.errors)
        decoded = GdkPixbuf.Pixbuf.new_from_file(str(result.completed[0]))
        self.assert_pixels_equal(decoded, self.image.new_subpixbuf(2, 1, 4, 3).rotate_simple(GdkPixbuf.PixbufRotation.CLOCKWISE))
        # A genuine owner-side failure must leave the worker with a visible
        # error, no successful result, and no publication; never a dead wait.
        with patch('prairie_apps.photos.edited_photo_node', side_effect=RuntimeError('Injected GTK render failure')):
            failed = complete(pool.submit(self.library.export_assets, [self.asset.id], self.root / 'failed-handoff',
                                           render_photo=window._render_export_copy))
        self.assertFalse(failed.completed)
        self.assertTrue(any('Injected GTK render failure' in message for message in failed.errors))
        self.assertEqual(list((self.root / 'failed-handoff').iterdir()), [])
        self.assertEqual(self.photo.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
