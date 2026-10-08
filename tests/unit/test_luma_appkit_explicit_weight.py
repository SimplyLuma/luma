"""Explicit typography weights survive role and treatment provider ordering."""
import time
import unittest
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk,GLib
from luma_appkit import apply_type,install_appkit,install_lumaui
class ExplicitWeight(unittest.TestCase):
    def test_mapped_role_defaults_and_explicit_weights(self):
        Gtk.init();install_appkit();install_lumaui()
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        window=Gtk.Window(child=box);window.add_css_class('luma-app-window')
        cases=[('page-title',750),('list-title',700),('lead',400),('body',650)]
        labels=[]
        for role,weight in cases:
            label=apply_type(Gtk.Label(label='Mapped weight'),role,weight=weight)
            labels.append(label);box.append(label)
        window.present();end=time.monotonic()+.3
        while time.monotonic()<end:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        for label,(_,weight) in zip(labels,cases):
            self.assertEqual(int(label.get_pango_context().get_font_description().get_weight()),weight)
        apply_type(labels[0],'page-title')
        end=time.monotonic()+.2
        while time.monotonic()<end:
            while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
            time.sleep(.005)
        self.assertEqual(int(labels[0].get_pango_context().get_font_description().get_weight()),700)
        window.close()
if __name__=='__main__':unittest.main()
