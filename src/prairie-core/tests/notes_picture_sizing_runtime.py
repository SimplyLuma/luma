#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Notes: pictures never decide how big the window is.

A real Notes window opens under Xvfb on a page holding a wide screenshot and a
small picture, the way the app opens at login: the page is loaded before the
window is first shown. Every frame from the first one is recorded.

1. The window opens at its opening size and keeps it: no frame is wider than
   the first, and none is wider than the screen.
2. The editor asks for no more width than its margins, whatever the pictures.
3. A wide picture is exactly as wide as the text column, a small one keeps its
   own size (never enlarged), and both keep their shape.
4. The pictures are at their final size by the second frame at the latest, and
   stay there: no shrinking over many frames.

Set NOTES_SIZING_SHOTS to a directory to keep a picture of the window.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("GSK_RENDERER", "cairo")
os.environ.setdefault("PRAIRIE_EDS_MODE", "disabled")
_home = tempfile.mkdtemp(prefix="notes-sizing-")
for variable, leaf in (("HOME", ""), ("XDG_DATA_HOME", "data"), ("XDG_STATE_HOME", "state"),
                       ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache")):
    os.environ[variable] = os.path.join(_home, leaf)
    os.makedirs(os.environ[variable], exist_ok=True)

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Graphene, Gtk  # noqa: E402

from prairie_apps import notes as notes_module  # noqa: E402
from prairie_apps.notes_attachments import store_picture  # noqa: E402
from prairie_apps.notes_backend import NotesStore, notes_data_directory  # noqa: E402

mock.patch("luma_appkit.widgets._prefers_dark", lambda: Adw.StyleManager.get_default().get_dark()).start()
context = GLib.MainContext.default()

WIDE = (2560, 1440)
SMALL = (240, 160)


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def screenshot_png(width: int, height: int) -> bytes:
    """A made-up app screenshot: a sidebar, a header and a grid of covers."""
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.fill(0x1E2027FF)
    pixbuf.new_subpixbuf(0, 0, width // 6, height).fill(0x2A2D36FF)
    pixbuf.new_subpixbuf(0, 0, width, height // 14).fill(0x33363FFF)
    colours = (0xC0504DFF, 0x4F81BDFF, 0x9BBB59FF, 0x8064A2FF, 0xF79646FF, 0x4BACC6FF)
    tile = width // 9
    for row in range(3):
        for column in range(6):
            x = width // 6 + tile // 3 + column * (tile + tile // 4)
            y = height // 14 + tile // 3 + row * (tile + tile // 3)
            if x + tile < width and y + tile < height:
                pixbuf.new_subpixbuf(x, y, tile, tile).fill(colours[(row + column) % len(colours)])
    ok, data = pixbuf.save_to_bufferv("png", [], [])
    assert ok
    return bytes(data)


def small_png(width: int, height: int) -> bytes:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.fill(0x4F81BDFF)
    ok, data = pixbuf.save_to_bufferv("png", [], [])
    assert ok
    return bytes(data)


def seed_library() -> None:
    path = notes_data_directory() / "notes.sqlite3"
    store = NotesStore(path)
    wide = store_picture(screenshot_png(*WIDE))
    small = store_picture(small_png(*SMALL))
    note = store.create_note(title="Tide")
    parts = ["Tracks and songs need rounded corners", "￼",
             "Album view should never break this way; scaling should be smarter", "￼",
             "A small picture keeps its own size", "￼", "The end"]
    body = "\n".join(parts)
    runs = []
    pictures = iter((wide, wide, small))
    for index, char in enumerate(body):
        if char == "￼":
            runs.append({"start": index, "end": index + 1, "style": "image", "src": next(pictures)})
    store.update_note(note.id, title="Tide", body=body, runs=tuple(runs))
    store.close()


def render(widget: Gtk.Widget) -> Gdk.Texture | None:
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, widget.get_width(), widget.get_height())
    node = snapshot.to_node()
    if node is None:  # nothing drawn yet; the size checks below still run
        return None
    renderer = widget.get_native().get_renderer()
    return renderer.render_texture(node, Graphene.Rect().init(0, 0, widget.get_width(), widget.get_height()))


class PictureSizingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        seed_library()
        cls.app = notes_module.NotesApplication()
        cls.app.set_flags(Gio.ApplicationFlags.NON_UNIQUE | Gio.ApplicationFlags.HANDLES_OPEN)
        assert cls.app.register(None)
        notes_module.install_appkit()
        notes_module._install_notes_style()
        cls.frames: list[tuple[int, int, int, tuple[tuple[int, int], ...]]] = []
        cls.window = notes_module.NotesWindow(cls.app)

        def record(window, _clock) -> bool:
            page = window.page
            cls.frames.append((window.get_width(), window.get_height(), page.column_width(),
                               tuple((p.get_width(), p.get_height()) for p in page.pictures)))
            return GLib.SOURCE_CONTINUE

        cls.window.add_tick_callback(record)
        cls.window.present()
        pump(2.0)
        monitor = cls.window.get_display().get_monitors().get_item(0)
        cls.screen = monitor.get_geometry()
        if os.environ.get("NOTES_SIZING_DEBUG"):
            print("frames", len(cls.frames), cls.frames[:6], cls.frames[-1:], flush=True)
        shots = os.environ.get("NOTES_SIZING_SHOTS")
        if shots:
            os.makedirs(shots, exist_ok=True)
            texture = render(cls.window.get_content())
            if texture is not None:
                texture.save_to_png(os.path.join(
                    shots, f"notes-pictures-{cls.screen.width}x{cls.screen.height}.png"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        pump(0.1)

    def drawn(self):
        """Frames in which the window has a size and the page has pictures."""
        frames = [frame for frame in self.frames if frame[0] > 0 and len(frame[3]) == 3]
        self.assertGreater(len(frames), 5, f"too few frames recorded: {self.frames[:3]}")
        return frames

    def test_the_window_keeps_its_opening_size(self) -> None:
        frames = self.drawn()
        widths = [frame[0] for frame in frames]
        self.assertLessEqual(max(widths), self.screen.width,
                             f"the window grew wider than the {self.screen.width}px screen: {max(widths)}")
        self.assertEqual(set(widths), {widths[0]},
                         f"the window changed width after opening: {sorted(set(widths))[:8]}…")

    def test_the_editor_asks_for_no_width_of_its_own(self) -> None:
        editor = self.window.editor
        minimum, _natural, _b1, _b2 = editor.measure(Gtk.Orientation.HORIZONTAL, -1)
        margins = editor.get_left_margin() + editor.get_right_margin()
        self.assertLessEqual(minimum, margins + 8,
                             f"the editor's minimum width {minimum} follows its pictures")
        column = self.window.page.column_width()
        for picture in self.window.page.pictures:
            smallest, _n, _a, _b = picture.measure(Gtk.Orientation.HORIZONTAL, -1)
            self.assertLessEqual(smallest, column, "a picture asks for more than the text column")

    def test_pictures_fit_the_column_keep_their_shape_and_are_not_enlarged(self) -> None:
        column = self.window.page.column_width()
        self.assertGreater(column, 200)
        sizes = [(p.get_width(), p.get_height()) for p in self.window.page.pictures]
        for (width, height), natural in zip(sizes, (WIDE, WIDE, SMALL)):
            self.assertLessEqual(width, column, f"a picture is wider than the text: {width} > {column}")
            self.assertAlmostEqual(height, width * natural[1] / natural[0], delta=1.5,
                                   msg=f"picture {width}x{height} lost its {natural} shape")
        self.assertEqual(sizes[0][0], min(column, WIDE[0]), "a wide picture fills the text width")
        self.assertEqual(sizes[2], SMALL, "a small picture is shown at its own size")

    def test_pictures_settle_at_once(self) -> None:
        frames = self.drawn()
        final = frames[-1][3]
        self.assertTrue(all(width > 0 for width, _height in final), f"pictures were never laid out: {final}")
        settled = next(index for index, frame in enumerate(frames) if frame[3] == final)
        self.assertLessEqual(settled, 1, f"pictures took {settled} frames to settle: "
                             f"{[frame[3][0][0] for frame in frames[:settled + 1]][:12]}")
        self.assertTrue(all(frame[3] == final for frame in frames[settled:]),
                        "pictures changed size again after settling")


if __name__ == "__main__":
    unittest.main(verbosity=2)
