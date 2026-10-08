# SPDX-License-Identifier: Apache-2.0
"""LumaUI structure: the table header and the one selection look.

TableHeader — one header for every list view: a subtle recessed bar across
the columns. Each heading that sorts is a button: a click, Enter or Space
sorts by it, again reverses; the active column shows its arrow and says so
to assistive technology (the accessible SORT property, v70 `aria-sort`).
A column without a key is a plain label; `end=True` right-aligns a heading
(v70 `.d`). Arrows move between headings. Rows start 6 below it.

It serves both kinds of list:
- lists built from rows (a Gtk.ListBox of boxes, Tide's songs, Monitor's
  processes): `TableHeader(columns, ...)`, and `header.align(row_box)` for
  each row so its cells share the header's column widths (v70 `lTHead.grid`,
  the merged `.lthgrid`);
- a Gtk.ColumnView: `TableHeader.for_column_view(view)` gives its own header
  the same bar and arrows and keeps the accessible sort state in step with
  the view's sorter (v70 `lTHead` on a table).

    header = TableHeader([Column(None, "", width=32), Column("title", "Title", expand=True),
                          Column("album", "Album", expand=True), Column("time", "Time", end=True, width=56)],
                         sort=("title", "ascending"), on_sort=resort)
    for row in rows: header.align(row)

Selection — one selection look everywhere: the raised chip (v70 `.lsel`).
The steps of a path before the active one keep the same shape as a quieter
trail (v70 `.ltrail`). Apps never draw their own: `Selection.apply(list)`
gives a ListBox, ListView, GridView, FlowBox or ColumnView the look for its
selected rows; `Selection.mark(widget)` and `Selection.trail(widget)` are for
rows an app selects by hand. The sidebar's selected row wears the same chip.

Rules every part follows: docs/developer/kit/lumaui-principles.md and
behaviour.md. CSS lives in luma-appkit-base.css under `/* LumaUI: Table
header */` and `/* LumaUI: Selection */`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk, Pango  # noqa: E402

from . import icons, lumaui  # noqa: E402
from .structure_adapt import arrow_keys, children  # noqa: E402

__all__ = ["Column", "TableHeader", "Selection", "SORT_DIRECTIONS"]

#: The two directions, in the order a first click and a second click give them.
SORT_DIRECTIONS = ("ascending", "descending")
_SORTS = {"ascending": Gtk.AccessibleSort.ASCENDING, "descending": Gtk.AccessibleSort.DESCENDING,
          None: Gtk.AccessibleSort.NONE}


def _sort_value(direction: str | None) -> GObject.Value:
    """GTK reads the accessible sort from an int-holding value (PyGObject would pass an enum)."""
    return GObject.Value(GObject.TYPE_INT, int(_SORTS[direction]))


@dataclass(frozen=True)
class Column:
    """One column: its sort key (None: a plain label), heading, and how it takes room.

    `expand` columns share what is left; `width` fixes a narrow column (a
    track number, a time); `end` right-aligns the heading (and says the
    column's cells are right-aligned numbers).
    """

    key: str | None
    label: str = ""
    expand: bool = False
    width: int | None = None
    end: bool = False


def _column(spec: Column | tuple) -> Column:
    if isinstance(spec, Column):
        return spec
    key, label, *rest = spec
    return Column(key, label, end=bool(rest and rest[0] in ("d", "end")))


class TableHeader(Gtk.Box):
    """The recessed, sortable header bar for a list built from rows."""

    __gtype_name__ = "LumaUITableHeader"

    def __init__(self, columns: Sequence[Column | tuple], *, sort: tuple[str, str] | None = None,
                 on_sort: Callable[[str, str], None] | None = None, density: str = "regular") -> None:
        """`density="compact"`: the columns' widths are the whole story, no inset or gap between
        them (v70 Depot's .dtab .lthead, where each column has its own measured width)."""
        if density not in ("regular", "compact"):
            raise ValueError('a table header is "regular" or "compact"')
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, accessible_role=Gtk.AccessibleRole.ROW)
        self.add_css_class("lumaui-thead")
        self.density = density
        if density == "compact":
            self.add_css_class("compact")
        self.columns = tuple(_column(c) for c in columns)
        if not self.columns:
            raise ValueError("a table header has at least one column")
        keys = [c.key for c in self.columns if c.key]
        if len(keys) != len(set(keys)):
            raise ValueError("column keys are unique")
        self.on_sort = on_sort
        self._sort: tuple[str, str] | None = None
        self.cells: list[Gtk.Widget] = []
        self._groups: list[Gtk.SizeGroup] = []
        for column in self.columns:
            cell = self._cell(column)
            self.cells.append(cell)
            self.append(cell)
            # Every column shares one width across the header and its rows, the
            # expanding ones too: otherwise each row splits its spare room by its
            # own text and the columns wander from row to row.
            group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
            group.add_widget(cell)
            self._groups.append(group)
        arrow_keys(self)
        if sort is not None:
            self.set_sort(*sort)

    # ── public API ────────────────────────────────────────────────────────

    @property
    def sort(self) -> tuple[str, str] | None:
        """(key, "ascending" | "descending"), or None when unsorted."""
        return self._sort

    def set_sort(self, key: str | None, direction: str = "ascending") -> None:
        """Show the list as sorted by `key` (without asking the app to sort)."""
        if key is None:
            self._sort = None
        else:
            if key not in {c.key for c in self.columns if c.key}:
                raise ValueError(f"no sortable column {key!r}")
            if direction not in SORT_DIRECTIONS:
                raise ValueError(f"sort direction is one of {SORT_DIRECTIONS}")
            self._sort = (key, direction)
        for column, cell in zip(self.columns, self.cells):
            if not column.key:
                continue
            active = self._sort is not None and self._sort[0] == column.key
            state = self._sort[1] if active else None
            lumaui.set_css_class(cell, "active", active)
            cell.arrow.set_from_icon_name(icons.icon_name("arrow-up" if state == "ascending" else "arrow-down"))
            cell.arrow.set_visible(active)
            cell.update_property([Gtk.AccessibleProperty.SORT], [_sort_value(state)])

    def activate_column(self, key: str) -> tuple[str, str]:
        """A click on a heading: sort by it, or reverse it if it already sorts."""
        if self._sort is not None and self._sort[0] == key:
            direction = "descending" if self._sort[1] == "ascending" else "ascending"
        else:
            direction = "ascending"
        self.set_sort(key, direction)
        if self.on_sort is not None:
            self.on_sort(key, direction)
        return key, direction

    def align(self, row: Gtk.Widget) -> Gtk.Widget:
        """Give a row's cells (its children, one per column) the header's column widths."""
        cells = children(row)
        if len(cells) != len(self.columns):
            raise ValueError(f"a row aligned to this header has {len(self.columns)} cells, not {len(cells)}")
        row.add_css_class("lumaui-thead-row")
        if self.density == "compact":
            row.add_css_class("compact")
        for column, cell, group in zip(self.columns, cells, self._groups):
            cell.set_hexpand(column.expand)
            if column.width:
                cell.set_size_request(column.width, -1)
            group.add_widget(cell)
        return row

    @staticmethod
    def for_column_view(view: Gtk.ColumnView) -> "ColumnViewHeader":
        """Give a Gtk.ColumnView's own header the LumaUI bar, arrows and sort state."""
        return ColumnViewHeader(view)

    # ── internals ─────────────────────────────────────────────────────────

    def _cell(self, column: Column) -> Gtk.Widget:
        if not column.key:
            cell = Gtk.Label(label=column.label, xalign=1 if column.end else 0,
                             ellipsize=Pango.EllipsizeMode.END, accessible_role=Gtk.AccessibleRole.COLUMN_HEADER)
            cell.add_css_class("lumaui-thead-label")
        else:
            cell = Gtk.Button(accessible_role=Gtk.AccessibleRole.COLUMN_HEADER)
            cell.add_css_class("lumaui-thead-cell")
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           halign=Gtk.Align.END if column.end else Gtk.Align.START)
            line.add_css_class("lumaui-thead-line")
            text = Gtk.Label(label=column.label)  # a heading is a word or two: it never truncates
            line.append(text)
            cell.arrow = icons.image("arrow-up")
            cell.arrow.add_css_class("lumaui-thead-arrow")
            cell.arrow.set_visible(False)
            line.append(cell.arrow)
            cell.set_child(line)
            cell.update_property([Gtk.AccessibleProperty.LABEL], [column.label])
            cell.update_property([Gtk.AccessibleProperty.SORT], [_sort_value(None)])
            cell.connect("clicked", lambda _b, k=column.key: self.activate_column(k))
        if column.end:
            cell.add_css_class("end")
        cell.set_hexpand(column.expand)
        if column.width:
            cell.set_size_request(column.width, -1)
        return cell


