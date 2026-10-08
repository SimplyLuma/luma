#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Lookup genuine signed-RPM Firefox artwork through an extracted Prairie RPM.

The optional Flatpak identity uses the exact same upstream PNG as a private
export fixture; it is not evidence of a Flatpak installation.
"""
import hashlib
import pathlib
import shutil
import sys
import tempfile
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gtk
Gtk.init()
candidate, upstream = map(pathlib.Path, sys.argv[1:3])
assert (candidate / 'Prairie/index.theme').is_file()
with tempfile.TemporaryDirectory(prefix='firefox-owner-') as temporary:
    export = pathlib.Path(temporary)
    folder = export / 'hicolor/64x64/apps'
    folder.mkdir(parents=True)
    shutil.copyfile('/usr/share/icons/hicolor/index.theme', export / 'hicolor/index.theme')
    source = upstream / 'hicolor/48x48/apps/firefox.png'
    assert source.is_file(), 'actual Fedora Firefox RPM icon missing'
    shutil.copyfile(source, folder / 'org.mozilla.firefox.png')
    theme = Gtk.IconTheme.new()
    theme.set_search_path([str(candidate), str(upstream), str(export), '/usr/share/icons'])
    theme.set_theme_name('Prairie')
    for size in (16, 24, 32, 48, 64, 128):
        paint = theme.lookup_icon('firefox', None, size, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.PRELOAD)
        path = pathlib.Path(paint.get_file().get_path())
        assert path.is_relative_to(upstream), (size, path)
        texture = Gdk.Texture.new_from_filename(str(path))
        assert texture.get_width() > 0 and texture.get_height() > 0
        print('PASS real Firefox RPM owner lookup and decode', size, path,
              hashlib.sha256(path.read_bytes()).hexdigest())
    paint = theme.lookup_icon('org.mozilla.firefox', None, 64, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags.PRELOAD)
    path = pathlib.Path(paint.get_file().get_path())
    assert path == folder / 'org.mozilla.firefox.png', path
    assert path.read_bytes() == source.read_bytes()
    print('PASS exported Flatpak identity retains unchanged upstream artwork')
    for alias in ('firefox', 'org.mozilla.firefox'):
        assert not (candidate / 'Prairie/scalable/apps' / (alias + '.svg')).exists(), alias
