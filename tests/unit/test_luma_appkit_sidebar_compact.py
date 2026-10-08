# SPDX-License-Identifier: Apache-2.0
"""Sidebar layout thresholds do not change phone presentation thresholds."""
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
try:
    import gi
    gi.require_version('Gtk', '4.0')
    from gi.repository import Gdk, GLib, Gtk
    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
    if HAVE_DISPLAY:
        from luma_appkit import SidebarToggle, install_appkit
except (ImportError, ValueError):
    HAVE_DISPLAY = False


def settle():
    end = time.monotonic() + .4
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


@unittest.skipUnless(HAVE_DISPLAY, 'needs a GTK display')
class DrawerThreshold(unittest.TestCase):
    def test_default_and_compact_preserve_sidebar_and_phone_modes(self):
        install_appkit()
        for threshold in (560, 901):
            sidebar = Gtk.Box()
            sidebar.append(Gtk.Label(label='Sidebar content'))
            body = Gtk.Box()
            body.append(sidebar)
            toggle = SidebarToggle(sidebar, drawer_below=threshold)
            body.append(toggle)
            window = Gtk.Window(child=body, default_height=200)
            window.present()
            for width in (1024, 720, 500, 720, 1180):
                window.set_default_size(width, 200)
                settle()
                self.assertEqual(sidebar.get_mapped(), width >= threshold)
                self.assertEqual(toggle.has_css_class('phone'), width < 560)
                self.assertEqual(toggle.has_css_class('drawer-mode'), width < threshold)
                self.assertEqual(toggle.shown, width >= threshold)
            window.destroy()

    def test_hidden_sidebar_releases_column_gap_and_restores(self):
        install_appkit()
        sidebar = Gtk.Box(width_request=120)
        sidebar.append(Gtk.Label(label='Sidebar'))
        content = Gtk.Box(hexpand=True)
        body = Gtk.Box(spacing=8)
        body.append(sidebar)
        body.append(content)
        toggle = SidebarToggle(sidebar)
        window = Gtk.Window(child=body, default_width=720, default_height=200)
        window.present()
        settle()
        self.assertTrue(toggle._revealer.get_visible())
        sidebar.set_visible(False)
        settle()
        ok, bounds = content.compute_bounds(body)
        self.assertTrue(ok)
        self.assertEqual(bounds.get_x(), 0)
        self.assertEqual(bounds.get_width(), body.get_width())
        sidebar.set_visible(True)
        settle()
        self.assertTrue(toggle._revealer.get_visible())
        toggle.set_active(False)
        settle()
        self.assertFalse(toggle._revealer.get_visible())
        toggle.set_active(True)
        settle()
        self.assertTrue(toggle._revealer.get_visible())
        window.destroy()

    def test_open_drawer_preserves_requested_search_focus(self):
        install_appkit()
        sidebar=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar.append(Gtk.Button(label='First row'))
        search=Gtk.Text(placeholder_text='Search')
        sidebar.append(search)
        body=Gtk.Box();body.append(sidebar);body.append(Gtk.Box(hexpand=True))
        toggle=SidebarToggle(sidebar,drawer_below=721)
        body.append(toggle)
        window=Gtk.Window(child=body,default_width=600,default_height=300)
        window.present();settle()
        toggle.toggle(initial_focus=search);settle()
        self.assertTrue(toggle.shown)
        self.assertIs(window.get_focus(),search)
        search.set_text('query');self.assertEqual(search.get_text(),'query')
        toggle.toggle();settle();self.assertFalse(toggle.shown)
        window.destroy()

    def test_rejects_invalid_layout_threshold(self):
        for value in (0, -1, True, 2.5):
            with self.assertRaises(ValueError):
                SidebarToggle(Gtk.Box(), drawer_below=value)


if __name__ == '__main__':
    unittest.main()
