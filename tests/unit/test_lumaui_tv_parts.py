#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""LumaUI's TV parts: units, materials, spatial focus, the bar's one bright thing, toast and idle."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src/luma-platform/appkit"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from luma_appkit import lumaui_tokens  # noqa: E402
from luma_appkit.tv_focus import DOWN, LEFT, RIGHT, UP, nearest  # noqa: E402
from luma_appkit.tv_parts import weather_glyph  # noqa: E402
from luma_appkit.tv_stage import tv_css, tv_material  # noqa: E402

HAS_DISPLAY = Gtk.init_check() if hasattr(Gtk, "init_check") else Gdk.Display.get_default() is not None


class Units(unittest.TestCase):
    def test_tv_lengths_become_pixels(self):
        self.assertEqual(tv_css("font-size: 26tv; margin: -1.5tv 0;", 2.0), "font-size: 52.00px; margin: -3.00px 0;")

    def test_other_tokens_are_left_alone(self):
        text = "color: #10tv0; .tvx { } box-shadow: 0 0 0 4tv white; tv-focus"
        self.assertEqual(tv_css(text, 1.0), "color: #10tv0; .tvx { } box-shadow: 0 0 0 4.00px white; tv-focus")


class Materials(unittest.TestCase):
    def test_surfaces_turn_opaque_and_ink_does_not(self):
        self.assertTrue(tv_material("well").endswith("0.45)"))
        self.assertTrue(tv_material("well", opaque=True).endswith(" 1)"))
        self.assertEqual(tv_material("ink_2", opaque=True), tv_material("ink_2"))

    def test_shared_scale_is_the_mockups(self):
        tv = lumaui_tokens.TV
        self.assertEqual((tv["bar"]["top"], tv["bar"]["height"], tv["layout"]["safe_x"]), (56, 74, 96))
        self.assertEqual((tv["button"]["height"], tv["button"]["radius"]), (66, 22))
        self.assertEqual((tv["toast"]["bottom"], tv["toast"]["above_action_bar"], tv["toast"]["above_keyboard"]),
                         (56, 172, 586))


class Spatial(unittest.TestCase):
    #   a b c        (a row of tabs)
    #     d          (a band under b and c)
    #   e f g        (cards)
    RECTS = {"a": (0, 0, 100, 50), "b": (120, 0, 100, 50), "c": (240, 0, 100, 50),
             "d": (0, 100, 340, 40), "e": (0, 200, 100, 80), "f": (120, 200, 100, 80), "g": (240, 200, 100, 80)}

    def go(self, origin, direction):
        return nearest(self.RECTS[origin], [(k, r) for k, r in self.RECTS.items() if k != origin], direction)

    def test_straight_ahead(self):
        self.assertEqual(self.go("a", RIGHT), "b")
        self.assertEqual(self.go("c", LEFT), "b")
        self.assertEqual(self.go("f", UP), "d")
        self.assertEqual(self.go("d", DOWN), "f")   # the card under the band's centre
        self.assertEqual(self.go("d", UP), "b")

    def test_nothing_beyond_an_edge(self):
        self.assertIsNone(self.go("c", RIGHT))
        self.assertIsNone(self.go("a", UP))

    def test_ahead_beats_a_closer_diagonal(self):
        rects = [("diagonal", (160, 60, 40, 40)), ("ahead", (0, 130, 100, 40))]
        self.assertEqual(nearest((0, 0, 100, 50), rects, DOWN), "ahead")


