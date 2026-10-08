# SPDX-License-Identifier: Apache-2.0
"""Mapped Notes review surfaces under the private Notes desktop runner."""
import os
from pathlib import Path
import unittest
from unittest import mock

from gi.repository import Adw, Graphene, Gtk
import notes_lumaui_runtime as base
from prairie_apps.notes_lumaui import _descendants
from luma_appkit import ScrollView

if os.environ.get("NOTES_REVIEW_DARK") == "1":
    mock.patch("luma_appkit.widgets._prefers_dark", lambda: True).start()


def capture(widget, name):
    directory = Path(os.environ["NOTES_REVIEW_SHOTS"])
    directory.mkdir(parents=True, exist_ok=True)
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, widget.get_width(), widget.get_height())
    node = snapshot.to_node()
    assert node is not None
    texture = widget.get_native().get_renderer().render_texture(
        node, Graphene.Rect().init(0, 0, widget.get_width(), widget.get_height()))
    texture.save_to_png(str(directory / name))


class ReviewVisualTests(unittest.TestCase):
    setUpClass = classmethod(base.DocumentTests.setUpClass.__func__)
    tearDownClass = classmethod(base.DocumentTests.tearDownClass.__func__)
    setUp = base.DocumentTests.setUp
    tearDown = base.DocumentTests.tearDown

    def test_page_image_selection_and_picker(self):
        window = self.window
        if os.environ.get("NOTES_REVIEW_DARK") == "1":
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        base.pump(.3)
        capture(window.document_host, "notes-page.png")
        window._open_note(window.store.get_note("4"))
        base.pump(.2)
        self.assertTrue(window.page.pictures)
        capture(window.document_host, "notes-image.png")
        window._open_note(window.store.get_note("1"))
        window.editor.grab_focus()
        base.pump(.1)
        window.buffer.select_range(window.buffer.get_iter_at_offset(0),
                                   window.buffer.get_iter_at_offset(10))
        base.pump(.35)
        self.assertTrue(window.selection_bubble.get_mapped())
        self.assertTrue(window.selection_style_picker.widget.get_mapped())
        capture(window.document_host, "notes-selection.png")
        mark = next(widget for widget in _descendants(window)
                    if widget.get_name() == "nt-folder-mark-launch" and widget.get_mapped())
        mark.emit("clicked")
        base.pump(.15)
        picker = next(widget for widget in _descendants(window)
                      if widget.get_name() == "nt-mark-picker" and widget.get_mapped())
        picker.tabs["icon"].set_active(True)
        base.pump(.1)
        scrolls = [widget for widget in _descendants(picker) if isinstance(widget, ScrollView)]
        self.assertEqual(len(scrolls), 1)
        scroll = scrolls[0]
        capture(picker, "notes-picker.png")
        self.assertGreater(scroll.get_vadjustment().get_upper(),
                           scroll.get_vadjustment().get_page_size())
        scroll.get_vadjustment().set_value(scroll.get_vadjustment().get_upper())
        base.pump(.1)
        capture(picker, "notes-picker-scrolled.png")


if __name__ == "__main__":
    unittest.main()
