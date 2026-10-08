"""A panel may replace its trigger with Close and restore it on every exit."""
import time
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
from luma_appkit import ActionCenter, BarAction, BarSearch, PanelKey, ToastHost, install_appkit, install_lumaui


def settle():
    until = time.monotonic() + .25
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class PanelClose(unittest.TestCase):
    def test_panel_key_has_52px_target_without_theme_padding(self):
        Gtk.init(); install_appkit(); install_lumaui()
        key = PanelKey('Start recording', record=True)
        window = Gtk.Window(child=key)
        window.add_css_class('luma-app-window'); window.present(); settle()
        self.assertEqual(key.get_height(), 52)
        window.close()

    def test_regular_and_equal_action_panels_keep_their_gutters(self):
        Gtk.init(); install_appkit(); install_lumaui()
        for items, gutter in (([BarAction('bold', tooltip='Bold')], 12),
                              ([BarAction('', 'Cancel', fill=True), BarAction('', 'Save', fill=True, primary=True)], 16)):
            host = ToastHost(Gtk.Box()); center = ActionCenter().attach(host)
            window = Gtk.Window(child=host, default_width=402, default_height=874)
            window.add_css_class('luma-app-window')
            center.show_bar(items); window.present(); settle()
            center.grow('form', Gtk.Label(label='Content')); settle()
            ok, bounds = center.bar.compute_bounds(host)
            self.assertTrue(ok)
            self.assertEqual(round(bounds.get_x()), gutter)
            self.assertEqual(round(bounds.get_width()), 402 - gutter * 2)
            window.close()

    def test_close_restores_trigger_and_keeps_search_room(self):
        Gtk.init(); install_appkit(); install_lumaui()
        host = ToastHost(Gtk.Box())
        center = ActionCenter().attach(host)
        window = Gtk.Window(child=host, default_width=402, default_height=874)
        window.add_css_class('luma-app-window')
        search = BarSearch('Search what was said', keep=True)
        center.show_bar([search, BarAction('mic', 'New', primary=True, keep_label=True,
                         key='new', panel=lambda: Gtk.Label(label='New memo'), panel_close=True)], fill=True)
        button = center.bar_row.get_last_child()
        original = button.get_child()
        window.present(); settle()
        for width in (402, 360, 500, 720):
            window.set_default_size(width, 874); settle()
            button.emit('clicked'); settle()
            self.assertEqual(center.grown, 'new')
            self.assertEqual(button.get_child().get_icon_name(), 'lumaui-x-symbolic')
            self.assertFalse(button.has_css_class('primary'))
            self.assertEqual(button.get_width(), 48 if width < 560 else 36)
            self.assertIs(center._panel_anchor, button)
            if width < 560:
                ok, bounds = center.bar.compute_bounds(host)
                self.assertTrue(ok)
                self.assertEqual(round(bounds.get_width()), width - 32)
            button.emit('clicked'); settle()
            self.assertIs(button.get_child(), original)
            self.assertTrue(button.has_css_class('primary'))
            center.grow('new', Gtk.Label(label='New memo')); settle()
            self.assertIs(center._panel_anchor, button)
            self.assertEqual(button.get_width(), 48 if width < 560 else 36)
            center.fold_panel(); settle()
            self.assertIs(button.get_child(), original)
        window.close()

if __name__ == '__main__': unittest.main()
