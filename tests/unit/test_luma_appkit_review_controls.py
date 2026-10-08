"""User-review regressions: navigation spacing and neutral raised controls."""
import unittest
from test_luma_appkit_island_resize import settle
from gi.repository import Gtk
from luma_appkit import ActionCenter, BarAction, SPACER, TextButton, IconOnlyButton, ToastHost, install_appkit, install_lumaui

class ReviewControls(unittest.TestCase):
    def setUp(self):
        Gtk.init(); install_appkit(); install_lumaui()

    def test_spacer_keeps_back_group_at_left_when_panel_grows(self):
        for width in (360, 402, 500):
            host = ToastHost(Gtk.Box())
            center = ActionCenter().attach(host)
            window = Gtk.Window(child=host, default_width=width, default_height=740)
            center.show_bar([BarAction('chevron-left', 'Thursday', keep_label=True), SPACER,
                             BarAction('x', tooltip='Close')])
            window.present(); settle()
            button = center.bar_row.get_first_child()
            before = button.get_width()
            center.grow('details', Gtk.Label(label='Event details')); settle()
            self.assertFalse(button.get_hexpand())
            self.assertEqual(button.get_width(), before)
            self.assertLess(button.get_width(), 150)
            ok, bounds = button.compute_bounds(window)
            self.assertTrue(ok); self.assertLess(bounds.get_x(), 40)
            center.fold(); settle(); window.close()

    def test_raised_controls_keep_standard_geometry_and_callbacks(self):
        calls=[]
        word=TextButton('Today',style='raised',on_click=lambda:calls.append('today'))
        icon=IconOnlyButton('chevron-right','Next year',raised=True,on_click=lambda:calls.append('next'))
        box=Gtk.Box(spacing=8,valign=Gtk.Align.START); box.append(word); box.append(icon)
        window=Gtk.Window(child=box); window.present(); settle()
        self.assertEqual(word.get_height(),36)
        self.assertEqual((icon.get_width(),icon.get_height()),(36,36))
        self.assertTrue(word.has_css_class('raised'));self.assertTrue(icon.has_css_class('raised'))
        word.emit('clicked');icon.emit('clicked');self.assertEqual(calls,['today','next'])
        icon.set_raised(False);self.assertFalse(icon.has_css_class('raised'))
        window.close()

if __name__=='__main__': unittest.main()
