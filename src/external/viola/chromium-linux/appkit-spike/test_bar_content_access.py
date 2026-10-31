# SPDX-License-Identifier: GPL-3.0-only
"""Mapped GTK chrome regressions; no browser-engine qualification claim."""
from pathlib import Path
import unittest
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, Gio, GLib, Gtk
from luma_appkit import install_appkit
from integrated_window import IntegratedWindow


def settle():
    loop = GLib.MainLoop()
    GLib.timeout_add(100, lambda: loop.quit() or False)
    loop.run()


class BarContentAccess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = Gtk.Application(application_id='org.projectluma.Viola.ChromeRegression',
                                          flags=Gio.ApplicationFlags.NON_UNIQUE)
        cls.application.register(None)
        install_appkit()
        provider = Gtk.CssProvider()
        provider.load_from_path(str(Path(__file__).with_name('native_sidebar.css')))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def setUp(self):
        self.window = IntegratedWindow(self.application, GLib.MainLoop(), lambda *_: None, [])
        self.addCleanup(self.window.destroy)
        # AppKit sizes fresh windows from the monitor; a small headless
        # monitor may correctly select the phone layout. These cases exercise
        # desktop chrome, so request and verify a desktop allocation explicitly.
        self.window.set_default_size(1000, 560)
        self.window.present()
        settle()
        self.window.state = {'activeTabId': 1, 'activeUrl': 'https://chat.invalid/',
                             'favorites': [], 'pinned': [], 'today': []}
        self.window.address_field.set_sensitive(True)
        self.chrome = self.window.responsive
        self.assertGreaterEqual(self.window.get_width(), 900)
        self.assertFalse(self.chrome.phone)
        self.chrome.render()
        settle()

    def test_hidden_bar_stays_out_of_bottom_page_pointer_motion(self):
        self.chrome.bar_handle.emit('clicked')
        settle()
        self.assertFalse(self.chrome.center.bar.get_mapped())
        self.assertTrue(self.chrome.bar_handle.get_mapped())
        self.assertLessEqual(self.chrome.bar_handle.get_width(), 110)
        self.assertLessEqual(self.chrome.bar_handle.get_height(), 20)
        for x in (0, self.window.page.get_width() // 2, self.window.page.get_width() - 1):
            for inset in (1, 12, 40, 71):
                self.chrome.pointer_moved(x, self.window.page.get_height() - inset)
                self.assertFalse(self.chrome.center.bar.get_visible())
        self.assertIn('Show address bar', self.chrome.bar_handle.get_tooltip_text())

    def test_handle_and_address_command_reveal_the_editor(self):
        self.chrome.hide_bar()
        self.chrome.bar_handle.emit('clicked')
        settle()
        self.assertTrue(self.chrome.center.bar.get_mapped())
        self.assertTrue(self.window.address_focused())
        self.chrome.hide_bar()
        self.window._focus_address()
        settle()
        self.assertTrue(self.window.address_focused())
        self.assertFalse(self.chrome.bar_hidden)

    def test_chat_history_does_not_reveal_but_tab_change_does(self):
        self.chrome.hide_bar()
        self.window.state['activeUrl'] = 'https://chat.invalid/conversation/123'
        self.chrome.render()
        self.assertFalse(self.chrome.center.bar.get_visible())
        self.window.state['activeTabId'] = 2
        self.chrome.render()
        self.assertTrue(self.chrome.center.bar.get_visible())

    def test_narrow_layout_uses_its_live_search_without_the_desktop_handle(self):
        self.window.set_default_size(420, 560)
        settle()
        self.assertLess(self.window.get_width(), 560)
        self.assertTrue(self.chrome.phone)
        self.assertFalse(self.chrome.bar_handle.get_visible())
        self.window._focus_address()
        settle()
        self.assertTrue(self.chrome.center.searching)
        self.assertIsInstance(self.window.get_focus(), Gtk.Editable)
        self.assertEqual(self.window.get_focus().get_text(), 'https://chat.invalid/')

    def test_reload_follows_address_and_dispatches_one_reload(self):
        self.assertIs(self.window.address_field.get_next_sibling(), self.window.reload)
        self.assertIs(self.window.reload.get_next_sibling(), self.chrome.share_anchor)
        sent = []
        self.window.send = lambda channel, payload=None: sent.append(channel)
        self.window.reload.emit('clicked')
        self.assertEqual(sent, ['nav:reload'])


if __name__ == '__main__':
    unittest.main()
