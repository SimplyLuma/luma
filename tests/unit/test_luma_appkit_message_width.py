"""A message cap must follow the actual column and reflow its real text."""
import time
import unittest
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GLib
from luma_appkit import MessageBubble, install_appkit, install_lumaui


def settle():
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(.01)


class MessageWidths(unittest.TestCase):
    def test_relative_cap_reflows_and_resizes_with_column(self):
        Gtk.init(); install_appkit(); install_lumaui()
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.set_margin_start(16); column.set_margin_end(16)
        bubble = MessageBubble('A long reply that wraps over several lines. ' * 12,
                               max_width=680, max_width_ratio=.8)
        column.append(bubble)
        window = Gtk.Window(child=column)
        window.set_default_size(402, 874); window.present(); settle()
        narrow_height = bubble.get_height()
        for width in (360, 500, 720, 1180, 402):
            window.set_default_size(width, 874); settle()
            self.assertLessEqual(bubble.get_allocated_width(), int(column.get_width() * .8))
            if width < 1180:
                self.assertGreaterEqual(bubble.get_allocated_width(), int(column.get_width() * .8) - 1)
            self.assertGreater(bubble.get_height(), 0)
            # A start/end-aligned bubble is allocated narrower than the column.
            # Its height must be measured at that cap, or wrapped lines clip.
            required = bubble.label.measure(Gtk.Orientation.VERTICAL, bubble.label.get_width())[0]
            self.assertGreaterEqual(bubble.label.get_height(), required)
            if width == 720:
                self.assertLess(bubble.get_height(), narrow_height)
        bubble.set_max_width_ratio(.5); settle()
        self.assertLessEqual(bubble.get_allocated_width(), column.get_width() // 2)
        bubble.set_max_width_ratio(None); bubble.set_max_width(200); settle()
        self.assertLessEqual(bubble.get_width(), 230)
        window.close(); settle()

    def test_default_bubble_uses_the_responsive_column(self):
        Gtk.init(); install_appkit(); install_lumaui()
        bubble = MessageBubble('That’s the one. Can we get a crop for the homepage hero?')
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.START)
        column.append(bubble)
        clamp = Adw.Clamp(child=column, maximum_size=300, tightening_threshold=300,
                          halign=Gtk.Align.START)
        row = Gtk.Box(); row.append(clamp)
        window = Gtk.Window(child=row, default_width=402, default_height=200)
        window.present(); settle()
        self.assertEqual(bubble.get_allocated_width(), 300)
        self.assertEqual(bubble.label.get_width(), bubble.get_width())
        window.set_default_size(720, 200); settle()
        self.assertEqual(bubble.label.get_width(), bubble.get_width())
        window.close(); settle()

    def test_invalid_relative_caps_are_rejected(self):
        for ratio in (0, -1, 1.1, True, float('nan')):
            with self.assertRaises(ValueError):
                MessageBubble('Hello', max_width_ratio=ratio)


if __name__ == '__main__':
    unittest.main()
