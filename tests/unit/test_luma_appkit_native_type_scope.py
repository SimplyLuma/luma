"""Dim labels inherit typography; native table cells own their horizontal inset."""
import time
import unittest
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Pango
from luma_appkit import install_appkit, install_lumaui, apply_type


def walk(widget):
    yield widget
    child=widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child=child.get_next_sibling()


class NativeTypeScope(unittest.TestCase):
    def test_dim_caption_and_table_scopes(self):
        Gtk.init(); install_appkit(); install_lumaui()
        body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        dim=Gtk.Label(label='Modified yesterday');dim.add_css_class('dim-label')
        apply_type(dim, 'reading');body.append(dim)
        caption=Gtk.Label(label='Caption');caption.add_css_class('caption');body.append(caption)
        model=Gtk.StringList.new(['File name'])
        table=Gtk.ColumnView(model=Gtk.NoSelection(model=model));table.add_css_class('lumaui-table')
        factory=Gtk.SignalListItemFactory()
        factory.connect('setup',lambda _,item:item.set_child(Gtk.Label(label='File name')))
        table.append_column(Gtk.ColumnViewColumn(title='Name',factory=factory));body.append(table)
        ordinary=Gtk.ListBox();ordinary.append(Gtk.Label(label='Ordinary list'));body.append(ordinary)
        window=Gtk.Window(child=body,default_width=400,default_height=250);window.add_css_class('luma-app-window');window.present()
        until=time.monotonic()+.35
        while time.monotonic()<until:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        self.assertEqual(dim.get_pango_context().get_font_description().get_size()/Pango.SCALE,15)
        self.assertEqual(caption.get_pango_context().get_font_description().get_size()/Pango.SCALE,11)
        row=next(w for w in walk(table) if w.get_css_name()=='row')
        self.assertEqual(row.get_style_context().get_padding().left,0)
        self.assertEqual(ordinary.get_row_at_index(0).get_style_context().get_padding().left,8)
        window.destroy()

if __name__=='__main__':unittest.main()
