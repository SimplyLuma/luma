#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""LumaUI's TV media parts: cover sizes, the room's pre-blur, the equaliser, covers, the pill and the slide."""

from __future__ import annotations

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
from luma_appkit.tv_media import (  # noqa: E402
    TvEqualizer, box_blur, cover_size, grade, hue_sleeve_pixels, hues_for, room_pixels,
)

HAS_DISPLAY = Gtk.init_check() if hasattr(Gtk, "init_check") else Gdk.Display.get_default() is not None
TV = lumaui_tokens.TV


class Sizes(unittest.TestCase):
    def test_covers_are_the_shared_scale(self):
        self.assertEqual(cover_size("square"), (260.0, 260.0))
        self.assertAlmostEqual(cover_size("portrait")[1], 230 * 4 / 3, delta=0.1)
        self.assertEqual(cover_size("landscape"), (400.0, 225.0))
        self.assertEqual(cover_size("square", 520), (520.0, 520.0))
        with self.assertRaises(ValueError):
            cover_size("round")

    def test_transport_and_pill_tokens(self):
        self.assertEqual((TV["button"]["round"], TV["button"]["round_main"]), (84, 108))
        self.assertEqual(TV["media"]["pill"]["max_width"], 520)
        self.assertEqual(TV["media"]["room"]["fade_ms"], 1100)

    def test_hues_are_stable_and_distinct(self):
        self.assertEqual(hues_for("Focus"), hues_for("Focus"))
        self.assertNotEqual(hues_for("Focus"), hues_for("Road trip"))
        self.assertTrue(all(0 <= h < 360 for h in hues_for("Sunday morning")))


class Room(unittest.TestCase):
    def test_box_blur_spreads_and_keeps_flat_areas(self):
        flat = bytearray([90] * (8 * 8 * 3))
        self.assertEqual(box_blur(flat, 8, 8, 3, 2), flat)
        dot = bytearray(5 * 5)
        dot[12] = 250
        out = box_blur(dot, 5, 5, 1, 1, passes=1)
        self.assertEqual(out[12], 27)            # 250 shared over a 3x3 box
        self.assertEqual(out[6], 27)
        self.assertEqual(out[0], 0)

    def test_grade_darkens_and_saturates(self):
        grey = grade(bytearray([200, 200, 200]), 3, 1.5, 0.55)
        self.assertEqual(list(grey), [110, 110, 110])        # grey stays grey, 0.55 as bright
        red = grade(bytearray([200, 100, 100]), 3, 1.5, 1.0)
        self.assertGreater(red[0] - red[1], 100)               # more saturated than it came in

    def test_room_pixels_are_small_and_dark(self):
        side = TV["media"]["room"]["side"]
        pixels = hue_sleeve_pixels((20, 200, 300), side)
        self.assertEqual(len(pixels), side * side * 3)
        room = room_pixels(bytes(pixels), side, side)
        self.assertEqual(len(room), len(pixels))
        self.assertLess(sum(room) / len(room), sum(pixels) / len(pixels))


class Equalizer(unittest.TestCase):
    def test_bars_rise_and_fall_between_low_and_full(self):
        low = TV["media"]["equalizer"]["low"]
        levels = [TvEqualizer.level(t / 20, 0) for t in range(41)]
        self.assertAlmostEqual(min(levels), low, places=3)
        self.assertAlmostEqual(max(levels), 1.0, places=3)
        self.assertNotEqual(TvEqualizer.level(0.3, 0.0), TvEqualizer.level(0.3, 0.4))


