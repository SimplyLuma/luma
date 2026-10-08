# SPDX-License-Identifier: Apache-2.0
"""Actual GTK/Pixbuf regressions for bounded previews and full-height groups.

Run with an actual display, GDK_SCALE=3 and the normal LumaUI runtime. No skip
is accepted. Inputs are generated, not copied from a user's photo library.
"""
from __future__ import annotations

import gc
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
from luma_appkit import MediaGrid, MediaItem
from luma_appkit.media_style import TextureLoader


def until(predicate, timeout=20):
    end = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(.005)
    return False


def image(path, width, height, colour=0xD34728FF):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.fill(colour)
    pixbuf.savev(str(path), 'png', [], [])


def close_loader(loader):
    if hasattr(loader, 'close'):
        loader.close()
    else:  # Exact baseline red uses the original loader, not a compatibility fake.
        loader._pool.shutdown(wait=True)
    until(lambda: not getattr(loader, '_active', ()), 20)


class CountedLoader(TextureLoader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.decode_calls = 0
        self.calls_lock = threading.Lock()
        self.gate = None

    def _decode(self, *args):
        with self.calls_lock:
            self.decode_calls += 1
        if self.gate is not None:
            self.gate.wait(10)
        return super()._decode(*args)


class MediaMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check() or Gdk.Display.get_default() is None:
            raise RuntimeError('a real native GTK display is required; no skip')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='luma-media-memory-')
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

    def test_panorama_has_long_edge_ceiling_and_real_pixels(self):
        path = self.root / 'panorama.png'
        image(path, 16000, 64)
        result = []
        self.loader.request(str(path), 2048, result.append)
        self.assertTrue(until(lambda: len(result) == 1))
        texture = result[0]
        self.assertIsInstance(texture, Gdk.Texture)
        self.assertLessEqual(max(texture.get_width(), texture.get_height()), 1024)
        self.assertLessEqual(texture.get_width() * texture.get_height(), 1024 * 1024)
        # The result is the supplied image, not an empty placeholder.
        pixbuf = Gdk.pixbuf_get_from_texture(texture)
        red, green, blue = pixbuf.get_pixels()[:3]
        self.assertGreater(red, green)
        self.assertGreater(green, blue)

    def test_cache_is_bounded_by_decoded_bytes(self):
        base = self.root / 'square.png'
        image(base, 1024, 1024)
        for n in range(20):
            path = self.root / f'cache-{n}.png'
            os.link(base, path)
            result = []
            self.loader.request(str(path), 1024, result.append)
            self.assertTrue(until(lambda: len(result) == 1))
            self.assertIsInstance(result[0], Gdk.Texture)
        retained = sum(t.get_width() * t.get_height() * 4 for t in self.loader._cache.values())
        self.assertLessEqual(retained, 64 * 1024 * 1024)
        self.assertGreater(retained, 0, 'no decoded result reached the cache')

    def test_same_pending_image_is_decoded_once(self):
        path = self.root / 'one.png'
        image(path, 128, 128)
        self.loader.gate = threading.Event()
        results = []
        for _ in range(12):
            self.loader.request(str(path), 128, results.append)
        self.assertTrue(until(lambda: self.loader.decode_calls > 0))
        self.loader.gate.set()
        self.assertTrue(until(lambda: len(results) == 12))
        self.assertTrue(all(isinstance(t, Gdk.Texture) for t in results))
        self.assertEqual(self.loader.decode_calls, 1)

    def test_worker_queue_cannot_accumulate_a_whole_catalog(self):
        base = self.root / 'tiny.png'
        image(base, 32, 32)
        self.loader.gate = threading.Event()
        results = []
        for n in range(128):
            path = self.root / f'queued-{n}.png'
            os.link(base, path)
            self.loader.request(str(path), 32, results.append)
        self.assertTrue(until(lambda: self.loader.decode_calls > 0))
        queued = self.loader._pool._work_queue.qsize()
        self.loader.gate.set()
        self.assertTrue(until(lambda: len(results) == 128))
        self.assertLessEqual(queued, 2, 'unbounded decoded-work executor queue')
        self.assertLessEqual(self.loader.decode_calls, 64)
        self.assertTrue(any(t is None for t in results), 'finite admission budget was not exercised')
        self.assertTrue(any(isinstance(t, Gdk.Texture) for t in results))

    def _page(self):
        base = self.root / 'page.png'
        image(base, 512, 512)
        items = []
        for n in range(200):
            path = self.root / f'photo-{n}.png'
            os.link(base, path)
            items.append(MediaItem(str(n), str(path)))
        grid = MediaGrid(items, kind='photo', columns=3, scrolls=False)
        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroller.set_child(grid)
        window = Gtk.Window(default_width=372, default_height=828)
        window.set_child(scroller)
        self.window = window
        return grid, scroller, window

    def test_unallocated_page_does_not_start_200_decodes(self):
        grid, _scroller, _window = self._page()
        self.assertEqual(len(grid._flat), 200)
        self.assertEqual(self.loader.decode_calls, 0)
        self.assertTrue(all(tile._picture is None for tile in grid._flat))

    def test_real_viewport_scroll_releases_old_pixels_and_loads_new(self):
        grid, scroller, window = self._page()
        window.present()
        self.assertTrue(until(lambda: grid._flat[0]._picture is not None))
        self.assertEqual(window.get_scale_factor(), 3, 'exercise the physical HiDPI mode')
        retained = [tile for tile in grid._flat if tile._picture is not None]
        self.assertGreater(len(retained), 0)
        self.assertLessEqual(len(retained), 36)
        self.assertLessEqual(self.loader.decode_calls, 36)
        adjustment = scroller.get_vadjustment()
        self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        self.assertTrue(until(lambda: grid._flat[-1]._picture is not None))
        self.assertIsNone(grid._flat[0]._picture, 'offscreen tile still owns decoded pixels')
        self.assertLessEqual(sum(tile._picture is not None for tile in grid._flat), 36)
        window.set_visible(False)
        self.assertTrue(until(lambda: all(tile._picture is None for tile in grid._flat)))
        self.assertFalse(self.loader._pending, 'unmapped tile subscriptions survived')

    def test_cancelled_queued_subscription_does_not_deliver(self):
        path = self.root / 'cancel.png'
        image(path, 64, 64)
        self.loader.gate = threading.Event()
        results = []
        request = self.loader.request(str(path), 64, results.append)
        self.assertTrue(until(lambda: self.loader.decode_calls > 0))
        self.assertIsNotNone(request)
        request.cancel()
        self.loader.gate.set()
        self.assertTrue(until(lambda: not self.loader._active))
        self.assertEqual(results, [])
        self.assertEqual(len(self.loader._cache), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
