# SPDX-License-Identifier: Apache-2.0
"""An even grid whose column count follows its width: category tiles 5 (2 on a phone), Look tiles 4,
Tweak cards 2 (1 on a phone), Library tiles about 7. Row-major; a row is as tall as its tallest cell."""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk  # noqa: E402

__all__ = ["Grid"]


class Grid(Gtk.Widget):
    __gtype_name__ = "DepotGrid"

    def __init__(self, columns: Callable[[int], int], *, column_gap: int = 12, row_gap: int = 12,
                 css: str = "") -> None:
        super().__init__(hexpand=True)
        for name in css.split():
            self.add_css_class(name)
        self.columns_for, self.column_gap, self.row_gap = columns, column_gap, row_gap
        self.cells: list[Gtk.Widget] = []
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def append(self, child: Gtk.Widget) -> None:
        self.cells.append(child)
        child.set_parent(self)

    def _layout(self, width: int) -> tuple[int, float]:
        """Columns and their exact width: CSS grid's 1fr tracks, fractional, so the last ends at the edge."""
        columns = max(1, self.columns_for(width))
        return columns, max(1.0, (width - self.column_gap * (columns - 1)) / columns)

    def _rows(self, columns: int) -> list[list[Gtk.Widget]]:
        shown = [c for c in self.cells if c.get_visible()]
        return [shown[i:i + columns] for i in range(0, len(shown), columns)]

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.HORIZONTAL:
            return 0, 600, -1, -1
        width = for_size if for_size > 0 else 600
        columns, cell = self._layout(width)
        heights = [max(c.measure(Gtk.Orientation.VERTICAL, round(cell))[1] for c in row) for row in self._rows(columns)]
        height = sum(heights) + self.row_gap * max(0, len(heights) - 1)
        return height, height, -1, -1

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        columns, cell = self._layout(width)
        y = 0
        for row in self._rows(columns):
            row_height = max(c.measure(Gtk.Orientation.VERTICAL, round(cell))[1] for c in row)
            for index, child in enumerate(row):
                point = Graphene.Point().init(index * (cell + self.column_gap), y)
                child.allocate(round(cell), row_height, -1, Gsk.Transform().translate(point))
            y += row_height + self.row_gap

    def do_snapshot(self, snapshot) -> None:
        for child in self.cells:
            if child.get_visible():
                self.snapshot_child(child, snapshot)

    def _key(self, _controller, keyval: int, _code: int, _state) -> bool:
        """Arrow keys move between cells (16.4)."""
        moves = {Gdk.KEY_Left: -1, Gdk.KEY_Right: 1}
        columns, _cell = self._layout(self.get_width() or 600)
        moves.update({Gdk.KEY_Up: -columns, Gdk.KEY_Down: columns})
        if keyval not in moves:
            return False
        focus = self.get_root().get_focus() if self.get_root() else None
        current = next((i for i, c in enumerate(self.cells) if focus is not None and (focus is c or focus.is_ancestor(c))), None)
        if current is None:
            return False
        target = current + moves[keyval]
        if 0 <= target < len(self.cells):
            self.cells[target].grab_focus()
            return True
        return False

    def do_dispose(self) -> None:
        for child in self.cells:
            if child.get_parent() is self:
                child.unparent()
        self.cells = []
        Gtk.Widget.do_dispose(self)
