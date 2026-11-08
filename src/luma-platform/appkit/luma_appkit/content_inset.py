# SPDX-License-Identifier: Apache-2.0
"""LumaUI content: the inset, a recessed well that does the dividing.

A list of rows, a side card, a fold's contents or a facts strip sits in an
inset instead of under borders or hairlines (Depot 7 Nov, rule 5). The well
reads its colour, ring, shade and lip from the kit's `well` tokens, so light,
dark, high contrast and D-Ink all follow.

    inset = Inset()                        # 8 padding, 20 round; rows go straight in
    inset.append(row)
    Inset(padding="card")                  # 16 padding, 18 round: a side card

    two = TwoColumnInset()                 # two columns, a hairline down the middle
    for row in rows:
        two.append(row)                    # row by row: 1 2 / 3 4, a row as tall as its taller half
    two.set_columns(1)                     # one column (a phone); the hairline goes

Rows are not separated inside an inset; their own hover fill shows them.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Graphene, Gsk, Gtk  # noqa: E402

from . import lumaui  # noqa: E402

__all__ = ["Inset", "TwoColumnInset", "INSET_PADDINGS"]

#: "list": rows (Depot .dq-ranks, 8 round 20); "card": a side card (.dq-afacts, 16, 18);
#: "none": the content owns its padding.
INSET_PADDINGS = ("list", "card", "none")


class Inset(Gtk.Box):
    """A well. Children stack vertically; `spacing` is the gap between them (a list's rows are 4 apart)."""

    __gtype_name__ = "LumaUIInset"

    def __init__(self, *, padding: str = "list", spacing: int | None = None,
                 orientation: Gtk.Orientation = Gtk.Orientation.VERTICAL) -> None:
        if padding not in INSET_PADDINGS:
            raise ValueError(f"inset padding is {', '.join(INSET_PADDINGS)}, not {padding!r}")
        super().__init__(orientation=orientation)
        self.add_css_class("lumaui-inset")
        if padding != "list":
            self.add_css_class(padding)
        if spacing is None and padding == "list":
            from .lumaui_tokens import INSET
            spacing = INSET["row_gap"]  # rows 4 apart (.dq-ranks row-gap)
        if spacing is not None:
            self.set_spacing(spacing)


def _at(x: float, y: float) -> Gsk.Transform:
    return Gsk.Transform().translate(Graphene.Point().init(x, y))


class _Halves(Gtk.Widget):
    """Two columns of equal width, row by row. Draws the middle hairline."""

    __gtype_name__ = "LumaUITwoColumnHalves"

    def __init__(self, gap: int, row_gap: int, hair_inset: int) -> None:
        super().__init__()
        self.columns = 2
        self.gap, self.row_gap, self.hair_inset = gap, row_gap, hair_inset
        self.items: list[Gtk.Widget] = []
        self.hair = Gtk.Box()
        self.hair.add_css_class("lumaui-inset-hair")
        self.hair.set_parent(self)

    # row-major, as the CSS grid it ports (Depot ranks 1 2 / 3 4); a row is as tall as its taller half
    def _rows(self) -> list[list[Gtk.Widget]]:
        shown = [item for item in self.items if item.get_visible()]
        step = 2 if self.columns == 2 else 1
        return [shown[i:i + step] for i in range(0, len(shown), step)]

    def _two(self) -> bool:
        return self.columns == 2 and sum(item.get_visible() for item in self.items) > 1

    def add(self, child: Gtk.Widget) -> None:
        self.items.append(child)
        child.insert_before(self, self.hair)
        self.queue_resize()

    def remove_all(self) -> None:
        for item in self.items:
            item.unparent()
        self.items.clear()
        self.queue_resize()

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def _heights(self, width: int) -> list[int]:
        return [max(item.measure(Gtk.Orientation.VERTICAL, width)[1] for item in row) for row in self._rows()]

    def _column_width(self, width: int) -> int:
        return (width - self.gap) // 2 if self._two() else width

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        shown = [item for item in self.items if item.get_visible()]
        if orientation == Gtk.Orientation.HORIZONTAL:
            widths = [item.measure(orientation, -1) for item in shown] or [(0, 0, -1, -1)]
            minimum, natural = max(w[0] for w in widths), max(w[1] for w in widths)
            if self._two():
                minimum, natural = 2 * minimum + self.gap, 2 * natural + self.gap
            return minimum, natural, -1, -1
        heights = self._heights(self._column_width(for_size) if for_size > 0 else -1)
        height = sum(heights) + self.row_gap * max(0, len(heights) - 1)
        return height, height, -1, -1

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        column_width = self._column_width(width)
        y = 0
        for row, row_height in zip(self._rows(), self._heights(column_width)):
            for index, item in enumerate(row):
                item.allocate(column_width, row_height, -1, _at((column_width + self.gap) * index, y))
            y += row_height + self.row_gap
        two = self._two()
        self.hair.set_visible(two)
        if two:
            self.hair.allocate(1, max(0, height - 2 * self.hair_inset), -1,
                               _at(column_width + self.gap // 2, self.hair_inset))

    def do_dispose(self) -> None:
        for item in self.items:
            item.unparent()
        self.items.clear()
        if self.hair is not None:
            self.hair.unparent()
            self.hair = None
        Gtk.Widget.do_dispose(self)

    def do_snapshot(self, snapshot) -> None:
        for item in self.items:
            if item.get_visible():
                self.snapshot_child(item, snapshot)
        if self.hair is not None and self.hair.get_visible():
            self.snapshot_child(self.hair, snapshot)


class TwoColumnInset(Inset):
    """An inset whose rows run in two columns with a 1 px hairline down the middle.

    Depot `.dq-ranks.two`: 33 between the columns, 4 between rows, the hairline
    16 short of the well's top and bottom edges (8 padding + 8). `set_columns(1)`
    stacks them (a phone) and the hairline disappears.
    """

    __gtype_name__ = "LumaUITwoColumnInset"

    def __init__(self, *, columns: int = 2) -> None:
        from .lumaui_tokens import INSET
        super().__init__(padding="list")
        self.add_css_class("two")
        self.halves = _Halves(INSET["column_gap"], INSET["row_gap"], INSET["hair_inset"])
        self.halves.set_hexpand(True)
        super().append(self.halves)
        self.set_columns(columns)

    def append(self, child: Gtk.Widget) -> None:  # noqa: D401 - Gtk.Box API
        self.halves.add(child)

    def remove_all(self) -> None:
        self.halves.remove_all()

    def set_columns(self, columns: int) -> None:
        if columns not in (1, 2):
            raise ValueError("a two-column inset has one or two columns")
        self.halves.columns = columns
        lumaui.set_css_class(self, "single", columns == 1)
        self.halves.queue_resize()

    @property
    def rows(self) -> list[Gtk.Widget]:
        return list(self.halves.items)
