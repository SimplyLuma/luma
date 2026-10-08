# SPDX-License-Identifier: Apache-2.0
"""Native cover admission, pixel lifetime and real artwork regression.

Run on the actual GTK4 high-DPI display. The >64 visible-covers case exercises
saturation and eventual real pixels, rather than merely counting callbacks.
"""
from __future__ import annotations

import gc
import os
from pathlib import Path
import tempfile
import threading
import unittest

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gtk
from luma_appkit import CoverArt
from luma_appkit.media_style import TextureLoader
from media_memory import CountedLoader, close_loader, image, until


class CoverMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check() or Gdk.Display.get_default() is None:
            raise RuntimeError('a real native GTK display is required; no skip')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='luma-cover-memory-')
        self.root = Path(self.temp.name)
        self.loader = CountedLoader()
        self.previous = TextureLoader._shared
        TextureLoader._shared = self.loader
        self.window = None

    def tearDown(self):
        if self.window is not None:
            self.window.destroy()
        if self.loader.gate is not None:
            self.loader.gate.set()
        close_loader(self.loader)
        TextureLoader._shared = self.previous
        self.temp.cleanup()
        gc.collect()

    def covers(self, count, columns, *, shape='album', size='mini'):
        base = self.root / 'cover.png'
        image(base, 128, 128)
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True)
        covers = []
        for n in range(count):
            path = self.root / f'cover-{n}.png'
            os.link(base, path)
            cover = CoverArt(f'Album {n}', 'Test artist', picture=str(path), shape=shape, size=size)
            grid.attach(cover, n % columns, n // columns, 1, 1)
            covers.append(cover)
        return grid, covers

    def show(self, child):
        self.window = Gtk.Window(default_width=372, default_height=828)
        self.window.set_child(child)
        self.window.present()

    def test_more_than_64_visible_covers_eventually_show_real_artwork(self):
        # Hold workers before construction too, so the exact preceding loader
        # can reach its64-key limit; real pixels after admission are the gate.
        self.loader.gate = threading.Event()
        grid, covers = self.covers(96, 12)
        self.show(grid)
        self.assertTrue(until(lambda: len(self.loader._pending) == 64))
        self.assertTrue(any(cover._picture is None for cover in covers))
        self.assertLessEqual(self.loader._pool._work_queue.qsize(), 2)
        self.loader.gate.set()
        self.assertTrue(until(lambda: all(cover._picture is not None for cover in covers)))
        self.assertEqual(self.window.get_scale_factor(), 3)
        self.assertTrue(all(isinstance(cover._picture, Gdk.Texture) for cover in covers))
        self.assertTrue(all(not cover.generated for cover in covers))
        # The supplied red picture, not a generated cover or an empty texture.
        red, green, blue = Gdk.pixbuf_get_from_texture(covers[-1]._picture).get_pixels()[:3]
        self.assertGreater(red, green)
        self.assertGreater(green, blue)
        retained = sum(t.get_width() * t.get_height() * 4 for t in self.loader._cache.values())
        self.assertLessEqual(retained, 64 * 1024 * 1024)
        self.assertFalse(self.loader._pending)
        self.assertFalse(self.loader._available)

    def test_real_cover_viewport_releases_and_reloads_on_scroll(self):
        grid, covers = self.covers(120, 3, shape='book', size='tile')
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_child(grid)
        self.show(scroller)
        self.assertTrue(until(lambda: covers[0]._picture is not None))
        self.assertIsNone(covers[-1]._picture)
        self.assertLessEqual(sum(cover._picture is not None for cover in covers), 24)
        adjustment = scroller.get_vadjustment()
        self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        self.assertTrue(until(lambda: covers[-1]._picture is not None))
        self.assertIsNone(covers[0]._picture, 'offscreen cover retains decoded pixels')
        self.assertTrue(covers[0].generated)
        self.assertLessEqual(sum(cover._picture is not None for cover in covers), 24)
        adjustment.set_value(0)
        self.assertTrue(until(lambda: covers[0]._picture is not None))
        self.window.set_visible(False)
        self.assertTrue(until(lambda: all(cover._picture is None for cover in covers)))
        self.assertFalse(self.loader._pending)
        self.assertFalse(self.loader._available)

    def test_unmap_saturated_covers_cancels_capacity_subscriptions(self):
        grid, covers = self.covers(96, 12)
        self.loader.gate = threading.Event()
        self.show(grid)
        self.assertTrue(until(lambda: len(self.loader._pending) == 64 and self.loader._available))
        self.window.set_visible(False)
        self.assertTrue(until(lambda: not self.loader._pending and not self.loader._available))
        self.loader.gate.set()
        self.assertTrue(until(lambda: not self.loader._active))
        self.assertTrue(all(cover._picture is None for cover in covers))
        self.assertFalse(self.loader._cache)

    def test_unreadable_artwork_does_not_retry_every_snapshot(self):
        cover = CoverArt('Missing', picture=str(self.root / 'absent.png'))
        self.show(cover)
        self.assertTrue(until(lambda: self.loader.decode_calls > 0 and not self.loader._active))
        before = self.loader.decode_calls
        for _ in range(10):
            cover.queue_draw()
            until(lambda: False, .05)
        self.assertEqual(self.loader.decode_calls, before)
        self.assertTrue(cover.generated)
        self.assertFalse(self.loader._available)


if __name__ == '__main__':
    unittest.main(verbosity=2)
