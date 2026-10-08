#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Real Tide covers and backdrop load through finite shared preview admission.

Temporary local images only. No media playback, account, network or user store.
"""
from pathlib import Path
import os
import tempfile
import threading
import sys
import traceback
import time

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk
from luma_appkit import CoverArt, add_style_sheet, install_appkit, install_lumaui
from luma_appkit.media_style import TextureLoader
from luma_tide.presentation import Album
from luma_tide.ui_parts import AlbumCover, PlayingBackdrop
from luma_tide.resources import stylesheet_path


native_errors = []
original_excepthook = sys.excepthook


def native_exception(kind, value, traceback_object):
    # PyGObject reports virtual-method errors through sys.excepthook and may
    # otherwise let the process exit0. Every native callback error fails this
    # real-widget test, including drawing callbacks.
    native_errors.append(value)
    traceback.print_exception(kind, value, traceback_object)


sys.excepthook = native_exception


def assert_native_callbacks():
    assert not native_errors, f'native GTK callback raised: {native_errors}'


def settle(predicate, seconds=30):
    end = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        assert_native_callbacks()
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('actual Tide artwork did not reach expected native state')


class HeldLoader(TextureLoader):
    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.calls = 0
        self.lock = threading.Lock()

    def _decode(self, key):
        with self.lock:
            self.calls += 1
        self.gate.wait(20)
        super()._decode(key)


class ObservedBackdrop(PlayingBackdrop):
    __gtype_name__ = 'TideArtworkAdmissionBackdrop'

    def __init__(self, album):
        self.real_frames = 0
        super().__init__(album)

    def do_snapshot(self, snapshot):
        super().do_snapshot(snapshot)
        if self.texture is not None:
            self.real_frames += 1


assert Gtk.init_check() and Gdk.Display.get_default() is not None
Adw.init()
install_appkit()
install_lumaui()
add_style_sheet(str(stylesheet_path()))
old_loader = TextureLoader._shared
loader = HeldLoader()
TextureLoader._shared = loader
window = None
try:
    with tempfile.TemporaryDirectory(prefix='tide-artwork-admission-') as temp:
        root = Path(temp)
        base = root / 'artwork.png'
        image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 128, 128)
        image.fill(0xD34728FF)
        image.savev(str(base), 'png', [], [])
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True)
        covers, loaded = [], []
        for index in range(96):
            path = root / f'album-{index}.png'
            os.link(base, path)
            album = Album(str(index), f'Album {index}', 'Test artist', 2026, artwork=path.as_uri())
            cover = AlbumCover(album, 26, on_loaded=loaded.append)
            assert isinstance(cover, CoverArt)
            grid.attach(cover, index % 12, index // 12, 1, 1)
            covers.append(cover)
        assert loader.calls == 0, 'Tide eagerly decodes unallocated album covers'
        window = Gtk.Window(default_width=372, default_height=828)
        window.set_child(grid)
        window.present()
        settle(lambda: len(loader._pending) == 64)
        assert window.get_scale_factor() == 3
        assert loader._pool._work_queue.qsize() <= 2
        loader.gate.set()
        settle(lambda: all(not cover.generated for cover in covers))
        assert len(loaded) >= 96, 'real Tide artwork-lighting callback was lost'
        assert all(isinstance(texture, Gdk.Texture) for texture in loaded)
        assert not loader._pending and not loader._available
        window.set_visible(False)
        settle(lambda: all(cover.generated for cover in covers))
        assert not loader._pending and not loader._available
        window.set_child(None)
        backdrop = ObservedBackdrop(Album('room', 'Room', 'Test artist', 2026, artwork=base.as_uri()))
        window.set_child(backdrop)
        window.present()
        settle(lambda: isinstance(backdrop.texture, Gdk.Texture) and backdrop.real_frames > 0)
        red, green, blue = Gdk.pixbuf_get_from_texture(backdrop.texture).get_pixels()[:3]
        assert red > green > blue
        window.set_visible(False)
        settle(lambda: backdrop.texture is None)
        assert not loader._pending and not loader._available
        assert_native_callbacks()
        print('PASS:96 Tide covers, real artwork-lighting callbacks, native styled backdrop frame and unmap')
finally:
    if window is not None:
        window.destroy()
    loader.gate.set()
    loader.close()
    TextureLoader._shared = old_loader
    sys.excepthook = original_excepthook
    assert_native_callbacks()
