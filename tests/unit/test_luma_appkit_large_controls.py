"""Hero controls fit their 40px row and expose working activation and toggle state."""
import time
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib
from luma_appkit import TextButton, IconOnlyButton, install_appkit, install_lumaui


class LargeControls(unittest.TestCase):
    def test_large_controls_and_unchanged_defaults(self):
        Gtk.init(); install_appkit(); install_lumaui()
        events = []
        row = Gtk.Box()
        primary = TextButton('Play', icon='play', size='large', style='key', on_click=lambda: events.append('play'))
        secondary = TextButton('Shuffle', icon='shuffle', size='large', style='fill')
        heart = IconOnlyButton('heart', 'Love album', size='large', active=True,
                               on_click=lambda: events.append('love'))
        for widget in (primary, secondary, heart): row.append(widget)
        window = Gtk.Window(child=row)
        window.add_css_class('luma-app-window')
        window.set_default_size(320, 80); window.present()
        end = time.monotonic()+.3
        while time.monotonic()<end:
            while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        for widget in (primary,secondary,heart): self.assertEqual(widget.get_height(),40)
        self.assertEqual(heart.get_width(),40)
        self.assertTrue(secondary.has_css_class('fill'))
        self.assertIn('heart-filled',heart.get_child().get_icon_name())
        heart.set_active(False)
        self.assertFalse(heart.has_css_class('on'))
        self.assertNotIn('filled',heart.get_child().get_icon_name())
        primary.emit('clicked');heart.emit('clicked')
        self.assertEqual(events,['play','love'])
        self.assertEqual(TextButton('Default').measure(Gtk.Orientation.VERTICAL,-1)[0],36)
        self.assertEqual(IconOnlyButton('x','Close').measure(Gtk.Orientation.VERTICAL,-1)[0],36)
        touch = TextButton('Shuffle', icon='shuffle', size='touch')
        row.append(touch)
        end = time.monotonic()+.2
        while time.monotonic()<end:
            while GLib.MainContext.default().pending(): GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        self.assertEqual(touch.get_height(),46)
        self.assertLess(touch.get_width(),120)
        window.close()


if __name__ == '__main__':
    unittest.main()
