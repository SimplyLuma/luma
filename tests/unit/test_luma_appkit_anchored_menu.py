"""Anchored menu bounds, dismissal and default bar placement on real windows."""
import os
import time
import unittest
from unittest.mock import patch
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, Gtk
from luma_appkit import AppWindow, BarAction, CommandRegistry, install_appkit, install_lumaui
from luma_appkit.menus import bar_menu


def settle():
    end = time.monotonic() + .4
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.01)


class AnchoredMenu(unittest.TestCase):
    def test_bounds_and_dismissal_on_phone_and_narrow_desktop(self):
        Gtk.init(); Adw.init(); install_appkit(); install_lumaui()
        app = Adw.Application(application_id='io.luma.AnchoredMenu.Test'); app.register(None)
        for form in ('phone', 'desktop'):
            with patch.dict(os.environ, {'LUMA_FORM_FACTOR': form}):
                window = AppWindow(application=app, title='Menu', app_id='io.luma.AnchoredMenu.Test', icon_name='applications-system-symbolic', commands=CommandRegistry(()), minimum_width=320)
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.END)
                box.set_margin_start(16); box.set_margin_end(16); box.set_margin_bottom(40)
                key = Gtk.Button(label='Modes', halign=Gtk.Align.START)
                box.append(key); window.set_body(box)
                window.set_default_size(402, 874); window.present()
                early = bar_menu(key, [BarAction('', 'Basic'), BarAction('', 'Scientific')], where='anchor', width_px=240)
                settle()
                _, early_bounds = early.compute_bounds(early.handle.host)
                _, key_bounds = key.compute_bounds(early.handle.host)
                self.assertEqual(round(early_bounds.get_y() + early_bounds.get_height()), round(key_bounds.get_y() + key_bounds.get_height()))
                early.close(); settle()
                for width in (360, 402, 720):
                    window.set_default_size(width, 874); settle()
                    frame = bar_menu(key, [BarAction('', 'Basic', active=True), BarAction('', 'Scientific')],
                                     where='anchor', width_px=240)
                    settle()
                    host = frame.handle.host
                    ok, bounds = frame.compute_bounds(host)
                    _, anchor = key.compute_bounds(host)
                    self.assertTrue(ok)
                    self.assertEqual(round(bounds.get_width()), 240)
                    self.assertEqual(round(bounds.get_x()), round(anchor.get_x()))
                    self.assertEqual(round(bounds.get_y() + bounds.get_height()), round(anchor.get_y() + anchor.get_height()))
                    self.assertFalse(frame.handle.scrim.has_css_class('lumaui-scrim'))
                    self.assertTrue(host._modal_key(None, Gdk.KEY_Escape, 0, 0, frame.handle))
                    settle(); self.assertFalse(frame.is_open)
                frame = bar_menu(key, [BarAction('', 'Basic')], where='anchor', width_px=240); settle()
                controllers = frame.handle.scrim.observe_controllers()
                click = next(controllers.get_item(i) for i in range(controllers.get_n_items())
                             if isinstance(controllers.get_item(i), Gtk.GestureClick))
                click.emit('released', 1, 0., 0.); settle()
                self.assertFalse(frame.is_open)
                window.set_default_size(402, 874); settle()
                default = bar_menu(key, [BarAction('', 'Basic')]); settle()
                self.assertTrue(default.handle.scrim.has_css_class('lumaui-scrim'))
                self.assertGreater(default.get_allocated_width(), 240)
                default.close(); window.close(); settle()


if __name__ == '__main__':
    unittest.main()
