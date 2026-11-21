# SPDX-License-Identifier: Apache-2.0
"""Text that changes many times a second (a level in dB) without relayouting the window.

A Gtk.Label queues a resize on every new text, and a resize walks every ancestor up to the window. A
Readout sizes itself once, from the widest text it will show, and a new text only redraws it.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Graphene, Gtk, Pango  # noqa: E402

from luma_appkit.content_type import apply_type  # noqa: E402


class Readout(Gtk.Widget):
    __gtype_name__ = "StreamyReadout"

    def __init__(self, text: str, *, widest: tuple[str, ...], role: str = "caption") -> None:
        super().__init__(accessible_role=Gtk.AccessibleRole.LABEL)
        self._text = text
        self._widest = widest
        apply_type(self, role)

    def get_label(self) -> str:
        return self._text

    def set_label(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        self.queue_draw()

    def do_css_changed(self, change) -> None:  # a new font or size: measure again
        Gtk.Widget.do_css_changed(self, change)
        self.queue_resize()

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation, _for_size):
        sizes = [self.create_pango_layout(t) for t in (self._text, *self._widest)]
        if orientation == Gtk.Orientation.HORIZONTAL:
            width = max(layout.get_pixel_size()[0] for layout in sizes)
            return width, width, -1, -1
        layout = sizes[0]
        height = layout.get_pixel_size()[1]
        baseline = layout.get_baseline() // Pango.SCALE
        return height, height, baseline, baseline

    def do_snapshot(self, snapshot) -> None:
        layout = self.create_pango_layout(self._text)
        width = layout.get_pixel_size()[0]
        snapshot.save()
        snapshot.translate(Graphene.Point().init(max(0, self.get_width() - width), 0))  # numbers end flush
        snapshot.append_layout(layout, self.get_color())
        snapshot.restore()