@unittest.skipUnless(HAS_DISPLAY, "needs a display")
class Widgets(unittest.TestCase):
    def setUp(self):
        from luma_appkit.tv_stage import TvStage
        self.window = Gtk.Window(default_width=1920, default_height=1080)
        self.stage = TvStage()
        self.window.set_child(self.stage)

    def tearDown(self):
        self.window.destroy()

    def settle(self):
        context = GLib.MainContext.default()
        for _ in range(60):
            while context.pending():
                context.iteration(False)

    def test_cover_is_its_art_plus_caption_in_tv_units(self):
        from luma_appkit.tv_media import TvCover
        cover = TvCover(title="Album", subtitle="Artist")
        self.stage.place(cover, left=96, top=190)
        self.window.present()
        self.settle()
        self.assertEqual(cover.get_width(), round(260 * self.stage.unit))
        self.assertGreater(cover.get_height(), cover.get_width())
        cover.set_tv_focus(True)
        self.assertTrue(cover._focused)

    def test_pill_hides_until_a_track_and_caps_its_width(self):
        from luma_appkit.tv_media import TvNowPlayingPill
        pill = TvNowPlayingPill()
        self.stage.place(pill, right=96, top=56, height=74)
        self.assertFalse(pill.get_visible())
        pill.show_track("A very long song title " * 6, "Artist", None, playing=True)
        self.window.present()
        self.settle()
        self.assertTrue(pill.get_visible())
        self.assertLessEqual(pill.get_width(), TV["media"]["pill"]["max_width"] * self.stage.unit + 1)
        pill.show_track(None)
        self.assertFalse(pill.get_visible())

    def test_transport_buttons_are_round_and_swap_material_with_focus(self):
        from luma_appkit.tv_media import TvTransportButton
        play = TvTransportButton("play", main=True, label="Play")
        self.assertTrue(play.has_css_class("tv-chip"))
        play.set_tv_focus(True)
        self.assertTrue(play.has_css_class("tv-key") and not play.has_css_class("tv-chip"))
        play.set_icon("pause", "Pause")
        self.assertEqual(play.glyph.name, "pause")

    def test_slide_reveals_only_as_far_as_needed(self):
        from luma_appkit.tv_media import TvSlide
        row = Gtk.Box(spacing=0)
        items = [Gtk.Box(width_request=300, height_request=100) for _ in range(10)]
        for item in items:
            row.append(item)
        slide = TvSlide(row, orientation=Gtk.Orientation.HORIZONTAL, peek=0, clip=False)
        self.stage.place(slide, left=0, right=0, top=0, height=100)
        self.window.present()
        self.settle()
        slide.reveal(items[1], animate=False)
        self.assertEqual(slide.offset, 0)                     # already in view
        slide.reveal(items[9], animate=False)
        self.assertEqual(slide.offset, 3000 - slide.get_width())

    def test_grid_makes_only_the_cells_near_the_screen(self):
        from luma_appkit.tv_media import TvGrid, TvSlide
        made, released = [], []

        def make(index, item):
            made.append(item)
            return Gtk.Box(width_request=10, height_request=80)
        grid = TvGrid(columns=6, cell=100, gap=10, row_gap=10, make=make, release=released.append)
        grid.set_items(range(6000), key=lambda item: item)
        slide = TvSlide(grid, orientation=Gtk.Orientation.VERTICAL, lead=0)
        self.stage.place(slide, left=0, right=0, top=0, bottom=0)
        self.window.present()
        self.settle()
        alive = len(grid.cells())
        self.assertGreater(alive, 6)
        self.assertLess(alive, 600)                            # never the whole collection
        _w, _gap, cell_h, row_gap = grid._metrics()
        self.assertEqual(cell_h, 80)
        pitch = cell_h + row_gap
        self.assertEqual(grid.get_height(), 1000 * cell_h + 999 * row_gap)
        slide.slide_to(500 * pitch, animate=False)
        self.settle()
        indexes = [grid.index_of(cell) for cell in grid.cells()]
        self.assertIn(500 * 6, indexes)                        # the rows now on screen exist
        self.assertNotIn(0, indexes)                           # the first rows were let go
        self.assertTrue(released)
        kept = grid.widget_at(3000)
        kept.add_css_class("tv-focus")                          # the remote's focus is never let go
        grid.set_items([3000] + list(range(10)), key=lambda item: item)
        self.assertIs(grid.widget_for(3000), kept)              # stable identity across a reorder
        self.assertEqual(grid.index_of(kept), 0)


if __name__ == "__main__":
    unittest.main()
