#!/usr/bin/python3
"""Long album credits must not determine grid columns or detach cover sizing."""
import time

from luma_tide.application import AlbumGrid, _album_tile, _fill_album_tile
from gi.repository import Adw, GLib, Gtk
from luma_appkit import install_appkit


def settle():
    until = time.monotonic() + 0.2
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.01)


Adw.init()
install_appkit()
album = ("A very long concert title at a very long venue " * 5,
         "An artist with an unusually long credit " * 5, 1977, 12, None)
tile = _album_tile()
_fill_album_tile(tile, album)
minimum, natural, *_ = tile.measure(Gtk.Orientation.HORIZONTAL, -1)
assert natural < 220, (minimum, natural)

for scheme in (Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK):
    Adw.StyleManager.get_default().set_color_scheme(scheme)
    for width in (360, 420, 500, 1024, 1440):
        grid = AlbumGrid(None)
        grid.set_albums([album] * 24)
        window = Gtk.Window(default_width=width, default_height=640, child=grid)
        try:
            window.present()
            settle()
            child = grid.view.get_first_child()
            while child is not None and child.get_first_child() is None:
                child = child.get_next_sibling()
            assert child is not None
            tile = child.get_first_child()
            assert tile.get_width() < 330, (width, tile.get_width())
            assert abs(tile.art.get_width() - tile.get_width()) <= 1
            assert abs(tile.art.get_width() - tile.art.get_height()) <= 1
            assert tile.title.get_width() <= tile.get_width()
            print(scheme.value_nick, width, tile.get_width(), flush=True)
        finally:
            window.destroy()
            settle()
