# SPDX-License-Identifier: Apache-2.0
"""Live compact collection pickers preserve their v71 phone geometry."""
import sys
import time
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src/luma-platform/appkit'))
import gi
gi.require_version('Gtk','4.0')
from gi.repository import GLib,Gtk
from luma_appkit import BarAction,install_appkit
from luma_appkit.action_center import make_control

class CompactPicker(unittest.TestCase):
    def test_picker_geometry_and_standard_key_unchanged(self):
        install_appkit()
        for width in (360,402,500):
            row=Gtk.Box();row.add_css_class('lumaui-action-center');row.add_css_class('phone')
            library=make_control(BarAction('image','Library',dropdown=True,dropdown_size='compact'))
            days=make_control(BarAction('','Days',dropdown=True,dropdown_size='compact'))
            key=make_control(BarAction('search',tooltip='Search'))
            for item in (library,days,key):row.append(item)
            row.append(Gtk.Box(hexpand=True))
            window=Gtk.Window(default_width=width,default_height=100,child=row);window.present()
            end=time.monotonic()+.3
            while time.monotonic()<end:
                while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
                time.sleep(.005)
            self.assertAlmostEqual(library.compute_bounds(row)[1].size.width,122,delta=2)
            self.assertAlmostEqual(days.compute_bounds(row)[1].size.width,82,delta=2)
            self.assertEqual(key.compute_bounds(row)[1].size.width,48)
            self.assertEqual(library.get_child().get_first_child().get_width(),18)
            self.assertEqual(library.get_child().get_last_child().get_width(),14)
            expected=library.get_style_context().lookup_color('luma_bar_ink_secondary')[1]
            self.assertEqual(library.get_color().to_string(),expected.to_string())
            window.close()

    def test_view_picker_geometry(self):
        install_appkit()
        for width in (360, 402, 500):
            row = Gtk.Box()
            row.add_css_class('lumaui-action-center')
            row.add_css_class('phone')
            picker = make_control(BarAction('sun', 'Today', dropdown=True, dropdown_size='view'))
            row.append(picker)
            row.append(Gtk.Box(hexpand=True))
            window = Gtk.Window(default_width=width, default_height=100, child=row)
            window.present()
            end = time.monotonic() + .3
            while time.monotonic() < end:
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                time.sleep(.005)
            self.assertAlmostEqual(picker.compute_bounds(row)[1].size.width, 122.5, delta=2)
            self.assertEqual(picker.get_child().get_first_child().get_width(), 19)
            self.assertEqual(picker.get_child().get_last_child().get_width(), 14)
            expected = picker.get_style_context().lookup_color('luma_bar_ink_secondary')[1]
            self.assertEqual(picker.get_color().to_string(), expected.to_string())
            window.close()

    def test_corner_badge_border_box_and_default_count(self):
        from luma_appkit import CountBadge
        install_appkit()
        row = Gtk.Box()
        row.add_css_class('lumaui-action-center')
        row.add_css_class('phone')
        actions = [make_control(BarAction('bell', tooltip='Alerts', badge=count)) for count in (1, 12, 99)]
        ordinary = CountBadge(1)
        for item in [*actions, ordinary]:
            row.append(item)
        window = Gtk.Window(default_width=402, default_height=100, child=row)
        window.present()
        end = time.monotonic() + .3
        while time.monotonic() < end:
            while GLib.MainContext.default().pending():
                GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        sizes = [item.bar_badge.compute_bounds(row)[1].size for item in actions]
        self.assertEqual(sizes[0].width, 16)
        self.assertTrue(all(size.height == 16 for size in sizes))
        self.assertTrue(all(16 < size.width <= 24 for size in sizes[1:]))
        self.assertEqual(ordinary.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 20)
        for item in actions:
            self.assertEqual(item.compute_bounds(row)[1].size.width, 48)
        window.close()

    def test_collection_row_spacing_resets_for_other_bars(self):
        from luma_appkit import ActionCenter, ToastHost, SPACER
        install_appkit()
        host=ToastHost(Gtk.Box())
        window=Gtk.Window(default_width=402,default_height=400,child=host)
        center=ActionCenter().attach(host)
        center.show_bar([BarAction('image','Library',dropdown=True,dropdown_size='compact'),
                         BarAction('','Days',dropdown=True,dropdown_size='compact'),SPACER,
                         BarAction('search',tooltip='Search'),BarAction('plus',tooltip='Add')],fill=True)
        window.present()
        end=time.monotonic()+.4
        while time.monotonic()<end:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        self.assertTrue(center.bar_row.has_css_class('compact-pickers'))
        first=center.bar_row.get_first_child();second=first.get_next_sibling()
        a=first.compute_bounds(center.bar_row)[1];b=second.compute_bounds(center.bar_row)[1]
        self.assertAlmostEqual(b.origin.x-a.origin.x-a.size.width,6,delta=.1)
        center.show_bar([BarAction('bold',tooltip='Bold'),BarAction('italic',tooltip='Italic')],fill=True)
        self.assertFalse(center.bar_row.has_css_class('compact-pickers'))
        window.close()

if __name__=='__main__':unittest.main()
