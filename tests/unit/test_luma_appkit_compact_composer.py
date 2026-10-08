# SPDX-License-Identifier: Apache-2.0
"""A compact composer keeps36px controls without shrinking ordinary phone bars."""
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gdk, GLib, Gtk
from luma_appkit import ActionEditor, BarTile, BarTiles, ActionCenter, BarAction, BarEntry, ToastHost, install_appkit


def settle():
    end = time.monotonic() + .4
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.005)


class CompactComposer(unittest.TestCase):
    def test_panel_only_preserves_room_above_held_actions(self):
        install_appkit()
        content = Gtk.Box(height_request=1200)
        scroller = Gtk.ScrolledWindow(child=content)
        host = ToastHost(scroller)
        window = Gtk.Window(child=host, default_width=402, default_height=874)
        center = ActionCenter().attach(host)
        center.show_bar([BarAction('plus', tooltip='Add')])
        window.present(); settle()
        ordinary_room = content.get_margin_bottom()
        center.show_panel(Gtk.Box(height_request=132)); settle()
        self.assertEqual(center.bar.get_allocated_width(), 370)
        self.assertEqual(center.bar.get_allocated_height(), 148)
        self.assertIsNotNone(center._row_top(host))
        self.assertGreater(content.get_margin_bottom(), ordinary_room + 60)
        center.show_bar([BarAction('plus', tooltip='Add')]); settle()
        self.assertLessEqual(abs(content.get_margin_bottom() - ordinary_room), 4)
        window.close()

    def test_explicit_tiles_keep_the_empty_fifth_column(self):
        install_appkit()
        tiles = BarTiles([BarTile('reply', 'Reply') for _ in range(4)], columns=5, size='compact')
        window = Gtk.Window(child=tiles, default_width=350, default_height=64)
        window.present(); settle()
        self.assertEqual(len(tiles.buttons), 4)
        expected = (tiles.get_width() - 4 * tiles.get_column_spacing()) / 5
        self.assertLessEqual(abs(tiles.buttons[0].get_allocated_width() - expected), 1)
        window.close()

    def test_return_submission_is_explicit_and_shift_keeps_newlines(self):
        calls = []
        for enabled in (False, True):
            editor = ActionEditor('Message', 'message-square', submit_on_return=enabled,
                                  primary=BarAction('send-horizontal', 'Send', on_activate=lambda: calls.append(1)))
            center = ActionCenter(editor)
            self.assertEqual(center._editor_key(None, Gdk.KEY_Return, 0, Gdk.ModifierType(0)), enabled)
            self.assertFalse(center._editor_key(None, Gdk.KEY_Return, 0, Gdk.ModifierType.SHIFT_MASK))
            self.assertTrue(center._editor_key(None, Gdk.KEY_Return, 0, Gdk.ModifierType.CONTROL_MASK))
            self.assertEqual(editor.hint.get_label(), 'Esc to fold · Return to send' if enabled else 'Esc to fold · Ctrl Return')
        self.assertEqual(len(calls), 3)

    def test_composer_geometry_and_default_reset(self):
        install_appkit()
        host = ToastHost(Gtk.Box())
        window = Gtk.Window(child=host, default_width=402, default_height=874)
        center = ActionCenter().attach(host)
        entry = BarEntry('compose', placeholder='Message', on_submit=lambda: None)
        center.show_bar([BarAction('plus', tooltip='Attach'), entry], phone_control_size='small')
        window.present(); settle()
        attach = center.bar_row.get_first_child()
        self.assertEqual((attach.get_width(), attach.get_height()), (36, 36))
        self.assertEqual(center.bar.get_allocated_height(), 48)
        center.show_bar([BarAction('plus', tooltip='Add')]); settle()
        button = center.bar_row.get_first_child()
        self.assertEqual((button.get_width(), button.get_height()), (48, 48))
        self.assertFalse(center.bar_row.has_css_class('small-controls'))
        window.close()
        with self.assertRaises(ValueError):
            center.set_phone_control_size('unknown')


if __name__ == '__main__':
    unittest.main()
