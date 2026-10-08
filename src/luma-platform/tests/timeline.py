# SPDX-License-Identifier: Apache-2.0
"""MD3's public Reel DTO contract and real GTK widget behavior."""
from __future__ import annotations

import unittest
from dataclasses import dataclass

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk
    HAVE_DISPLAY = bool(Gtk.init_check() and Gdk.Display.get_default())
except (ImportError, AttributeError, ValueError):
    HAVE_DISPLAY = False


@dataclass(frozen=True)
class Clip:
    id: str
    name: str
    kind: str
    start_frame: int
    duration_frames: int
    text: str = ""
    transition_after: str | None = None


@dataclass(frozen=True)
class Lane:
    id: str
    kind: str
    clips: tuple[Clip, ...]
    locked: bool = False


def _drain():
    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


class TimelineNative(unittest.TestCase):
    def setUp(self):
        self.assertTrue(HAVE_DISPLAY, "package timeline test requires a GTK display")
        from luma_appkit import Timeline
        self.assertEqual(Timeline.__name__, "Timeline")
        self.events = []
        self.clip = Clip("shot-1", "Opening", "video", 24, 48, transition_after="dissolve")
        self.lanes = (Lane("main", "main", (self.clip,)),)
        self.timeline = Timeline(lanes=self.lanes, duration=10, fps=24,
                                 position=1, selected=("shot-1",),
                                 callbacks={key: lambda *args, key=key: self.events.append((key, args))
                                            for key in ("seek", "skim", "select", "move", "trim", "split",
                                                        "transition_remove", "tool", "snapping", "zoom",
                                                        "gesture_begin", "gesture_commit", "gesture_cancel")})

    def test_real_clip_structure_callbacks_and_updates(self):
        clip_button = self.timeline._clip_buttons["shot-1"]
        self.assertEqual(clip_button.get_name(), "timeline-clip-shot-1")
        self.assertTrue(clip_button.has_css_class("selected"))
        clip_button.emit("clicked")
        self.assertIn(("select", (("shot-1",),)), self.events)
        self.timeline.set_tool("blade", emit=True)
        clip_button.emit("clicked")
        self.assertIn(("split", ("shot-1", 1.0)), self.events)
        self.timeline.set_position(3)
        self.assertEqual(self.timeline.position, 3)
        self.timeline.set_selected(())
        self.assertFalse(self.timeline._clip_buttons["shot-1"].has_css_class("selected"))
        self.timeline.set_export_status("Exporting", .42)
        self.assertEqual(self.timeline.info.get_text(), "Exporting")
        self.assertAlmostEqual(self.timeline.export_progress.get_fraction(), .42)
        self.timeline.set_zoom(1.25)
        self.assertEqual(self.timeline.info.get_text(), "Exporting")
        self.timeline.set_export_status(None)
        self.assertIn("10", self.timeline.info.get_text())
        self.assertFalse(self.timeline.export_progress.get_visible())
        self.timeline.set_content((Lane("audio", "audio", (Clip("music", "Music", "music", 0, 120),)),),
                                  duration=12, fps=24, markers=(48,), selected=("music",))
        self.assertEqual(set(self.timeline._clip_buttons), {"music"})
        self.assertTrue(self.timeline._clip_buttons["music"].has_css_class("selected"))
        self.timeline._on_skim(None, self.timeline._pps * 2, 0)
        self.assertIn(("skim", (2.0,)), self.events)

    def test_narrow_header_preserves_zoom_controls(self):
        import time
        from luma_appkit import install_appkit, install_lumaui
        install_appkit(); install_lumaui()
        window = Gtk.Window(default_width=360, default_height=400)
        self.timeline.set_margin_start(16); self.timeline.set_margin_end(16)
        self.timeline.set_export_status("Preparing selected clips for export")
        window.set_child(self.timeline); window.present()
        until = time.monotonic() + .3
        while time.monotonic() < until:
            _drain(); time.sleep(.005)
        self.assertEqual(window.get_width(), 360)
        self.assertEqual(self.timeline.info.get_text(), "Preparing selected clips for export")
        button = self.timeline.zoom_controls.get_last_child()
        ok, bounds = button.compute_bounds(window)
        self.assertTrue(ok)
        self.assertLessEqual(bounds.get_x() + bounds.get_width(), 344)
        before = self.timeline.zoom
        button.emit('clicked')
        self.assertGreater(self.timeline.zoom, before)
        window.destroy()

    def test_drag_emits_one_model_intent_with_frame_units(self):
        gesture = Gtk.GestureDrag()
        self.timeline._drag_begin(gesture, 5, self.clip, 1.0, 2.0, 40)
        self.timeline._drag_end(gesture, self.timeline._pps, self.clip)
        self.assertEqual([name for name, _args in self.events],
                         ["gesture_begin", "move", "gesture_commit"])
        self.assertEqual(self.events[1][1], ("shot-1", 2.0))
        self.events.clear()
        self.timeline._drag_begin(gesture, 38, self.clip, 1.0, 2.0, 40)
        self.timeline._drag_end(gesture, self.timeline._pps / 2, self.clip)
        self.assertEqual([name for name, _args in self.events],
                         ["gesture_begin", "trim", "gesture_commit"])
        self.assertEqual(self.events[1][1], ("shot-1", 2.5))
        self.events.clear()
        self.timeline._drag_begin(gesture, 5, self.clip, 1.0, 2.0, 40)
        self.timeline._drag_end(gesture, 1, self.clip)
        self.assertEqual([name for name, _args in self.events],
                         ["gesture_begin", "gesture_cancel"])

    def test_compact_widths_allocate_and_keep_actions(self):
        for width, height in ((360, 294), (500, 800), (980, 680), (1024, 600)):
            window = Gtk.Window()
            window.set_child(self.timeline)
            window.set_default_size(width, height)
            window.present()
            _drain()
            self.assertGreater(self.timeline.get_allocated_width(), 0)
            self.assertGreater(self.timeline.scroller.get_allocated_height(), 0)
            self.assertIsNotNone(self.timeline._clip_buttons.get("shot-1"))
            self.assertTrue(self.timeline._tool_buttons["blade"].get_visible())
            window.set_child(None)
            window.destroy()

    def test_rejects_invalid_frame_data(self):
        with self.assertRaisesRegex(ValueError, "duration_frames"):
            self.timeline.set_content((Lane("main", "main", (Clip("bad", "Bad", "video", 0, 0),)),))


if __name__ == "__main__":
    unittest.main()
