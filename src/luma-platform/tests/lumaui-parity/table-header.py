# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaTableHeader, LumaColumnViewHeader and luma_selection_* (C) and
structure_table.py (Python)."""

from gi.repository import Gio, Gtk as _Gtk


def c_header(C, Gtk, *, sort=None, rows=False):
    header = C.TableHeader.new()
    header.add_column(None, "", False, 32, False)
    header.add_column("title", "Title", True, -1, False)
    header.add_column("album", "Album", True, -1, False)
    header.add_column("time", "Time", False, 56, True)
    if sort is not None:
        header.set_sort(sort[0], C.SortDirection.ASCENDING if sort[1] == "ascending" else C.SortDirection.DESCENDING)
    return _with_rows(Gtk, header, header.align) if rows else header


def py_header(K, Gtk, *, sort=None, rows=False):
    header = K.TableHeader([K.Column(None, "", width=32), K.Column("title", "Title", expand=True),
                            K.Column("album", "Album", expand=True), K.Column("time", "Time", end=True, width=56)],
                           sort=sort)
    return _with_rows(Gtk, header, header.align) if rows else header


def _with_rows(Gtk, header, align):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(header)
    for n in range(2):
        row = Gtk.Box()
        for text in (str(n + 1), "Song", "Album", "3:12"):
            row.append(Gtk.Label(label=text))
        align(row)
        box.append(row)
    return box


def _column_view(Gtk, apply):
    store = Gio.ListStore(item_type=Gtk.StringObject)
    for text in ("b", "a"):
        store.append(Gtk.StringObject.new(text))
    view = Gtk.ColumnView()
    view.set_model(Gtk.NoSelection.new(Gtk.SortListModel.new(store, view.get_sorter())))
    for key in ("name", "size"):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, item: item.set_child(Gtk.Label()))
        column = Gtk.ColumnViewColumn.new(key.title(), factory)
        column.set_id(key)
        column.set_sorter(Gtk.StringSorter.new(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")))
        view.append_column(column)
    view.sort_by_column(view.get_columns().get_item(1), Gtk.SortType.DESCENDING)
    apply(view)
    return view


def c_for_column_view(C):
    return getattr(C.TableHeader, "for_column_view", None) or C.table_header_for_column_view


def _list(Gtk, apply, mark, trail):
    box = Gtk.ListBox()
    apply(box)
    rows = [Gtk.Label(label=t) for t in ("one", "two", "three")]
    for row in rows:
        box.append(row)
    mark(rows[0], True)
    trail(rows[1], True)
    return box


CASES = [
    ("header", lambda C, Gtk: c_header(C, Gtk), lambda K, Gtk: py_header(K, Gtk)),
    ("sorted", lambda C, Gtk: c_header(C, Gtk, sort=("album", "descending")),
     lambda K, Gtk: py_header(K, Gtk, sort=("album", "descending"))),
    ("rows", lambda C, Gtk: c_header(C, Gtk, sort=("title", "ascending"), rows=True),
     lambda K, Gtk: py_header(K, Gtk, sort=("title", "ascending"), rows=True)),
    ("column-view", lambda C, Gtk: _column_view(Gtk, c_for_column_view(C)),
     lambda K, Gtk: _column_view(Gtk, K.TableHeader.for_column_view)),
    ("selection", lambda C, Gtk: _list(Gtk, C.selection_apply, C.selection_mark, C.selection_trail),
     lambda K, Gtk: _list(Gtk, K.Selection.apply, K.Selection.mark, K.Selection.trail)),
]
