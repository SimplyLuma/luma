# SPDX-License-Identifier: Apache-2.0
"""Geometry regressions in real app roots, where legacy CSS also applies."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/luma-platform/appkit'))
try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk, GLib, Gtk
    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
except (ImportError, ValueError):
    HAVE_DISPLAY = False


@unittest.skipUnless(HAVE_DISPLAY, 'needs GTK 4 and a display')
class AppGeometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from luma_appkit.widgets import install_appkit
        install_appkit()

    def settle(self):
        loop = GLib.MainLoop()
        GLib.timeout_add(400, loop.quit)
        loop.run()

    def test_places_tiles_and_inline_eta_fit_a_phone(self):
        from luma_appkit import ActionCenter, BarAction, BarReadout, BarTile, BarTiles, ToastHost
        for width in (360, 402, 500):
            tiles = BarTiles([BarTile('building', 'Studio', sub='9 min', well=True) for _ in range(4)])
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            page.append(tiles)
            host = ToastHost(page)
            window = Gtk.Window(default_width=width, default_height=874)
            window.add_css_class('luma-app-window')
            window.set_child(host)
            center = ActionCenter().attach(host)
            eta = BarReadout('9 min', '2.4 mi · arrive 10:20 PM', note='Light traffic')
            center.show_bar([eta, BarAction('navigation', tooltip='Go', primary=True)], fill=True)
            try:
                window.present()
                self.settle()
                for button in tiles.buttons:
                    stack = button.get_child()
                    glyph = stack.get_first_child()
                    self.assertEqual((glyph.get_width(), glyph.get_height()), (44, 44))
                    self.assertEqual(stack.get_last_child().get_label(), '9 min')
                    _, bounds = button.compute_bounds(window)
                    self.assertLessEqual(bounds.get_x() + bounds.get_width(), width)
                self.assertEqual(eta.widget.get_first_child().get_last_child().get_label(), 'Light traffic')
                self.assertFalse(center._overflow)
                eta.set('8 min', '2.1 mi · arrive 10:19 PM')
                self.assertEqual(eta._labels[0].get_label(), '8 min')
            finally:
                window.destroy()

    def test_expanded_labelled_action_keeps_icon_and_word_centered(self):
        from luma_appkit import BarAction
        from luma_appkit.action_center import make_control
        button = make_control(BarAction('plus', 'New session', primary=True, keep_label=True))
        window = Gtk.Window(default_width=402, default_height=100)
        window.add_css_class('luma-app-window')
        window.set_child(button)
        try:
            window.present()
            self.settle()
            _, key = button.compute_bounds(window)
            _, content = button.get_child().compute_bounds(window)
            self.assertAlmostEqual(key.get_x() + key.get_width() / 2,
                                   content.get_x() + content.get_width() / 2, delta=1)
        finally:
            window.destroy()

    def test_panel_text_field_geometry_and_editing_survive_size_changes(self):
        from luma_appkit import TextField
        calls = []
        field = TextField('Name', value='Archive', size='panel', on_changed=calls.append)
        window = Gtk.Window(default_width=402, default_height=200)
        window.add_css_class('luma-app-window')
        field.set_valign(Gtk.Align.START)
        window.set_child(field)
        try:
            window.present()
            self.settle()
            self.assertEqual(field.entry.get_height(), 48)
            field.text = 'Pictures'
            self.assertEqual(calls[-1], 'Pictures')
            field.set_size('regular')
            self.settle()
            self.assertEqual(field.entry.get_height(), 38)
            self.assertEqual(field.text, 'Pictures')
            with self.assertRaises(ValueError):
                field.set_size('oversized')
        finally:
            window.destroy()

    def test_back_lead_and_flexible_search_in_app_window(self):
        from luma_appkit import ActionCenter, BarAction, BarSearch, TitleIsland, ToastHost
        for width in (360, 402, 500, 720, 1180):
            with self.subTest(width=width):
                window = Gtk.Window(default_width=width, default_height=874)
                window.add_css_class('luma-app-window')
                page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                host = ToastHost(page)
                window.set_child(host)
                island = TitleIsland('Prompts', lead='back', phone_only=False)
                page.append(island)
                center = ActionCenter().attach(host)
                center.show_bar([BarSearch(keep=True), BarAction('list-filter', tooltip='Lists'),
                                 BarAction('user-plus', tooltip='Add')], fill=True)
                try:
                    window.present()
                    self.settle()
                    self.assertEqual(island.lead_button.get_width(), 48)
                    self.assertEqual(island.lead_button.get_height(), 48 if width <= 540 else 44)
                    self.assertFalse(center._overflow, 'a flexible search must shrink before hiding actions')
                    child = center.bar_row.get_first_child()
                    while child:
                        self.assertTrue(child.get_visible())
                        ok, bounds = child.compute_bounds(center.bar)
                        self.assertTrue(ok)
                        self.assertGreaterEqual(bounds.get_x(), 0)
                        self.assertLessEqual(bounds.get_x() + bounds.get_width(), center.bar.get_width())
                        child = child.get_next_sibling()
                finally:
                    window.destroy()
                    self.settle()

    def test_icon_primary_is_square_and_four_tiles_fit_a_pane(self):
        from luma_appkit import ActionCenter, BarAction, StackedButton, StackedButtons, ToastHost
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tiles = StackedButtons([StackedButton('calendar', label) for label in
                                ('Location', 'Reminders', 'Calendar', 'Share')], size='tile')
        page.append(tiles)
        host = ToastHost(page)
        window = Gtk.Window(default_width=288, default_height=700)
        window.add_css_class('luma-app-window')
        window.set_child(host)
        center = ActionCenter().attach(host)
        center.show_bar([BarAction('plus', tooltip='Add', primary=True)])
        try:
            window.present()
            self.settle()
            self.assertEqual(window.get_width(), 288, 'tile text must not expand a narrow pane')
            self.assertLessEqual(tiles.measure(Gtk.Orientation.HORIZONTAL, -1)[0], 288)
            window.set_default_size(1024, 700)
            self.settle()
            key = center.bar_row.get_first_child()
            self.assertEqual((key.get_width(), key.get_height()), (36, 36))
        finally:
            window.destroy()
            self.settle()

    def test_list_first_phone_reserves_status_area(self):
        from luma_appkit import ListFirst
        page = ListFirst(Gtk.Box(), Gtk.Box(), title='Contacts')
        window = Gtk.Window(default_width=402, default_height=874)
        window.add_css_class('luma-app-window')
        window.add_css_class('lumaui-phone-device')
        window.add_css_class('lumaui-bleed')
        window.set_decorated(False)
        window.set_child(page)
        try:
            window.present()
            self.settle()
            ok, bounds = page.title_label.compute_bounds(window)
            self.assertTrue(ok)
            self.assertEqual(bounds.get_y(), 60, '44 status + 16 title margin')
        finally:
            window.destroy()
            self.settle()

    def test_sidebar_foot_uses_one_horizontal_inset(self):
        from luma_appkit import NavigationSidebar, SidebarFoot
        sidebar = NavigationSidebar()
        foot = SidebarFoot(search='Search people')
        sidebar.append_footer(foot)
        window = Gtk.Window(default_width=272, default_height=700)
        window.add_css_class('luma-app-window')
        window.set_child(sidebar)
        try:
            window.present()
            self.settle()
            ok, bounds = foot.compute_bounds(window)
            self.assertTrue(ok)
            ok, outer = sidebar.compute_bounds(window)
            self.assertTrue(ok)
            self.assertEqual(bounds.get_x() - outer.get_x(), 10)
        finally:
            window.destroy()
            self.settle()

    def test_phone_form_actions_share_the_frame_equally(self):
        from luma_appkit import ActionCenter, BarAction, ToastHost
        host = ToastHost(Gtk.Box())
        window = Gtk.Window(default_width=402, default_height=874)
        window.add_css_class('luma-app-window')
        window.set_decorated(False)
        window.set_child(host)
        center = ActionCenter().attach(host)
        center.show_bar([BarAction('', 'Cancel', fill=True, filled=True),
                         BarAction('', 'Save', fill=True, primary=True)])
        center.grow('form', Gtk.Label(label='Edit'))
        try:
            window.present()
            self.settle()
            self.assertEqual(center.bar.compute_bounds(window)[1].get_width(), 370)
            cancel = center.bar_row.get_first_child()
            save = center.bar_row.get_last_child()
            self.assertEqual((cancel.compute_bounds(window)[1].get_width(),
                              save.compute_bounds(window)[1].get_width()), (176, 176))
        finally:
            window.destroy()
            self.settle()

    def test_sidebar_phone_title_aligns_to_screen_safe_area(self):
        from luma_appkit import NavigationSidebar
        sidebar = NavigationSidebar()
        sidebar.set_phone_title('Memos')
        window = Gtk.Window(default_width=402, default_height=874)
        for css_class in ('luma-app-window', 'lumaui-phone-device', 'lumaui-bleed'):
            window.add_css_class(css_class)
        window.set_decorated(False)
        window.set_child(sidebar)
        try:
            window.present()
            self.settle()
            bounds = sidebar.phone_title_label.compute_bounds(window)[1]
            self.assertEqual((bounds.get_x(), bounds.get_y()), (16, 60))
            self.assertLessEqual(bounds.get_height(), 36)
        finally:
            window.destroy()
            self.settle()

    def test_phone_rich_menu_and_lit_card_keep_their_own_metrics_and_ink(self):
        from luma_appkit import ContentLitCard, RichMenuItem, TypeLabel
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        menu = RichMenuItem('Settings', icon='settings').menu_widget(lambda: None)
        column.append(menu)
        column.add_css_class('lumaui-media')
        caption = TypeLabel('Wind', role='caption')
        column.append(ContentLitCard(caption))
        window = Gtk.Window(default_width=402, default_height=874)
        window.add_css_class('luma-app-window')
        window.add_css_class('lumaui-phone-device')
        window.set_child(column)
        try:
            window.present()
            self.settle()
            self.assertEqual(menu.compute_bounds(window)[1].get_height(), 48)
            color = caption.label.get_style_context().get_color()
            self.assertAlmostEqual(color.red, 1.0, places=2)
            self.assertAlmostEqual(color.green, 1.0, places=2)
            self.assertAlmostEqual(color.blue, 1.0, places=2)
        finally:
            window.destroy()
            self.settle()

    def test_cover_tile_can_fit_a_compact_now_playing_sleeve(self):
        from luma_appkit import CoverArt
        cover = CoverArt('Blue Hour', size='tile')
        window = Gtk.Window(default_width=44, default_height=44)
        window.set_decorated(False)
        window.set_child(cover)
        try:
            window.present()
            self.settle()
            self.assertEqual((cover.get_width(), cover.get_height()), (44, 44))
        finally:
            window.destroy()
            self.settle()

    def test_island_rim_is_only_drawn_on_desktop(self):
        from luma_appkit import Island
        for phone in (False, True):
            with self.subTest(phone=phone):
                island = Island()
                window = Gtk.Window(default_width=402, default_height=874)
                window.add_css_class('luma-app-window')
                if phone:
                    window.add_css_class('lumaui-phone-device')
                window.set_decorated(False)
                window.set_child(island)
                try:
                    window.present()
                    self.settle()
                    snapshot = Gtk.Snapshot.new()
                    island.do_snapshot(snapshot)
                    node = snapshot.to_node()
                    serialized = bytes(node.serialize().get_data()).decode() if node else ''
                    if phone:
                        self.assertNotIn('inset-shadow', serialized)
                    else:
                        self.assertIn('inset-shadow', serialized)
                finally:
                    window.destroy()
                    self.settle()