class ColumnViewHeader:
    """A Gtk.ColumnView's header in the LumaUI look, its sort state spoken."""

    def __init__(self, view: Gtk.ColumnView) -> None:
        self.view = view
        view.add_css_class("lumaui-table")
        Selection.apply(view)
        sorter = view.get_sorter()
        if sorter is not None:
            sorter.connect("changed", lambda *_a: self.sync())
        view.connect("map", lambda *_a: self.sync())
        self.sync()

    def titles(self) -> list[Gtk.Widget]:
        """The column title widgets, in column order (GTK's `columnview > header > button`)."""
        header = self.view.get_first_child()
        while header is not None and header.get_css_name() != "header":
            header = header.get_next_sibling()
        return children(header) if header is not None else []

    def sync(self) -> tuple[str | None, str | None]:
        sorter = self.view.get_sorter()
        column = sorter.get_primary_sort_column() if sorter is not None else None
        order = sorter.get_primary_sort_order() if column is not None else None
        direction = None if column is None else ("ascending" if order == Gtk.SortType.ASCENDING else "descending")
        columns = self.view.get_columns()
        titles = self.titles()
        for index in range(columns.get_n_items()):
            if index >= len(titles):
                break
            active = columns.get_item(index) is column
            lumaui.set_css_class(titles[index], "active", active)
            titles[index].update_property([Gtk.AccessibleProperty.SORT], [_sort_value(direction if active else None)])
        key = column.get_id() if column is not None else None
        return key, direction


