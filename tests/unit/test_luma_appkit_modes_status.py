# SPDX-License-Identifier: Apache-2.0
"""ModeSwitch.set_status (kit-requests clock-01): a place's running dot and time left, and Clock's
icon tabs in the bar (clock-02 item 3). Builds real widgets on stock GTK 4; skipped without a display."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPKIT = ROOT / "src/luma-platform/appkit"
sys.path.insert(0, str(APPKIT))

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gtk

    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
except (ImportError, ValueError):
    HAVE_DISPLAY = False

PLACES = [("world", "World", "globe"), ("alarms", "Alarms", "alarm-clock", 2),
          ("stopwatch", "Stopwatch", "timer"), ("timer", "Timer", "hourglass")]


class _Base(unittest.TestCase):
    def setUp(self):
        from luma_appkit import action_center, action_toast
        from luma_appkit.structure_placement import ModeSwitch
        self.ac, self.toast, self.ModeSwitch = action_center, action_toast, ModeSwitch
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.destroy()

    def _center(self, width):
        host = self.toast.ToastHost(Gtk.Box())
        window = Gtk.Window()
        window.set_child(host)
        self.windows.append(window)
        host.get_width = lambda: width
        return self.ac.ActionCenter().attach(host)


@unittest.skipUnless(HAVE_DISPLAY, "needs GTK 4 and a display")
class ModeStatus(_Base):

    def test_running_dot_and_time_left_on_a_computer(self):
        places = self.ModeSwitch(PLACES, current="world")
        timer, stopwatch = places.buttons["timer"], places.buttons["stopwatch"]
        self.assertFalse(timer.status_dot.get_visible() or timer.status_text.get_visible())
        places.set_status("timer", "4:12")
        self.assertEqual(timer.status_text.get_label(), "4:12")
        self.assertTrue(timer.status_text.get_visible() and not timer.status_dot.get_visible())
        places.set_status("stopwatch", "running")
        self.assertTrue(stopwatch.status_dot.get_visible() and not stopwatch.status_text.get_visible())
        places.set_status("timer", "running")
        self.assertTrue(timer.status_dot.get_visible() and not timer.status_text.get_visible(), "the dot replaces the time")
        places.set_status("timer", None)
        self.assertFalse(timer.status_dot.get_visible() or timer.status_text.get_visible())
        with self.assertRaises(ValueError):
            places.set_status("nope", "running")
        css = (APPKIT / "luma-appkit-base.css").read_text()
        self.assertIn("label.lumaui-mode-status", css)
        self.assertIn("box.lumaui-mode-dot", css)

    def test_time_left_gives_way_to_a_folded_label(self):
        places = self.ModeSwitch(PLACES, current="world")
        places.set_status("timer", "4:12")
        places._narrow = True            # a narrow window folds the other places' words
        places._labels()
        self.assertFalse(places.buttons["timer"].status_text.get_visible())
        places.set_current("timer")
        places._labels()
        self.assertTrue(places.buttons["timer"].status_text.get_visible(), "the chosen place keeps its words")
        places.add_css_class("in-bar")   # an icon-only tab in a phone bar has no room for it
        places._labels()
        self.assertFalse(places.buttons["timer"].status_text.get_visible())

    def test_running_dot_is_a_corner_dot_on_a_phone_tab(self):
        center = self._center(375)
        places = self.ModeSwitch(PLACES, current="world")
        center.show_bar([], modes=places, tabs="compact")
        places._narrow = True
        places._labels()
        stopwatch, alarms = places.buttons["stopwatch"], places.buttons["alarms"]
        places.set_status("stopwatch", "running")
        dot = stopwatch.status_dot
        self.assertTrue(dot.get_visible() and dot.has_css_class("corner"))
        self.assertIsInstance(dot.get_parent(), Gtk.Overlay, "over the tab's glyph, top right")
        # a tab with a count and a dot keeps both in its corner
        places.set_status("alarms", "running")
        self.assertTrue(alarms.count_badge.has_css_class("corner") and alarms.status_dot.has_css_class("corner"))
        # back to a wide bar: both inline again, no overlay left
        places._narrow = False
        places._labels()
        for button in (stopwatch, alarms):
            self.assertNotIsInstance(button.get_child(), Gtk.Overlay)
            self.assertFalse(button.status_dot.has_css_class("corner"))
        self.assertIs(stopwatch.status_dot.get_parent(), stopwatch.get_child())
        css = (APPKIT / "luma-appkit-bar.css").read_text()
        self.assertIn("box.lumaui-mode-dot.corner", css)


@unittest.skipUnless(HAVE_DISPLAY, "needs GTK 4 and a display")
class ClockBar(_Base):
    """clock-02 item 3: TabBar(compact=True) inside the bar."""

    def test_the_key_beside_icon_tabs_is_56_and_the_count_is_10_5_400_ink(self):
        from luma_appkit.structure_tabs import TabBar
        ac = self.ac
        tabs = TabBar([(k, w, i) for k, w, i, *_ in PLACES], compact=True)
        tabs.set_count("alarms", 2)
        center = self._center(402)
        center.show_bar([ac.SEPARATOR, ac.BarAction("play", tooltip="Start", primary=True)], modes=tabs)
        self.assertTrue(center.bar.has_css_class("with-tabs"))
        center.show_bar([ac.BarAction("play", tooltip="Start", primary=True)])
        self.assertFalse(center.bar.has_css_class("with-tabs"), "only a bar with icon tabs widens its key")
        css = (APPKIT / "luma-appkit-bar.css").read_text()
        self.assertIn("box.lumaui-ac-bar.with-tabs button.lumaui-bar-button.bar.primary.icon", css)
        self.assertRegex(css, r"label\.lumaui-tab-count \{\s*min-width: 8px; min-height: 16px; padding: 0 4px; "
                              r"font-size: 10.5px; font-weight: 400; color: @luma_ink;")
        self.assertTrue(tabs._counts["alarms"].has_css_class("lumaui-tab-count"))


if __name__ == "__main__":
    unittest.main()
