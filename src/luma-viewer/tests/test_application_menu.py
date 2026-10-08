# SPDX-License-Identifier: Apache-2.0
"""Exercise the real responsive Viewer and its shared command controls."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from luma_appkit import install_appkit
from luma_viewer.application import ViewerWindow
from luma_viewer.recents import Recents


class ApplicationMenuTests(unittest.TestCase):
    def settle(self, condition):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            if condition():
                return
            time.sleep(.01)
        self.fail('Viewer native layout or menu action did not settle')

    def test_menu_keeps_recent_navigation_without_sidebar_toggle(self):
        self.assertIsNotNone(Gdk.Display.get_default(), 'Native GTK display is required')
        app = Adw.Application(application_id='org.projectluma.ViewerMenuTest',
                              flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.assertTrue(app.register(None))
        install_appkit()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Recents(root / 'history', root / 'cache')
            with patch('luma_viewer.composition.Recents', return_value=store):
                window = ViewerWindow(app)
            try:
                window.present()
                self.settle(lambda: window.get_mapped())
                theme = Gtk.IconTheme.get_for_display(window.get_display())
                for group in window.commands.visible_groups(menu=True):
                    for command in group.commands:
                        self.assertTrue(command.icon and command.icon.startswith('lumaui-'), command.id)
                        self.assertTrue(theme.has_icon(command.icon), command.icon)
                self.assertNotIn('viewer.sidebar', window.commands._commands)
                for width in (1180, 1024, 500, 360, 1180):
                    window.set_default_size(width, 740)
                    try:
                        # GtkWindow's content allocation excludes native CSD
                        # shadow extents. The requested size belongs to its
                        # GdkSurface; assert that actual surface, not a guessed
                        # symmetric decoration offset.
                        self.settle(lambda: window.get_surface().get_width() == width)
                    except AssertionError:
                        self.fail(f'Viewer requested {width}, surface {window.get_surface().get_width()}, '
                                  f'content {window.get_width()}, '
                                  f'default {window.get_default_size()}, '
                                  f'content minimum {window.layout.measure(Gtk.Orientation.HORIZONTAL, -1)}')
                    self.assertFalse(window.sidebar_toggle.get_visible(), width)
                    self.assertTrue(window.commands.invoke('viewer.recents'))
                    self.settle(lambda: window.sidebar_toggle.shown)
                    if width < 721:
                        self.assertTrue(window.sidebar.get_mapped())
                        self.assertIsNotNone(window.sidebar_toggle._drawer)
                        window.sidebar_toggle._drawer.cancel()
                        self.settle(lambda: not window.sidebar_toggle.shown)
                    self.assertFalse(window.sidebar_toggle.get_visible(), width)
                self.assertEqual(store.entries, [])
                self.assertFalse((root / 'history' / 'recents.json').exists())
            finally:
                window.destroy()
                app.quit()
