# SPDX-License-Identifier: Apache-2.0
"""The item page's two columns (8.1): the reading column takes what is left; the side is exactly 280."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Graphene, Gsk, Gtk  # noqa: E402

__all__ = ["Columns"]


class Columns(Gtk.Widget):
    __gtype_name__ = "DepotColumns"

    def __init__(self, main: Gtk.Widget, side: Gtk.Widget, *, side_width: int = 280, gap: int = 36) -> None:
        super().__init__(hexpand=True)
        self.add_css_class("dq-icols")
        self.main, self.side, self.side_width, self.gap = main, side, side_width, gap
        main.set_parent(self)
        side.set_parent(self)
        self._scroll = None
        self.connect("realize", lambda _w: self._watch())

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def _main_width(self, width: int) -> int:
        return max(0, width - self.side_width - self.gap)

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.HORIZONTAL:
            minimum = self.main.measure(orientation, -1)[0] + self.side_width + self.gap
            return minimum, max(minimum, 860), -1, -1
        main_width = self._main_width(for_size) if for_size > 0 else -1
        main = self.main.measure(orientation, main_width)
        side = self.side.measure(orientation, self.side_width)
        return max(main[0], side[0]), max(main[1], side[1]), -1, -1

    def _watch(self) -> None:
        scroller = self.get_ancestor(Gtk.ScrolledWindow)
        if scroller is not None and self._scroll is None:
            self._scroll = scroller.get_vadjustment()
            self._scroll.connect("value-changed", lambda _a: self.queue_allocate())

    def _sticky(self, height: int, side_height: int) -> float:
        """The side column stays 12 below the island's top while the reading column scrolls (8.1)."""
        if self._scroll is None:
            return 0.0
        viewport = self.get_ancestor(Gtk.Viewport)
        if viewport is None:
            return 0.0
        ok, point = self.compute_point(viewport, Graphene.Point().init(0, 0))
        if not ok:
            return 0.0
        offset = 12 - point.y
        return max(0.0, min(offset, height - side_height))

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        main_width = self._main_width(width)
        self.main.allocate(main_width, max(height, self.main.measure(Gtk.Orientation.VERTICAL, main_width)[1]), -1, None)
        side_height = self.side.measure(Gtk.Orientation.VERTICAL, self.side_width)[1]
        point = Graphene.Point().init(main_width + self.gap, self._sticky(height, side_height))
        self.side.allocate(self.side_width, side_height, -1, Gsk.Transform().translate(point))

    def do_snapshot(self, snapshot) -> None:
        self.snapshot_child(self.main, snapshot)
        self.snapshot_child(self.side, snapshot)

    def do_dispose(self) -> None:
        for child in (self.main, self.side):
            if child is not None and child.get_parent() is self:
                child.unparent()
        Gtk.Widget.do_dispose(self)