@unittest.skipUnless(HAS_DISPLAY, "needs a display")
class Widgets(unittest.TestCase):
    def test_exactly_one_bright_thing_in_the_bar(self):
        from luma_appkit.tv_parts import TvTab, TvTopBar
        bar = TvTopBar(clock=False)
        tabs = [TvTab("Oakland", icon="navigation"), TvTab("Lisbon"), TvTab("", icon="plus", accessible="Add a place")]
        bar.set_tabs(tabs)
        bar.set_open(0)
        self.assertTrue(tabs[0].has_css_class("tv-chip"))          # focus in the page: the open tab is a chip
        self.assertFalse(any(t.has_css_class("tv-key") for t in tabs))
        self.assertTrue(bar.track.get_hexpand() is False or not bar.track.compute_expand(Gtk.Orientation.HORIZONTAL))
        tabs[2].set_tv_focus(True)
        self.assertTrue(tabs[0].has_css_class("tv-chip"))
        self.assertTrue(tabs[2].has_css_class("tv-key"))
        tabs[2].set_tv_focus(False)
        tabs[0].set_tv_focus(True)
        self.assertTrue(tabs[0].has_css_class("tv-key"))
        self.assertFalse(tabs[0].has_css_class("tv-chip"))

    def test_accent_button(self):
        from luma_appkit.tv_parts import TvButton
        play = TvButton("Play", icon="play", accent=True)
        self.assertTrue(play.has_css_class("tv-accent") and not play.has_css_class("tv-chip"))
        play.set_tv_focus(True)
        self.assertTrue(play.has_css_class("tv-key") and play.has_css_class("tv-accent"))
        play.set_tv_focus(False)
        self.assertFalse(play.has_css_class("tv-chip"))

    def test_idle_ignores_motion_in_place(self):
        from luma_appkit.tv_focus import TvFocus
        from luma_appkit.tv_idle import TvIdle
        from luma_appkit.tv_stage import TvStage
        seen = []
        idle = TvIdle(TvFocus(TvStage()), seconds=20, on_idle=lambda: seen.append("idle"),
                      on_wake=lambda: seen.append("wake"))
        idle.go_idle()
        idle._moved(None, 500, 300)
        idle._moved(None, 503, 301)          # relayout and jitter
        self.assertTrue(idle.idle)
        idle._moved(None, 600, 300)          # a real move
        self.assertFalse(idle.idle)

    def test_focus_moves_in_a_window(self):
        from luma_appkit.tv_focus import TvFocus
        from luma_appkit.tv_stage import TvStage
        stage = TvStage()
        window = Gtk.Window(child=stage, default_width=960, default_height=540)
        boxes = [Gtk.Box(width_request=100, height_request=60) for _ in range(3)]
        for index, box in enumerate(boxes):
            stage.place(box, left=96 + index * 240, top=200)
        focused = []
        focus = TvFocus(stage, on_change=focused.append)
        for box in boxes:
            focus.add(box)
        window.present()
        context = GLib.MainContext.default()
        for _ in range(50):
            context.iteration(False)
        from luma_appkit.lumaui import tv_unit
        self.assertAlmostEqual(stage.unit, tv_unit(stage.get_width(), stage.get_height()), places=4)
        focus.focus(boxes[0])
        self.assertTrue(boxes[0].has_css_class("tv-focus"))
        self.assertTrue(focus.handle(Gdk.KEY_Right))
        self.assertIs(focus.focused, boxes[1])
        self.assertFalse(boxes[0].has_css_class("tv-focus"))
        focus.push_scope([boxes[2]], on_back=lambda: True)
        self.assertIs(focus.focused, boxes[2])
        focus.handle(Gdk.KEY_Left)                       # confined to the scope
        self.assertIs(focus.focused, boxes[2])
        focus.handle(Gdk.KEY_Escape)                     # Back closes it and returns
        self.assertIs(focus.focused, boxes[1])
        window.destroy()

    def test_editable_field_keeps_typing_keys(self):
        from luma_appkit.tv_focus import TvFocus
        from luma_appkit.tv_parts import TvField
        from luma_appkit.tv_stage import TvStage
        stage = TvStage()
        field = TvField(placeholder="Search")
        stage.place(field, left=96, top=200, width=600)
        focus = TvFocus(stage)
        activated = []
        focus.add(field.text, editable=True, on_activate=lambda: activated.append(True))
        focus.focus(field.text)
        self.assertFalse(focus.handle(Gdk.KEY_a))
        self.assertFalse(focus.handle(Gdk.KEY_BackSpace))
        self.assertFalse(focus.handle(Gdk.KEY_Left))
        self.assertTrue(focus.handle(Gdk.KEY_Return))
        self.assertEqual(activated, [True])

    def test_leaving_a_field_takes_gtk_focus_with_it(self):
        from luma_appkit.tv_focus import TvFocus
        from luma_appkit.tv_parts import TvField
        from luma_appkit.tv_stage import TvStage
        stage = TvStage()
        window = Gtk.Window(child=stage, default_width=960, default_height=540)
        field = TvField(placeholder="Search")
        row = Gtk.Box(height_request=40)
        stage.place(field, left=96, top=100, width=600)
        stage.place(row, left=96, top=300, width=600)
        focus = TvFocus(stage)
        picked = []
        focus.add(field.text, editable=True)
        focus.add(row, on_activate=lambda: picked.append("row"))
        window.present()
        for _ in range(50):
            GLib.MainContext.default().iteration(False)
        focus.focus(field.text)
        self.assertIs(window.get_focus(), field.text)
        self.assertTrue(focus.handle(Gdk.KEY_Down))
        self.assertIs(focus.focused, row)
        self.assertIs(window.get_focus(), row)          # not left on the text: the keyboard closes
        self.assertTrue(focus.handle(Gdk.KEY_Return))
        self.assertEqual(picked, ["row"])
        window.destroy()

    def test_hold_ok_or_menu_opens_the_items_menu(self):
        from luma_appkit.commands import Command, CommandGroup, CommandRegistry
        from luma_appkit.tv_focus import TvFocus
        from luma_appkit.tv_stage import TvStage
        stage = TvStage()
        window = Gtk.Window(child=stage, default_width=960, default_height=540)
        item = Gtk.Box(width_request=200, height_request=60)
        stage.place(item, left=96, top=100)
        ran, opened = [], []
        registry = CommandRegistry((CommandGroup("", (
            Command("a.default", "Set as default", lambda: ran.append("default"), "star"),
            Command("a.hidden", "Hidden", lambda: ran.append("hidden"), visible=lambda: False),
            Command("a.remove", "Remove", lambda: ran.append("remove"), "trash-2", destructive=True),
        )),))
        focus = TvFocus(stage)
        focus.add(item, on_activate=lambda: opened.append("ok"), menu=registry)
        window.present()
        context = GLib.MainContext.default()
        for _ in range(50):
            context.iteration(False)
        focus.focus(item)
        self.assertTrue(focus.handle(Gdk.KEY_Return))     # a press alone does nothing yet
        self.assertEqual(opened, [])
        self.assertTrue(focus.release(Gdk.KEY_Return))    # a short press is OK
        self.assertEqual(opened, ["ok"])
        self.assertFalse(focus.in_scope)
        focus.handle(Gdk.KEY_Return)
        focus.handle(Gdk.KEY_Return)                      # auto-repeat while held changes nothing
        end = GLib.get_monotonic_time() + 800_000
        while GLib.get_monotonic_time() < end:
            context.iteration(False)
        self.assertTrue(focus.in_scope)                   # held: the menu
        menu_rows = focus._scopes[-1].items
        self.assertEqual([r.get_first_child().get_last_child().get_label() for r in menu_rows],
                         ["Set as default", "Remove"])
        self.assertTrue(focus.release(Gdk.KEY_Return))    # the release after the hold is swallowed
        self.assertEqual(opened, ["ok"])
        self.assertIs(focus.focused, menu_rows[0])
        focus.handle(Gdk.KEY_Escape)                      # Back closes and returns to the item
        self.assertFalse(focus.in_scope)
        self.assertIs(focus.focused, item)
        self.assertTrue(focus.handle(Gdk.KEY_Menu))       # Menu opens it at once
        end = GLib.get_monotonic_time() + 400_000
        while GLib.get_monotonic_time() < end:
            context.iteration(False)
        focus.handle(Gdk.KEY_Down)
        focus.handle(Gdk.KEY_Return)                      # rows have no menu: OK on press
        self.assertEqual(ran, ["remove"])
        self.assertFalse(focus.in_scope)
        self.assertIs(focus.focused, item)
        window.destroy()

    def test_a_held_ok_with_repeats_opens_the_menu_and_picks_nothing(self):
        from luma_appkit.commands import Command, CommandGroup, CommandRegistry
        from luma_appkit.tv_focus import TvFocus
        from luma_appkit.tv_stage import TvStage
        stage = TvStage()
        window = Gtk.Window(child=stage, default_width=960, default_height=540)
        item = Gtk.Box(width_request=200, height_request=60)
        stage.place(item, left=96, top=100)
        ran = []
        registry = CommandRegistry((CommandGroup("", (
            Command("b.first", "First", lambda: ran.append("first")),
            Command("b.second", "Second", lambda: ran.append("second")),
        )),))
        focus = TvFocus(stage)
        focus.add(item, on_activate=lambda: ran.append("ok"), menu=registry)
        window.present()
        context = GLib.MainContext.default()

        def spin(ms):
            end = GLib.get_monotonic_time() + ms * 1000
            while GLib.get_monotonic_time() < end:
                context.iteration(False)
        spin(300)
        focus.focus(item)
        none = Gdk.ModifierType(0)
        focus._key_pressed(None, Gdk.KEY_Return, 0, none)
        start = GLib.get_monotonic_time()
        while GLib.get_monotonic_time() - start < 650_000:     # held 650 ms, the client repeating from 500
            spin(33)
            if GLib.get_monotonic_time() - start > 500_000:
                focus._key_pressed(None, Gdk.KEY_Return, 0, none)
        focus._key_released(None, Gdk.KEY_Return, 0, none)
        self.assertTrue(focus.in_scope)
        self.assertEqual(ran, [])                                # neither the repeats nor the release pick
        rows = focus._scopes[-1].items
        focus._key_pressed(None, Gdk.KEY_Down, 0, none)          # by order, even before layout
        focus._key_released(None, Gdk.KEY_Down, 0, none)
        self.assertIs(focus.focused, rows[1])
        focus._key_pressed(None, Gdk.KEY_Return, 0, none)        # a new press picks
        focus._key_released(None, Gdk.KEY_Return, 0, none)
        self.assertEqual(ran, ["second"])
        self.assertIs(focus.focused, item)
        window.destroy()

    def test_idle_waits_then_any_key_wakes_and_is_swallowed(self):
        from luma_appkit.tv_focus import TvFocus
        from luma_appkit.tv_idle import TvIdle
        from luma_appkit.tv_stage import TvStage
        stage = TvStage()
        focus = TvFocus(stage)
        seen = []
        idle = TvIdle(focus, seconds=20, on_idle=lambda: seen.append("idle"), on_wake=lambda: seen.append("wake"))
        self.assertTrue(idle._key(Gdk.KEY_I, Gdk.ModifierType.SHIFT_MASK))   # Shift+I: review shortcut
        self.assertEqual(seen, ["idle"])
        self.assertTrue(idle._key(Gdk.KEY_Down, 0))                          # waking key does nothing else
        self.assertEqual(seen, ["idle", "wake"])
        self.assertFalse(idle._key(Gdk.KEY_Down, 0))
        release = idle.hold()
        self.assertEqual(idle._source, 0)
        release()

    def test_toast_replaces_and_rises_over_the_dock(self):
        from luma_appkit.tv_stage import TvStage
        from luma_appkit.tv_toast import TvToast
        stage = TvStage()
        first = TvToast.show(stage, "Added Lisbon", lead="map-pin")
        second = TvToast.show(stage, "Removed Lisbon", kicker="PLACES", value="3")
        self.assertIs(first, second)
        self.assertEqual(stage._pins[second].bottom, 56)
        stage.docked = "keyboard"
        self.assertEqual(stage._pins[second].bottom, 586)
        stage.docked = "action-bar"
        self.assertEqual(stage._pins[second].bottom, 172)



