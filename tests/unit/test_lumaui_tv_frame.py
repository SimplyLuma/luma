#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The floating island for every luma_appkit window (tv_frame + LumaUI.window_set_tv_frame).

A fixture sidebar app built only from luma_appkit's desktop parts (IslandSplitView,
NavigationSidebar, NavigationRow, Island, libadwaita rows), with no TV code: in TV posture it gets
the island, and leaving TV posture puts every widget back where it was.
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/luma-platform/appkit"))
os.environ.setdefault("GSETTINGS_BACKEND", "memory")

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("LumaUI", "1")
    from gi.repository import Adw, GLib, Gtk, LumaUI
    import luma_appkit
    from luma_appkit import AppWindow, Island, IslandSplitView, NavigationRow, NavigationSidebar, tv_frame
    READY = Gtk.init_check() and tv_frame.supported()
except (ImportError, ValueError, AttributeError):
    READY = False


def spin(ms: int) -> None:
    end = time.monotonic() + ms / 1000
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        context.iteration(False)
        time.sleep(0.005)


def page(title: str) -> Gtk.Widget:
    preferences = Adw.PreferencesPage()
    group = Adw.PreferencesGroup(title=title)
    group.add(Adw.SwitchRow(title="Keep the list in step"))
    group.add(Adw.ActionRow(title="Last change", subtitle="Today"))
    preferences.add(group)
    return preferences


def pantry(app):
    """A desktop sidebar app, as any luma_appkit app is written (Connect's own shape)."""
    window = AppWindow(application=app, app_id="org.projectluma.tests.PyPantry", title="Pantry", icon_name="org.projectluma.Connect",
                       commands=luma_appkit.CommandRegistry(()), default_width=1280, default_height=720)
    sidebar = NavigationSidebar()
    for title, icon in (("Recipes", "book-open"), ("Shopping", "shopping-cart")):
        sidebar.append_row(NavigationRow(title, icon_name=icon, subtitle="12 items"))
    sidebar.append_section("Kitchen")
    sidebar.append_row(NavigationRow("Timers", icon_name="timer", trailing="3"))
    island = Island()
    island.append(page("Recipes"))
    split = IslandSplitView()
    split.set_sidebar(Adw.NavigationPage.new(sidebar, "Pantry"))
    split.set_content(Adw.NavigationPage.new(island, "Recipes"))
    window.set_body(split)
    sidebar.list.select_row(sidebar.list.get_row_at_index(0))
    return window, split, sidebar, island


