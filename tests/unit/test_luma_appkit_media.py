# SPDX-License-Identifier: Apache-2.0
"""LumaUI media family (KB-C): tiles and grids, transport, voice clips, waveforms,
cover art, adjustment sliders, image viewport and crop, colour choice, charts
and measures, and the timeline.

`Sources` and `Pure` read files and pure helpers and run anywhere GTK's
introspection data is installed. `Parts` builds real widgets on stock GTK 4
and needs a display (it never presents a window); it is skipped otherwise.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPKIT = ROOT / "src/luma-platform/appkit"
KIT = APPKIT / "luma_appkit"
SHEET = APPKIT / "luma-appkit-media.css"
FRAGMENT = ROOT / "config/shared/design-tokens.d/media.json"
GALLERY = ROOT / "src/luma-platform/tools/lumaui-gallery/pages_media.py"

sys.path.insert(0, str(APPKIT))
try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk

    HAVE_GTK = True
    HAVE_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
except (ImportError, ValueError):
    HAVE_GTK = HAVE_DISPLAY = False

COLOUR_LITERAL = re.compile(r"(?<![\w-])#[0-9a-fA-F]{3,8}\b|\brgba?\(")


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _texture(red: int = 90, green: int = 120, blue: int = 160, width: int = 30, height: int = 20):
    data = bytes([red, green, blue] * width * height)
    return Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8, GLib.Bytes.new(data), width * 3)


def _drain() -> None:
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


class Sources(unittest.TestCase):
    def test_generated_tokens_carry_the_fragment(self):
        from luma_appkit import lumaui_tokens

        fragment = json.loads(FRAGMENT.read_text())["lumaui"]
        for group, values in fragment["media"].items():
            stated = {k: v for k, v in values.items() if not k.startswith("_")}
            self.assertEqual(lumaui_tokens.MEDIA[group], stated,
                             f"lumaui_tokens.MEDIA[{group!r}] is stale: run generate-luma-platform-tokens.py")
        sheets = {"light": "luma-appkit-tokens.css", "dark": "luma-appkit-dark-tokens.css"}
        for appearance, sheet in sheets.items():
            text = (APPKIT / sheet).read_text()
            for role in fragment["colors"][appearance]:
                self.assertIn(f"@define-color luma_{role} ", text, f"{role} missing from {sheet}")

    def test_every_appearance_names_every_colour(self):
        colours = json.loads(FRAGMENT.read_text())["lumaui"]["colors"]
        names = [set(k for k in roles if not k.startswith("_")) for roles in colours.values()]
        self.assertTrue(all(n == names[0] for n in names), "an appearance is missing a colour role")

    def test_sheet_names_colours_and_is_balanced(self):
        text = _strip_comments(SHEET.read_text())
        self.assertEqual(COLOUR_LITERAL.findall(text), [], "the media sheet writes a colour instead of naming it")
        self.assertEqual(text.count("{"), text.count("}"), "unbalanced braces in the media sheet")

    def test_sheet_uses_only_family_variables_that_exist(self):
        generated = set(re.findall(r"(--lumaui-[a-z0-9-]+):", (APPKIT / "luma-appkit-tokens.css").read_text()))
        missing = sorted(set(re.findall(r"var\((--lumaui-[a-z0-9-]+)\)", SHEET.read_text())) - generated)
        self.assertEqual(missing, [], "the media sheet names variables the token sheet does not define")

    def test_modules_do_not_import_widgets(self):
        for module in KIT.glob("media_*.py"):
            self.assertNotRegex(module.read_text(), r"from \.widgets import|import widgets", module.name)

    def test_parts_have_a_css_banner_and_gallery_page(self):
        css = SHEET.read_text()
        gallery = GALLERY.read_text()
        for banner, key in (("MediaTile / MediaGrid", "media-grid"), ("MediaTransport", "media-transport"),
                            ("ImageViewport", "media-viewport"),
                            ("AudioWaveform", "media-waveform"),
                            ("ValueSlider / AdjustmentGroup / AdjustmentPanel", "media-adjust"),
                            ("CoverArt", "media-cover"), ("VoiceClip", "media-voice")):
            self.assertTrue(f"/* LumaUI: {banner}" in css, f"no /* LumaUI: {banner} */ block in the media sheet")
            self.assertTrue(f'"{key}"' in gallery, f"no gallery page {key!r} in pages_media.py")


class Pure(unittest.TestCase):
    def test_corners_follow_real_neighbours(self):
        from luma_appkit.media_grid import tile_corners

        # 7 tiles in 3 columns: rows [0 1 2] [3 4 5] [6].
        self.assertEqual(tile_corners(0, 7, 3), {"tl"})
        self.assertEqual(tile_corners(2, 7, 3), {"tr"})
        self.assertEqual(tile_corners(5, 7, 3), {"br"})   # nothing below 5
        self.assertEqual(tile_corners(6, 7, 3), {"bl", "br"})  # alone on the last row
        self.assertEqual(tile_corners(4, 7, 3), set())
        self.assertEqual(tile_corners(0, 1, 4), {"tl", "tr", "br", "bl"})
        self.assertEqual(tile_corners(1, 2, 4), {"tr", "br"})

    def test_insets_keep_tiles_one_gap_apart(self):
        from luma_appkit.media_grid import tile_insets

        columns, gap, cell = 4, 3.0, 100.0
        edges = []
        for col in range(columns):
            left, right, _bottom = tile_insets(col, 8, columns, gap)
            edges.append((col * cell + left, (col + 1) * cell - right))
        widths = {round(b - a, 6) for a, b in edges}
        self.assertEqual(len(widths), 1, "tiles differ in width")
        for (_a, b), (c, _d) in zip(edges, edges[1:]):
            self.assertAlmostEqual(c - b, gap)
        self.assertEqual(tile_insets(7, 8, 4, 3)[2], 0.0)
        self.assertEqual(tile_insets(3, 8, 4, 3)[2], 3.0)

    def test_columns_match_css_auto_fill(self):
        from luma_appkit.media_grid import grid_columns

        # repeat(auto-fill, minmax(168px, 1fr)) with a 3 px gap.
        self.assertEqual(grid_columns(696, "photo"), 4)
        self.assertEqual(grid_columns(170, "photo"), 1)
        self.assertEqual(grid_columns(338, "photo"), 1)
        self.assertEqual(grid_columns(339, "photo"), 2)
        self.assertEqual(grid_columns(1000, "photo", phone=True), 3)
        self.assertEqual(grid_columns(362, "photo", 168, phone=True), 2)
        self.assertEqual(grid_columns(362, "photo", 168 * 1.55, phone=True), 1)
        self.assertEqual(grid_columns(362, "photo", 110, phone=True), 3)
        self.assertEqual(grid_columns(696, "photo", 168 * 1.55), 2)
        self.assertEqual(grid_columns(240, "clip"), 2)       # Reel's 264 px browser
        self.assertEqual(grid_columns(900, "library"), 5)

    def test_transport_times(self):
        from luma_appkit.media_transport import clock_text, remaining_text, timecode_parts

        self.assertEqual(clock_text(43.9), "0:43")
        self.assertEqual(clock_text(3725), "1:02:05")
        self.assertEqual(clock_text(float("nan")), "0:00")
        self.assertEqual(remaining_text(43, 184), "\u22122:21")
        self.assertEqual(remaining_text(200, 184), "\u22120:00")
        self.assertEqual(timecode_parts(12.2, 24), ("00:12", ":05"))
        self.assertEqual(timecode_parts(95.5, None), ("01:35", ""))

    def test_waveform_resampling_keeps_peaks(self):
        from luma_appkit.waveform import mini_bars, resample_peaks

        peaks = [0.1] * 100
        peaks[37] = 0.9
        bars = resample_peaks(peaks, 10)
        self.assertEqual(len(bars), 10)
        self.assertEqual(bars[3], 0.9)
        self.assertEqual(resample_peaks([], 10), [])
        rects = mini_bars([0.5] * 160)
        self.assertEqual(len(rects), 40)
        self.assertEqual(rects[0], (0.0, 4.5, 7.0))

    def test_glyph_paths_start_absolute(self):
        from luma_appkit.media_style import _absolute_start

        self.assertEqual(_absolute_start("m17 2 4 4-4 4"), "M17 2 l4 4-4 4")
        self.assertEqual(_absolute_start("M3 11v-1"), "M3 11v-1")
        self.assertEqual(_absolute_start("m7 22-4-4 4-4"), "M7 22 l-4-4 4-4")

    def test_slider_readouts_and_fill(self):
        from luma_appkit.media_adjust import fill_span, value_text

        self.assertEqual(value_text(12, -100), "+12")
        self.assertEqual(value_text(-3, -60, "dB"), "\u22123 dB")
        self.assertEqual(value_text(80, 0, "%"), "80%")
        self.assertEqual(value_text(0, -100), "0")
        self.assertEqual(value_text(4, -15, "°"), "+4°")
        self.assertEqual(value_text(0.25, 0, "", 0.05), "0.25")
        self.assertEqual(fill_span(20, -100, 100), (0.5, 0.6))
        self.assertEqual(fill_span(-50, -100, 100), (0.25, 0.5))
        self.assertEqual(fill_span(30, 0, 100), (0.0, 0.3))

    def test_cover_hues_are_stable(self):
        from luma_appkit.media_cover import cover_hues

        self.assertEqual(cover_hues("Blue Hour", (250, 285, 210)), (250, 285, 210))
        self.assertEqual(cover_hues("Blue Hour"), cover_hues("  blue hour "))
        self.assertNotEqual(cover_hues("Blue Hour"), cover_hues("Night Bus"))
        h, h2, h3 = cover_hues("x", (100,))
        self.assertEqual((h, h2, h3), (100, 135, 60))

    def test_voice_clip_time(self):
        from luma_appkit.media_voice import clip_time

        self.assertEqual(clip_time(0, 12.4), "0:12")
        self.assertEqual(clip_time(5.2, 12.4), "0:08")
        self.assertEqual(clip_time(12.4, 12.4), "0:00")
        self.assertEqual(clip_time(0, 75), "1:15")

    def test_cover_and_contain(self):
        from luma_appkit import media_style

        box = media_style.rect(0, 0, 100, 100)
        cover = media_style.cover_rect(200, 100, box)
        self.assertEqual((cover.get_width(), cover.get_height(), cover.get_x()), (200, 100, -50))
        contain = media_style.contain_rect(200, 100, box)
        self.assertEqual((contain.get_width(), contain.get_height(), contain.get_y()), (100, 50, 25))

    def test_viewport_fit_is_before_rotation_and_insets_only_affect_image(self):
        from luma_appkit.media_viewport import fit_bounds, normalise_adjustments

        self.assertEqual(fit_bounds(800, 600, 20, 10, (88, 32, 96, 32)),
                         (32, 112, 736, 368))
        self.assertEqual(fit_bounds(500, 700, 10, 20, (132, 32, 360, 32)),
                         (198, 132, 104, 208))
        self.assertEqual(fit_bounds(100, 100, 20, 10, (0, 60, 0, 60)),
                         (0, 0, 0, 0))
        incoming = {"e": 25, "rot": 90, "flip": True}
        copied = normalise_adjustments(incoming)
        incoming["e"] = 99
        self.assertEqual((copied["e"], copied["rot"], copied["flip"]), (25, 90, True))
        self.assertEqual(normalise_adjustments(None)["pre"], "")
        for invalid in ({"rot": 45}, {"pre": "unknown"}, {"crop": "3:2"},
                        {"e": float("nan")}, {"flip": 1}):
            with self.assertRaises(ValueError):
                normalise_adjustments(invalid)


@unittest.skipUnless(HAVE_DISPLAY, "needs a display")
class Parts(unittest.TestCase):
    def test_image_viewport_snapshots_real_texture_and_keeps_full_stage(self):
        from luma_appkit import ImageViewport

        viewport = ImageViewport(surround="black")
        viewport.allocate(800, 600, -1, None)
        viewport.set_fit_insets(88, 32, 96, 32)
        self.assertEqual(viewport.get_content_bounds(), (0, 0, 0, 0))
        texture = _texture(width=20, height=10)
        viewport.set_paintable(texture)
        self.assertEqual(viewport.get_content_bounds(), (32, 112, 736, 368))
        viewport.set_adjustments({'crop': 'square'})
        self.assertEqual(viewport.get_content_bounds(), (192, 88, 416, 416))
        viewport.set_adjustments(None)
        snapshot = Gtk.Snapshot.new()
        viewport.do_snapshot(snapshot)
        node = snapshot.to_node()
        self.assertIsNotNone(node)
        self.assertEqual((node.get_bounds().get_width(), node.get_bounds().get_height()), (800, 600))
        original = {"e": 20, "c": 10, "hi": 5, "sh": 5, "w": 12, "sat": -8,
                    "pre": "warm", "rot": 90, "flip": True}
        viewport.set_adjustments(original)
        original["rot"] = 0
        self.assertEqual(viewport._adjustments["rot"], 90)
        adjusted = Gtk.Snapshot.new()
        viewport.do_snapshot(adjusted)
        self.assertIsNotNone(adjusted.to_node())
        self.assertEqual(viewport.get_content_bounds(), (192, 192, 416, 208),
                         "quarter turns must fit the portrait inside the inset area")
        for rotation, expected in ((0, (32, 112, 736, 368)),
                                   (90, (192, 192, 416, 208)),
                                   (180, (32, 112, 736, 368)),
                                   (270, (192, 192, 416, 208))):
            viewport.set_adjustments({'rot': rotation, 'flip': True})
            x, y, width, height = viewport.get_content_bounds()
            self.assertEqual((x, y, width, height), expected)
            shown_width, shown_height = ((height, width) if rotation in (90, 270)
                                          else (width, height))
            center_x, center_y = x + width / 2, y + height / 2
            self.assertGreaterEqual(center_x - shown_width / 2, 32)
            self.assertLessEqual(center_x + shown_width / 2, 768)
            self.assertGreaterEqual(center_y - shown_height / 2, 88)
            self.assertLessEqual(center_y + shown_height / 2, 504)
            rotated = Gtk.Snapshot.new()
            viewport.do_snapshot(rotated)
            rotated_node = rotated.to_node()
            self.assertIsNotNone(rotated_node)
            self.assertEqual((rotated_node.get_bounds().get_width(),
                              rotated_node.get_bounds().get_height()), (800, 600))
        viewport.set_paintable(None)
        empty = Gtk.Snapshot.new()
        viewport.do_snapshot(empty)
        self.assertIsNotNone(empty.to_node(), "an empty viewer must still paint its black stage")

    def test_physical_phone_viewer_paints_square_unshadowed_image(self):
        from luma_appkit import ImageViewport

        window = Gtk.Window()
        viewport = ImageViewport(surround="black")
        window.set_child(viewport)
        viewport.set_paintable(_texture(width=20, height=10))
        viewport.allocate(402, 874, -1, None)
        desktop = Gtk.Snapshot.new()
        viewport.do_snapshot(desktop)
        self.assertIn(b"outset-shadow", desktop.to_node().serialize().get_data())
        window.add_css_class("lumaui-phone-device")
        phone = Gtk.Snapshot.new()
        viewport.do_snapshot(phone)
        serialized = phone.to_node().serialize().get_data()
        self.assertNotIn(b"outset-shadow", serialized)
        self.assertNotIn(b"rounded-clip", serialized)
        self.assertEqual(viewport.get_content_bounds(), (0, 336.5, 402, 201))
        window.destroy()

    def test_tile_refuses_unknown_kinds(self):
        from luma_appkit import MediaTile

        with self.assertRaises(ValueError):
            MediaTile(kind="square")

    def test_tile_reads_its_state(self):
        from luma_appkit import MediaTile

        tile = MediaTile(_texture(), title="Pink coat", favourite=True, badge="Edited", kind="library", dimmed=True)
        self.assertEqual(tile.accessible_text(), "Pink coat, Favourite, Edited, Set aside")
        tile.set_selected(True)
        self.assertTrue(tile.has_css_class("selected"))
        opened = []
        tile.on_open = lambda: opened.append(True)
        tile._open()
        self.assertEqual(opened, [True])

    def test_tile_is_square_or_its_kind_aspect(self):
        from luma_appkit import MediaTile

        self.assertEqual(MediaTile(kind="photo").measure(Gtk.Orientation.VERTICAL, 168)[1], 168)
        self.assertEqual(MediaTile(kind="library").measure(Gtk.Orientation.VERTICAL, 180)[1], 120)
        self.assertEqual(MediaTile(kind="clip").measure(Gtk.Orientation.VERTICAL, 160)[1], 90)

    def test_grid_binds_items_and_routes_activation(self):
        from luma_appkit import MediaGrid, MediaItem

        items = [MediaItem(str(i), _texture(), title=f"Photo {i}") for i in range(5)]
        seen = []
        grid = MediaGrid(items, kind="library", scrolls=False, on_select=lambda item, mode: seen.append((item.id, mode)),
                         on_open=lambda item: seen.append((item.id, "open")))
        self.assertEqual(grid.model.get_n_items(), 5)
        grid._activated(items[2], "extend")
        grid._opened(items[3])
        self.assertEqual(seen, [("2", "extend"), ("3", "open")])
        # Every tile exists in a flat group and follows its item.
        tile = grid._flat[1]
        items[1].favourite = True
        self.assertTrue(tile.favourite)
        grid.model.remove(0)
        _drain()
        self.assertEqual(len(grid._flat), 4)

    def test_flat_grid_height_is_its_rows(self):
        from luma_appkit import MediaGrid, MediaItem

        grid = MediaGrid([MediaItem(str(i), _texture()) for i in range(6)], kind="photo", scrolls=False)
        # 4 columns at 696: two rows of (696 - 9) / 4 = 171.75 with one 3 px gap.
        height = grid.measure(Gtk.Orientation.VERTICAL, 696)[1]
        self.assertEqual(height, 172 + 175)

    def test_scrolling_grid_is_a_recycling_view(self):
        from luma_appkit import MediaGrid, MediaItem

        store = Gio.ListStore(item_type=MediaItem)
        for i in range(2000):
            store.append(MediaItem(str(i), None))
        grid = MediaGrid(store, kind="photo")
        self.assertIsInstance(grid.view, Gtk.GridView)
        self.assertIs(grid.model, store)

    def test_transport_variants_and_intents(self):
        from luma_appkit import MediaTransport

        with self.assertRaises(ValueError):
            MediaTransport("bar")
        played, seeks, steps, loops = [], [], [], []
        deck = MediaTransport("deck", duration=184, position=43, shuffle=False, repeat=False, volume=0.5,
                              on_play=played.append, on_seek=seeks.append, on_step=steps.append)
        self.assertEqual(deck._elapsed.get_label(), "0:43")
        self.assertEqual(deck._left.get_label(), "\u22122:21")
        deck.toggle_play()
        self.assertEqual(played, [True])
        self.assertTrue(deck.playing)
        self.assertEqual(deck.play_key.get_tooltip_text(), "Pause")
        deck.key("next").emit("clicked")
        self.assertEqual(steps, [1])
        deck.key("shuffle").emit("clicked")
        self.assertTrue(deck.shuffle and deck.key("shuffle").has_css_class("on"))
        self.assertIsNotNone(deck.volume_control)
        deck.scrub.emit("change-value", Gtk.ScrollType.JUMP, 100.0)
        self.assertEqual(seeks, [100.0])
        # While the listener holds the scrub, the player's position waits.
        deck._scrubbing = True
        deck.set_position(10)
        self.assertEqual(deck.position, 100.0)
        deck._scrubbing = False
        deck._held_until = 0
        deck.set_position(10)
        self.assertEqual(deck.position, 10)

        reel = MediaTransport("attached", duration=95.5, position=12.2, fps=24, loop=False, on_loop=loops.append,
                              on_fullscreen=lambda: None)
        value, _caption = reel._lcd_cells[0]
        texts = []
        child = value.get_first_child()
        while child is not None:
            texts.append((child.get_label(), child.has_css_class("dim")))
            child = child.get_next_sibling()
        self.assertEqual(texts, [("00:12", False), (":05", True)])
        reel.key("loop").emit("clicked")
        self.assertEqual(loops, [True])
        reel.key("end").emit("clicked")
        self.assertEqual(reel.position, 95.5)

        session = MediaTransport("lcd", lcd=[(("12", "3", "1"), "Bar"), ("0:21.4", "Time")],
                                 readouts=[("112", "BPM", lambda: None)], recording=False, loop=True)
        self.assertEqual(len(session._lcd_cells), 2)
        self.assertIsNotNone(session.key("record"))

    def test_waveform_deck_takes_the_phone_shape(self):
        # v71 @container win (max-width: 559px) .medctl: the transport on its own row, time and speed under it,
        # 24 and 28 glyphs, the waveform's bars 1 px apart; and back again on a computer.
        from luma_appkit import AudioWaveform, MediaTransport
        wave = AudioWaveform()
        memo = MediaTransport("waveform", duration=63, speed=1.0, waveform=wave)
        keys, row, column = memo._keys_row, memo._controls, memo._column
        self.assertIs(keys.get_parent(), row, "a computer: time · keys · speed on one line")
        self.assertEqual(wave.gap, 2.0)
        memo._phone_shape(True)
        self.assertIs(keys.get_parent(), column)
        self.assertIs(column.get_first_child(), keys, "the transport first, then the time and speed line")
        self.assertIsNone(row.get_center_widget())
        self.assertEqual((memo.play_key.glyph._size, memo.key("back").glyph._size), (28, 24))
        self.assertEqual(wave.gap, 1.0)
        memo._phone_shape(False)
        self.assertIs(keys.get_parent(), row)
        self.assertEqual((wave.gap, memo.play_key.glyph._size), (2.0, 19))

    def test_now_playing_phone_shape(self):
        # v71 .tnowph: scrub, elapsed / time left under its ends, the five keys spread, then the volume.
        from luma_appkit import MediaTransport
        played, steps, shuffled = [], [], []
        now = MediaTransport("now", duration=225, position=84, playing=False, shuffle=False, repeat=False, volume=0.5,
                             on_play=played.append, on_step=steps.append, on_shuffle=shuffled.append)
        def _children(widget):
            out, child = [], widget.get_first_child()
            while child is not None:
                out.append(child)
                child = child.get_next_sibling()
            return out

        self.assertIn("now", now.get_css_classes())
        self.assertEqual(now.get_orientation(), Gtk.Orientation.VERTICAL)
        scrub, times, keys, volume = _children(now)
        self.assertIs(scrub, now.scrub)
        self.assertEqual((now._elapsed.get_label(), now._left.get_label()), ("1:24", "−2:21"))
        order = [k for k in ("shuffle", "previous", "next", "repeat") if now.key(k) is not None]
        self.assertEqual(order, ["shuffle", "previous", "next", "repeat"])
        self.assertEqual([now.key(k).glyph._size for k in order], [22, 32, 32, 22])
        self.assertEqual(now.play_key.glyph._size, 32)
        buttons = [c for c in _children(keys) if isinstance(c, Gtk.Button)]
        self.assertEqual(buttons, [now.key("shuffle"), now.key("previous"), now.play_key, now.key("next"), now.key("repeat")])
        self.assertEqual(len(_children(keys)), 9, "a spacer between each pair: space-between")
        self.assertIs(volume, now.volume_control)
        self.assertTrue(now._volume_range.get_hexpand())
        now.play_key.emit("clicked")
        now.key("next").emit("clicked")
        now.key("shuffle").emit("clicked")
        self.assertEqual((played, steps, shuffled), ([True], [1], [True]))
        now.set_position(100)
        self.assertEqual(now._elapsed.get_label(), "1:40")

    def test_speed_menu_is_v71s(self):
        # Memos' speed menu: "Speed", 0.75-2 (no 0.5), a hairline, the app's own rows.
        from luma_appkit import MediaTransport
        from luma_appkit.action_bubble import MenuItem
        chosen = []
        memo = MediaTransport("waveform", duration=63, speed=1.0, speeds=(0.75, 1, 1.25, 1.5, 2), on_speed=chosen.append,
                              speed_extras=lambda: [MenuItem("Skip silences", icon="skip-forward", selected=True)])
        rows = memo._speed_rows()
        self.assertEqual(rows[0], "Speed")
        self.assertEqual([r.label for r in rows[1:6]], ["0.75×", "1×", "1.25×", "1.5×", "2×"])
        self.assertTrue(rows[2].selected and not rows[1].selected)
        self.assertIsNone(rows[6])
        self.assertEqual((rows[7].label, rows[7].selected), ("Skip silences", True))
        rows[4].on_activate()
        self.assertEqual(chosen, [1.5])
        plain = MediaTransport("waveform", duration=63, speed=1.0)._speed_rows()
        self.assertEqual(len(plain), 1 + 6, "default: the heading and the six rates, no hairline")

    def test_waveform_deck_seeks_and_speeds(self):
        from luma_appkit import AudioWaveform, MediaTransport

        wave = AudioWaveform()
        wave.set_audio([0.2, 0.8] * 100, 63)
        seeks, speeds, skips = [], [], []
        memo = MediaTransport("waveform", duration=63, speed=1.0, waveform=wave, on_seek=seeks.append,
                              on_speed=speeds.append, on_skip=skips.append)
        self.assertEqual(wave.kind, "deck")
        wave.emit("seek", 30.0)
        self.assertEqual(seeks, [30.0])
        self.assertEqual(memo._elapsed.get_label(), "0:30")
        self.assertEqual(memo._total.get_label(), "/ 1:03")
        memo.key("forward").emit("clicked")
        self.assertEqual(skips, [15])
        memo.set_speed(1.5)
        self.assertEqual(memo._speed_label.get_label(), "1.5×")

    def test_waveform_keys_and_live(self):
        from luma_appkit import AudioWaveform

        wave = AudioWaveform("well")
        wave.set_audio([0.5] * 50, 60)
        seeks = []
        wave.connect("seek", lambda _w, s: seeks.append(s))
        wave._key(None, Gdk.KEY_Right, 0, 0)
        wave._key(None, Gdk.KEY_End, 0, 0)
        self.assertEqual(seeks, [5.0, 60.0])
        self.assertEqual(wave.measure(Gtk.Orientation.VERTICAL, -1)[1], 120)
        wave.set_live(True)
        for _ in range(200):
            wave.push_peak(0.4)
        self.assertEqual(len(wave.peaks), 150)
        mini = AudioWaveform("mini")
        self.assertEqual(mini.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 44)
        self.assertFalse(mini.get_focusable())
        with self.assertRaises(ValueError):
            AudioWaveform("big")

    def test_value_slider_moves_resets_and_reports(self):
        from luma_appkit import AdjustmentGroup, AdjustmentPanel, ValueSlider

        changes, resets = [], []
        slider = ValueSlider("Exposure", 0, -100, 100, on_change=changes.append, on_reset=lambda: resets.append(1))
        self.assertEqual(slider.value_label.get_label(), "0")
        self.assertFalse(slider.has_css_class("set"))
        slider.range._key(None, Gdk.KEY_Right, 0, 0)
        slider.range._key(None, Gdk.KEY_Page_Up, 0, 0)
        self.assertEqual(changes, [1.0, 11.0])
        self.assertEqual(slider.value_label.get_label(), "+11")
        self.assertTrue(slider.has_css_class("set"))
        slider.reset()
        self.assertEqual((slider.value, resets, changes[-1]), (0.0, [1], 0.0))
        slider.set_value(250)
        self.assertEqual(slider.value, 100.0)
        with self.assertRaises(ValueError):
            ValueSlider("Bad", layout="grid")

        group = AdjustmentGroup("Light", [slider, ValueSlider("Contrast")])
        self.assertFalse(group.open)
        self.assertTrue(group.dot.get_visible())
        group.header.emit("clicked")
        self.assertTrue(group.open and group.has_css_class("open"))

        panel_resets = []
        panel = AdjustmentPanel([("Light", [ValueSlider("Exposure", 20, layout="stacked")]), group],
                                on_reset=lambda: panel_resets.append(1))
        self.assertTrue(panel.reset_button.get_sensitive())
        panel.reset()
        self.assertEqual(panel_resets, [1])
        self.assertFalse(panel.changed)
        self.assertFalse(panel.reset_button.get_sensitive())
        self.assertFalse(group.dot.get_visible())

    def test_cover_art_sizes_and_real_art(self):
        from luma_appkit import CoverArt

        hero = CoverArt("Blue Hour", "Mara Sol", size="hero")
        self.assertEqual(hero.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 264)
        self.assertEqual(hero.measure(Gtk.Orientation.VERTICAL, 264)[1], 264)
        book = CoverArt("The Salt Road", "I. Varga", shape="book", size="mini")
        self.assertEqual(book.measure(Gtk.Orientation.VERTICAL, 46)[1], 69)
        self.assertTrue(book.generated)
        book.set_picture(_texture())
        self.assertFalse(book.generated)
        tile = CoverArt("Salt", size="tile")
        self.assertEqual(tile.measure(Gtk.Orientation.VERTICAL, 200)[1], 200)
        with self.assertRaises(ValueError):
            CoverArt("x", shape="disc")

    def test_voice_clip_plays_and_seeks(self):
        from luma_appkit import VoiceClip

        toggles, seeks = [], []
        clip = VoiceClip(12.4, peaks=[0.3, 0.9] * 40, on_toggle=toggles.append, on_seek=seeks.append)
        self.assertTrue(clip.has_css_class("wave"))
        self.assertEqual(clip.time.get_label(), "0:12")
        clip.key.emit("clicked")
        self.assertEqual(toggles, [True])
        self.assertEqual(clip._glyph.name, "pause")
        clip.shape.emit("seek", 6.0)
        self.assertEqual(seeks, [6.0])
        self.assertEqual(clip.time.get_label(), "0:07")
        voicemail = VoiceClip(31, on_seek=seeks.append)
        self.assertTrue(voicemail.has_css_class("bar"))
        voicemail._seek_fraction(0.5)
        self.assertEqual(seeks[-1], 15.5)
        self.assertEqual(voicemail.shape.measure(Gtk.Orientation.VERTICAL, -1)[1], 4)

    def test_texture_loader_decodes_off_the_main_loop(self):
        import tempfile

        from luma_appkit.media_style import TextureLoader

        with tempfile.TemporaryDirectory() as folder:
            path = f"{folder}/p.png"
            _texture(width=400, height=300).save_to_png(path)
            loader = TextureLoader(capacity=4, workers=1)
            got = []
            loader.request(path, 120, got.append)
            for _ in range(200):
                _drain()
                if got:
                    break
                import time

                time.sleep(0.01)
            self.assertEqual(len(got), 1)
            self.assertIsNotNone(got[0])
            self.assertEqual(min(got[0].get_width(), got[0].get_height()), 128)
            self.assertIs(loader.cached(path, 120), got[0])


if __name__ == "__main__":
    unittest.main()