@unittest.skipUnless(HAS_DISPLAY, "needs a display")
class BarCluster(unittest.TestCase):
    """Start's pause menu, from inside an app: the bar's clock is reachable and opens the quick panel."""

    def test_up_reaches_the_bar_right_the_cluster_ok_the_quick_panel(self):
        from unittest import mock
        from luma_appkit import tv_input
        from luma_appkit.tv_focus import TvFocus
        from luma_appkit.tv_parts import TvTab, TvTopBar
        from luma_appkit.tv_stage import TvStage
        stage = TvStage()
        window = Gtk.Window(child=stage, default_width=960, default_height=540)
        bar = TvTopBar()
        stage.place(bar, **TvTopBar.PIN)
        tabs = [TvTab("Home"), TvTab("Library")]
        bar.set_tabs(tabs)
        cards = [Gtk.Box(width_request=100, height_request=60) for _ in range(2)]
        for index, card in enumerate(cards):
            stage.place(card, left=96 + index * 300, top=300)
        focus = TvFocus(stage)
        for tab in tabs:
            focus.add(tab, group="tabs")
        for card in cards:
            focus.add(card, group="cards")        # the app registers nothing for the cluster
        window.present()
        context = GLib.MainContext.default()
        for _ in range(50):
            context.iteration(False)
        focus.focus(cards[1])
        focus.handle(Gdk.KEY_Up)
        self.assertIn(focus.focused, tabs)         # Up from the top row reaches the bar
        focus.focus(tabs[1])
        focus.handle(Gdk.KEY_Right)
        self.assertIs(focus.focused, bar.cluster)  # Right past the tabs: the cluster
        self.assertTrue(bar.cluster.has_css_class("tv-focus"))
        with mock.patch.object(tv_input, "show_quick_panel") as show:
            focus.handle(Gdk.KEY_Return)
        show.assert_called_once()                  # OK: the shell's quick panel over the app
        focus.handle(Gdk.KEY_Down)
        self.assertIn(focus.focused, cards)        # and back into the page
        self.assertFalse(bar.cluster.has_css_class("tv-focus"))
        window.destroy()

    def test_the_clock_stays_on_the_safe_line(self):
        from luma_appkit.tv_parts import TvTopBar
        from luma_appkit.tv_stage import TvStage
        stage = TvStage()
        window = Gtk.Window(child=stage, default_width=960, default_height=540)
        bar = TvTopBar()
        stage.place(bar, **TvTopBar.PIN)
        window.present()
        context = GLib.MainContext.default()
        for _ in range(50):
            context.iteration(False)
        u = stage.unit
        ok, clock = bar.clock.compute_bounds(stage)
        ok2, cluster = bar.cluster.compute_bounds(stage)
        self.assertTrue(ok and ok2)
        right = stage.get_width() - 96 * u
        pad = lumaui_tokens.TV["quick_panel"]["cluster_padding_x"] * u
        self.assertAlmostEqual(clock.get_x() + clock.get_width(), right, delta=1.0)
        self.assertAlmostEqual(cluster.get_x() + cluster.get_width(), right + pad, delta=1.0)
        window.destroy()