@unittest.skipUnless(READY, "needs a display, GTK 4, libadwaita and LumaUI at API level 9")
class TvFrame(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.projectluma.tests.PyPantry")
        cls.app.register(None)

    def test_desktop_window_is_untouched(self):
        window, split, sidebar, island = pantry(self.app)
        window.tv_posture = False
        window.present()
        spin(300)
        self.assertFalse(LumaUI.window_get_tv_active(window))
        self.assertIsInstance(sidebar.get_parent(), Adw.NavigationPage)
        self.assertFalse(sidebar.has_css_class("lumaui-tv-island"))
        window.destroy()

    def test_sidebar_app_gets_the_island_and_gives_it_back(self):
        window, split, sidebar, island = pantry(self.app)
        holder = split.get_parent()
        window.present()
        spin(200)
        window.tv_posture = True
        spin(500)
        self.assertTrue(LumaUI.window_get_tv_active(window))
        frame = sidebar.get_parent()
        self.assertTrue(frame.has_css_class("lumaui-tv-frame"))
        self.assertIs(island.get_parent(), frame)
        self.assertIsNone(split.get_parent())
        self.assertTrue(sidebar.has_css_class("lumaui-tv-island"))
        row = sidebar.list.get_row_at_index(0)
        self.assertFalse(row._labels.get_last_child().get_visible())          # no subtitle in the island
        self.assertFalse(sidebar.list.get_row_at_index(2).get_child().get_first_child().get_visible())  # hairline
        self.assertTrue(any(w.has_css_class("lumaui-part-row") for w in tv_frame._walk(island)))
        # The island is as tall as its rows.
        ok, bounds = sidebar.compute_bounds(window)
        self.assertTrue(ok)
        self.assertLess(bounds.get_height(), window.get_height() / 2)

        window.tv_posture = False
        spin(400)
        self.assertFalse(LumaUI.window_get_tv_active(window))
        self.assertIs(split.get_parent(), holder)
        self.assertIsInstance(sidebar.get_parent(), Adw.NavigationPage)
        self.assertIsInstance(island.get_parent(), Adw.NavigationPage)
        self.assertFalse(sidebar.has_css_class("lumaui-tv-island"))
        self.assertTrue(row._labels.get_last_child().get_visible())
        self.assertFalse(any(w.has_css_class("lumaui-part-row") for w in tv_frame._walk(island)))
        window.destroy()

    def test_a_redrawn_shell_is_found_again(self):
        window, split, sidebar, island = pantry(self.app)
        window.present()
        window.tv_posture = True
        spin(400)
        # The app shows a new shell (Connect rebuilds on sign-in): the new one gets the island.
        other, split2, sidebar2, _island2 = pantry(self.app)
        other.set_body(Gtk.Box())  # free the second shell from its own window
        window.set_body(split2)
        spin(500)
        self.assertTrue(LumaUI.window_get_tv_active(window))
        self.assertTrue(sidebar2.get_parent().has_css_class("lumaui-tv-frame"))
        window.destroy()

    def test_one_island_is_centred(self):
        window = AppWindow(application=self.app, app_id="org.projectluma.tests.PyPantry", title="Pantry", icon_name="org.projectluma.Connect",
                           commands=luma_appkit.CommandRegistry(()), default_width=1280, default_height=720)
        island = Island()
        island.append(Gtk.Button(label="Sign In…"))
        window.set_body(island)
        window.present()
        window.tv_posture = True
        spin(400)
        self.assertTrue(LumaUI.window_get_tv_active(window))
        self.assertTrue(island.get_parent().has_css_class("lumaui-tv-centred"))
        window.tv_posture = False
        spin(300)
        self.assertFalse(LumaUI.window_get_tv_active(window))
        self.assertFalse(island.get_parent().has_css_class("lumaui-tv-frame"))
        window.destroy()

    def test_code_field_on_a_tv(self):
        from luma_appkit import TextField, set_code_field
        window = AppWindow(application=self.app, app_id="org.projectluma.tests.PyPantry", title="Pantry",
                           icon_name="org.projectluma.Connect", commands=luma_appkit.CommandRegistry(()),
                           default_width=1280, default_height=720)
        island = Island()
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        field, go = TextField("Code"), Gtk.Button(label="Sign In")
        form.append(field)
        form.append(go)
        island.append(form)
        set_code_field(field, length=8, group=4, next=go)
        window.set_body(island)
        window.present()
        spin(200)
        field.entry.set_text("ab-12 c")
        self.assertEqual(field.entry.get_text(), "ab-12 c")     # a desktop field takes what is typed
        from luma_appkit import set_busy
        set_busy(go, True, "Signing In…")
        self.assertEqual(go.get_label(), "Sign In")              # off a TV nothing changes
        set_busy(go, False)
        window.tv_posture = True
        spin(300)
        boxes = field.get_next_sibling()
        self.assertTrue(boxes.has_css_class("lumaui-code") and boxes.get_visible())
        self.assertEqual(field.entry.get_text(), "AB12C")       # A-Z and 0-9, uppercased
        field.entry.set_text("k7q2xm4pzz")
        self.assertEqual(field.entry.get_text(), "K7Q2XM4P")    # at most eight
        self.assertEqual([b.get_label() for b in boxes.boxes], list("K7Q2XM4P"))
        from luma_appkit import set_busy
        set_busy(go, True, "Signing In…")
        self.assertEqual(go.get_label(), "Signing In…")          # on a TV the button says it
        set_busy(go, False)
        self.assertEqual(go.get_label(), "Sign In")
        window.tv_posture = False
        spin(300)
        self.assertFalse(boxes.get_visible())
        self.assertFalse(field.has_css_class("lumaui-code-source"))
        window.destroy()


if __name__ == "__main__":
    unittest.main()
