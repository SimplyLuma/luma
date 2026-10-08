# SPDX-License-Identifier: GPL-3.0-only
"""A stale GTK application default must not override packaged Viola artwork."""
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, Gtk
from integrated_window import IntegratedWindow


class BrowserIdentity(unittest.TestCase):
    def test_frame_uses_owned_icon_after_application_startup(self):
        application = Adw.Application(application_id='com.rhyme.viola',
                                     flags=Gio.ApplicationFlags.NON_UNIQUE)
        application.register(None)
        previous = Gtk.Window.get_default_icon_name()
        # The getter may return None, but the GTK setter requires a string.
        self.addCleanup(Gtk.Window.set_default_icon_name, previous or '')
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        paths = theme.get_search_path()
        self.addCleanup(theme.set_search_path, paths)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        destination = Path(folder.name) / 'com.rhyme.viola.browser.svg'
        shutil.copyfile(Path(__file__).parent / 'assets/org.projectluma.Viola.NativeIntegration.svg',
                        destination)
        theme.add_search_path(folder.name)
        self.assertTrue(theme.has_icon('com.rhyme.viola.browser'))
        Gtk.Window.set_default_icon_name('com.rhyme.viola')
        host = IntegratedWindow(application, SimpleNamespace(quit=lambda: None),
                                lambda *_args: None, [])
        self.addCleanup(host.destroy)
        self.assertEqual(Gtk.Window.get_default_icon_name(), 'com.rhyme.viola.browser')
        images = []
        def inspect(widget):
            if isinstance(widget, Gtk.Image):
                images.append(widget.get_icon_name())
            child = widget.get_first_child()
            while child:
                inspect(child)
                child = child.get_next_sibling()
        inspect(host.identity)
        self.assertIn('com.rhyme.viola.browser', images)
        self.assertNotIn('com.rhyme.viola', images)


if __name__ == '__main__':
    unittest.main()