class Selection:
    """The one selection look: the raised chip; a path's earlier steps, a quieter trail."""

    @staticmethod
    def apply(widget: Gtk.Widget) -> Gtk.Widget:
        """Selected rows or children of this list, grid or column view wear the chip."""
        if not isinstance(widget, (Gtk.ListBox, Gtk.ListView, Gtk.GridView, Gtk.FlowBox, Gtk.ColumnView)):
            raise TypeError("Selection.apply takes a ListBox, ListView, GridView, FlowBox or ColumnView")
        widget.add_css_class("lumaui-selection")
        return widget

    @staticmethod
    def mark(widget: Gtk.Widget, selected: bool = True) -> None:
        """A row an app selects by hand (a tile, a column-view step) wears the chip."""
        lumaui.set_css_class(widget, "lumaui-selected", selected)
        if selected:
            lumaui.set_css_class(widget, "lumaui-trail", False)
        if isinstance(widget, Gtk.Accessible):
            widget.update_state([Gtk.AccessibleState.SELECTED], [GObject.Value(GObject.TYPE_INT, int(selected))])

    @staticmethod
    def trail(widget: Gtk.Widget, on: bool = True) -> None:
        """An earlier step of a path (column view): the same shape, quieter."""
        lumaui.set_css_class(widget, "lumaui-trail", on)
        if on:
            lumaui.set_css_class(widget, "lumaui-selected", False)
