# SPDX-License-Identifier: Apache-2.0
"""Realized PDF thumbnail ink stays visible on fixed paper in every theme.

Run with private bus/X11/XDG and a memory settings backend. This is a small
native widget regression, not a replacement for the canonical visual gate.
"""
import os
import tempfile
import time
import cairo
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')
from gi.repository import Adw, Gio, GLib, Graphene, Gtk
from luma_appkit import install_appkit
from luma_viewer.page_navigation import PaperFace

dark = os.environ['VIEWER_CHECK_THEME'] == 'dark'
Gio.Settings.new('org.gnome.desktop.interface').set_string('color-scheme', 'prefer-dark' if dark else 'prefer-light')
if Gio.SettingsSchemaSource.get_default().lookup('org.project_luma.shell-state', True):
    Gio.Settings.new('org.project_luma.shell-state').set_string('surface-treatment', 'dark' if dark else 'light')
Adw.init()
manager = Adw.StyleManager.get_default()
manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
install_appkit()
face = PaperFace(lambda: True)
window = Gtk.Window(child=face)
window.present()
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    while GLib.MainContext.default().pending():
        GLib.MainContext.default().iteration(False)
    if face.get_mapped() and face.get_width() == 54:
        break
    time.sleep(.01)
assert face.get_mapped() and (face.get_width(), face.get_height()) == (54, 70)
assert manager.get_dark() == dark
if os.environ.get('ADW_DEBUG_HIGH_CONTRAST') == '1':
    assert manager.get_high_contrast()
snapshot = Gtk.Snapshot()
Gtk.WidgetPaintable.new(face).snapshot(snapshot, 54, 70)
texture = window.get_renderer().render_texture(snapshot.to_node(), Graphene.Rect().init(0, 0, 54, 70))
with tempfile.TemporaryDirectory() as scratch:
    path = os.path.join(scratch, 'thumbnail.png')
    assert texture.save_to_png(path)
    surface = cairo.ImageSurface.create_from_png(path)
    data, stride = surface.get_data(), surface.get_stride()
    def rgb(x, y):
        offset = y * stride + x * 4
        return tuple(data[offset + channel] for channel in (2, 1, 0))
    paper = rgb(40, 60)
    for index in range(4):
        ink = rgb(10, 11 + index * 7)
        # The v70 paper lines differ from the paper by more than 32 levels.
        # The invalid role inherited dark window ink (242/243/245) on white
        # paper; this catches that actual rendered regression, including HC.
        assert max(abs(a - b) for a, b in zip(paper, ink)) > 32, (dark, index, paper, ink)
        print('NATIVE THUMBNAIL', 'dark' if dark else 'light', 'HC', manager.get_high_contrast(), index, paper, ink, flush=True)
window.destroy()
while GLib.MainContext.default().pending():
    GLib.MainContext.default().iteration(False)
assert not window.get_visible()
print('CLOSED THUMBNAIL: PASS', flush=True)