if __name__ == "__main__":
    unittest.main()


class Weather(unittest.TestCase):
    """An app's TV bar shows Home's weather beside the time, drawn as the home's bar draws it."""

    def test_glyph_matches_the_home_bar(self):
        # tvModel.js weatherGlyph: the same conditions, the same icons and tints.
        cases = {("Thunderstorms", 12): ("lumaui-cloud-lightning-symbolic", None),
                 ("Light snow", 12): ("lumaui-snowflake-symbolic", None),
                 ("Rain", 23): ("lumaui-cloud-rain-symbolic", None),
                 ("Fog", 8): ("lumaui-cloud-fog-symbolic", None),
                 ("Cloudy", 8): ("lumaui-cloud-symbolic", None),
                 ("Partly cloudy", 12): ("lumaui-cloud-sun-symbolic", "sun"),
                 ("Fair", 22): ("lumaui-cloud-moon-symbolic", "moon"),
                 ("Clear", 6): ("lumaui-sun-symbolic", "sun"),
                 ("Clear", 19): ("lumaui-moon-symbolic", "moon"),
                 ("", 3): ("lumaui-moon-symbolic", "moon")}
        for (condition, hour), expected in cases.items():
            self.assertEqual(weather_glyph(condition, hour), expected, (condition, hour))

    @unittest.skipUnless(HAS_DISPLAY, "needs a display")
    def test_bar_shows_a_reading_and_hides_without_one(self):
        from luma_appkit.tv_parts import TvTopBar
        bar = TvTopBar()
        bar.show_weather({})
        self.assertFalse(bar.weather.get_visible() or bar.divider.get_visible())
        bar.show_weather({"value": "57\u00b0", "description": "Clear"})
        self.assertTrue(bar.weather.get_visible() and bar.divider.get_visible())
        self.assertEqual(bar.weather_label.get_label(), "57\u00b0")
        # The weather sits in the cluster before the clock: weather | time.
        self.assertIs(bar.weather.get_next_sibling(), bar.divider)
        self.assertIs(bar.divider.get_next_sibling(), bar.clock)


class SlideBleed(unittest.TestCase):
    @unittest.skipUnless(HAS_DISPLAY, "needs a display")
    def test_a_clipping_slide_leaves_room_for_a_lifted_cover(self):
        # A focused cover lifts and casts its shadow past the slide's box; the
        # slide clips itself with SLIDE_BLEED of room, never at its own edge.
        from luma_appkit.tv_media import SLIDE_BLEED, TvSlide
        slide = TvSlide(Gtk.Box(), orientation=Gtk.Orientation.HORIZONTAL)
        self.assertEqual(slide.get_overflow(), Gtk.Overflow.VISIBLE)
        self.assertTrue(slide._clip)
        cover = lumaui_tokens.TV["cover"]
        self.assertGreaterEqual(SLIDE_BLEED, cover["square"] * (cover["focus_scale"] - 1) / 2 + cover["ring"])

