#!/usr/bin/python3
"""Designed Depot GTK matrix. Explicit test catalogue, no package mutations."""
import os
from pathlib import Path
import sys
import time
import tempfile
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="depot-smoke-config-")
from unittest.mock import patch
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk
from luma_depot import native
from luma_depot.window import DepotApplication, DepotWindow
from luma_installer.depot_catalog import load_catalog


def settle(seconds=.3):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        while GLib.MainContext.default().iteration(False):pass
        time.sleep(.01)


def widgets(root):
    yield root
    child=root.get_first_child()
    while child:
        yield from widgets(child)
        child=child.get_next_sibling()


catalog=load_catalog(Path(sys.argv[1]))
app=DepotApplication();assert app.register(None)
with patch.object(native,'load_catalog',return_value=catalog):
    for width in (360,500,1024,1440):
        window=DepotWindow(app);window.set_default_size(width,760);window.present();settle(1)
        assert abs(window.get_surface().get_width()-width)<=2,(width,window.get_surface().get_width())
        assert window.catalogue and len(window.catalogue.apps)>=len(catalog.applications)
        pending=[a for a in window.catalogue.apps if a.app_id.startswith('catalog:')]
        assert any(a.installable for a in pending)
        assert any(not a.installable for a in pending)
        for target in (next(a for a in pending if a.installable),
                       next(a for a in pending if not a.installable)):
            window.go('app',target.app_id);settle()
            buttons=[w for w in widgets(window) if isinstance(w,Gtk.Button) and w.get_label()=='Install']
            publisher=[w for w in widgets(window) if isinstance(w,Gtk.Button) and w.get_label()=='Get from Publisher']
            if target.installable:
                assert len(buttons)==1 and buttons[0].get_sensitive()
            else:
                # An app Depot cannot install either links to its publisher or
                # shows an Install button that cannot be pressed; never both.
                assert (len(publisher)==1 and not buttons) or (len(buttons)==1 and not buttons[0].get_sensitive())
        window.go('mine');settle()
        window.go('updates');settle()
        window.go('home');settle()
        if os.environ.get('DEPOT_SMOKE_IMAGES'):
            paintable=Gtk.WidgetPaintable.new(window.get_child());snapshot=Gtk.Snapshot.new()
            paintable.snapshot(snapshot,window.get_width(),window.get_height())
            texture=window.get_renderer().render_texture(snapshot.to_node(),None)
            texture.save_to_png(str(Path(os.environ['DEPOT_SMOKE_IMAGES'])/f'depot-{width}.png'))
        window.close();settle()
        print(f'Depot designed GTK smoke passed at {width}px')
app.quit()
