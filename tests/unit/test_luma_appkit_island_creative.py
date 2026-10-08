"""Creative Back/title stays one control and restores the standard divided lead."""
import unittest
from test_luma_appkit_island_resize import settle
from gi.repository import Gtk
from luma_appkit import CornerPill, TitleIsland, LayerHost, install_appkit, install_lumaui

class CreativeIsland(unittest.TestCase):
    def test_primary_icon_retains_name_and_edit_done(self):
        pill=CornerPill(actions=[("play","Present",lambda:None)], primary="Present", primary_icon_only=True)
        key=pill.controls["actions.0"]
        self.assertIsInstance(key.get_child(),Gtk.Image)
        self.assertTrue(key.has_css_class("primary"))
        self.assertEqual(key.get_tooltip_text(),"Present")
        self.assertIsInstance(pill.done_button.get_child(),Gtk.Box)

    def test_all_widths_and_variant_transitions(self):
        Gtk.init(); install_appkit(); install_lumaui()
        for width in (360, 402, 720, 1180):
            host=LayerHost(Gtk.Box(), name="window")
            window=Gtk.Window(child=host, default_width=width, default_height=740)
            window.add_css_class("luma-app-window")
            island=TitleIsland("Launch walkthrough", lead="back", variant="creative", phone_only=False)
            island.float_over(host); window.present(); settle()
            self.assertEqual(island.get_height(),44)
            self.assertEqual(island.lead_button.get_width(),28)
            self.assertFalse(island.lead_button.get_focusable())
            leads=[]; island.connect("lead",lambda *_:leads.append(True))
            island.title_button.emit("clicked"); self.assertEqual(len(leads),1)
            island.set_grow(Gtk.Label(label="Details"), "details")
            island.title_button.emit("clicked"); settle(); self.assertTrue(island.grown)
            island.title_button.emit("clicked"); settle(); self.assertFalse(island.grown)
            island.set_variant("standard"); settle()
            self.assertEqual(island.lead_button.get_width(),48)
            self.assertEqual(island.get_height(),48 if width < 560 else 44)
            self.assertTrue(island.lead_button.get_focusable())
            window.close()
        with self.assertRaises(ValueError): TitleIsland(variant="unknown")

if __name__ == "__main__": unittest.main()
