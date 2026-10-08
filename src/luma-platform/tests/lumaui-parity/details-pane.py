# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaDetailsPane and its rows (C) and structure_details.py (Python)."""

from gi.repository import Gdk, Gio, GObject


def _noop(*_args):
    return None


def _paintables(n):
    return [Gdk.Paintable.new_empty(40, 40) for _ in range(n)]


def _store(items):
    store = Gio.ListStore(item_type=GObject.Object)
    for item in items:
        store.append(item)
    return store


def c_row(C, Gtk, actions=True):
    row = C.DetailsRow.new("Priya Raman", "@priya", Gtk.Label(label="PR"))
    if actions:
        row.add_action("phone", "Call", "win.call")
        row.add_action("message-circle", "Message", "win.message")
    return row


def py_row(K, Gtk, actions=True):
    acts = [("phone", "Call", _noop), ("message-circle", "Message", _noop)] if actions else []
    return K.DetailsRow("Priya Raman", "@priya", lead=Gtk.Label(label="PR"), actions=acts)


def c_item(C, Gtk):
    item = C.DetailsItem.new("Standup", "Every weekday", "calendar")
    item.set_trail(Gtk.Label(label="9:30"))
    item.set_selected(True)
    return item


def py_item(K, Gtk):
    return K.DetailsItem("Standup", "Every weekday", icon="calendar", trail=Gtk.Label(label="9:30"), selected=True)


def c_pane(C, Gtk, *, shown=False, main=False):
    pane = C.DetailsPane.new("Details", main)
    pane.add_hero("Launch crew", "4 people", Gtk.Label(label="LC"))
    pane.add_section("Conversation", None, None)
    pane.add_fact("Created", "Sep 2")
    pane.add_fact("Encryption", "End-to-end")
    pane.add_section("People", "View all", "win.people")
    pane.add_row(c_row(C, Gtk))
    pane.add_row(C.FactRow.new("phone", "mobile", "+44 7700 900123", True))
    pane.add_row(C.AddRow.new("Add people", None, "<Control>n"))
    pane.add_section("Photos", None, None)
    pane.add_photos(_store(_paintables(4)))
    if shown:
        pane.set_subject(GObject.Object())
        pane.show(True)
    return pane


def py_pane(K, Gtk, *, shown=False, main=False):
    pane = K.DetailsPane("Details", main=main)
    pane.add_hero("Launch crew", "4 people", lead=Gtk.Label(label="LC"))
    pane.add_section("Conversation")
    pane.add_facts([("Created", "Sep 2"), ("Encryption", "End-to-end")])
    pane.add_section("People", action=("View all", _noop))
    pane.add_list([py_row(K, Gtk), K.FactRow("phone", "mobile", "+44 7700 900123"),
                   K.AddRow("Add people", shortcut="Ctrl+N")])
    pane.add_section("Photos")
    pane.add_photos(_paintables(4))
    if shown:
        pane.show(open=True, subject=object())
    return pane


CASES = [
    ("row", lambda C, Gtk: c_row(C, Gtk), lambda K, Gtk: py_row(K, Gtk)),
    ("row-no-actions", lambda C, Gtk: c_row(C, Gtk, False), lambda K, Gtk: py_row(K, Gtk, False)),
    ("item", lambda C, Gtk: c_item(C, Gtk), lambda K, Gtk: py_item(K, Gtk)),
    ("item-plain", lambda C, Gtk: C.DetailsItem.new("Thread", None, None),
     lambda K, Gtk: K.DetailsItem("Thread")),
    ("add-row", lambda C, Gtk: C.AddRow.new("Add source", "plus", None),
     lambda K, Gtk: K.AddRow("Add source", icon="plus")),
    ("add-row-shortcut", lambda C, Gtk: C.AddRow.new("Add people", None, "<Control>n"),
     lambda K, Gtk: K.AddRow("Add people", shortcut="Ctrl+N")),
    ("fact-row", lambda C, Gtk: C.FactRow.new("mail", "work", "priya@example.com", True),
     lambda K, Gtk: K.FactRow("mail", "work", "priya@example.com")),
    ("fact-row-no-copy", lambda C, Gtk: C.FactRow.new("link", "site", "example.com", False),
     lambda K, Gtk: K.FactRow("link", "site", "example.com", copy=False)),
    ("photos", lambda C, Gtk: C.DetailsPhotos.new(_store(_paintables(5))),
     lambda K, Gtk: K.DetailsPhotos(_paintables(5))),
    ("pane-closed", lambda C, Gtk: c_pane(C, Gtk), lambda K, Gtk: py_pane(K, Gtk)),
    ("pane-shown", lambda C, Gtk: c_pane(C, Gtk, shown=True), lambda K, Gtk: py_pane(K, Gtk, shown=True)),
    ("pane-main", lambda C, Gtk: c_pane(C, Gtk, main=True), lambda K, Gtk: py_pane(K, Gtk, main=True)),
]
