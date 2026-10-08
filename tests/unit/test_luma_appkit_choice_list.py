# SPDX-License-Identifier: Apache-2.0
"""Described radio choices fit phones and keep accessible, keyboard selection."""
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Gtk
from luma_appkit import ChoiceList, Switch, install_appkit


def settle():
    end = time.monotonic() + .3
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class Choices(unittest.TestCase):
    def test_form_insets_and_default_panel_reset(self):
        from luma_appkit import ActionCenter, BarAction, TextField, ToastHost
        install_appkit()
        host = ToastHost(Gtk.Box())
        window = Gtk.Window(child=host, default_width=402, default_height=874)
        center = ActionCenter().attach(host)
        center.show_bar([BarAction('plus', tooltip='Add')], fill=True)
        window.present(); settle()
        form = TextField('Name', size='panel')
        center.grow('form', form, panel_padding=False); settle()
        self.assertTrue(center.panel.has_css_class('unpadded'))
        self.assertEqual(form.compute_bounds(center.panel)[1].origin.x, 0)
        self.assertEqual(form.compute_bounds(center.panel)[1].origin.y, 0)
        self.assertEqual(form.entry.get_height(), 48)
        regular = Gtk.Label(label='A regular menu')
        center.grow('menu', regular); settle()
        self.assertFalse(center.panel.has_css_class('unpadded'))
        bounds = regular.compute_bounds(center.panel)[1]
        self.assertEqual(bounds.origin.x, 4)
        self.assertEqual(bounds.origin.y, 8)
        window.close()

    def test_fit_selection_and_keyboard(self):
        install_appkit()
        for width in (360, 402, 720):
            changed = []
            choices = ChoiceList([('a', 'For Luma', 'Snapshots and compression; only Linux reads it.'),
                ('b', 'For every computer', 'Windows, Mac and Linux can all read and write it.')],
                selected='a', size='panel', on_choose=changed.append)
            toggle = Switch(big=True, label='Protect with a password')
            host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            host.append(choices); host.append(toggle)
            window = Gtk.Window(child=host, default_width=width)
            window.present(); settle()
            self.assertEqual(window.get_width(), width)
            self.assertEqual(choices.get_accessible_role(), Gtk.AccessibleRole.RADIO_GROUP)
            self.assertEqual(choices.buttons['a'].get_accessible_role(), Gtk.AccessibleRole.RADIO)
            self.assertEqual(changed, [])
            self.assertTrue(choices.buttons['b'].activate()); settle()
            self.assertEqual(changed, ['b'])
            self.assertEqual(choices.selected, 'b')
            self.assertFalse(choices.buttons['a'].has_css_class('on'))
            self.assertFalse(choices.buttons['a'].get_focusable())
            self.assertTrue(choices.buttons['b'].get_focusable())
            self.assertTrue(choices._key('b', Gdk.KEY_Down))
            self.assertEqual(choices.selected, 'a')
            self.assertEqual(changed, ['b', 'a'])
            self.assertEqual(toggle.compute_bounds(host)[1].size.width, 44)
            self.assertEqual(toggle.compute_bounds(host)[1].size.height, 26)
            window.close()


if __name__ == '__main__':
    unittest.main()
