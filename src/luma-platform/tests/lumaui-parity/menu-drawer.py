# SPDX-License-Identifier: Apache-2.0
"""Parity: MenuDrawer (structure_drawer.py) and FloatingMenu (action_bubble.py).

A drawer only exists presented, so each side presents one over a throwaway
window and takes the card back out of its layer host to compare.
"""
from gi.repository import Gio, Gtk

from luma_appkit import action_bubble


def model():
    menu = Gio.Menu()
    sort = Gio.Menu()
    sort.append("By _Name", "win.sort::name")
    sort.append("By Date", "win.sort::date")
    menu.append_section("Sort", sort)
    more = Gio.Menu()
    more.append("Rename…", "win.rename")
    sub = Gio.Menu()
    sub.append("Everyone", "win.share::all")
    more.append_submenu("Share", sub)
    menu.append_section(None, more)
    return menu


def taken(card):
    card.get_parent().remove_overlay(card)
    return card


def anchor():
    window = Gtk.Window()
    button = Gtk.Button(label="More")
    window.set_child(button)
    return button


def c_menu(C):
    menu = C.FloatingMenu.new("Open in")
    menu.add_heading("Apps")
    menu.add_item("Text Editor", "file-text", None, "Default", "win.open::text", True)
    menu.add_item("Files", "folder", None, None, "win.open::files", False)
    menu.add_separator()
    menu.add_item("Other…", None, None, None, None, False)
    return menu


def py_menu():
    Item = action_bubble.MenuItem
    return action_bubble.FloatingMenu([
        "Apps",
        Item("Text Editor", icon="file-text", note="Default", selected=True),
        Item("Files", icon="folder"),
        None,
        Item("Other…"),
    ], label="Open in")


CASES = [
    ("model", lambda C, Gtk: taken(C.MenuDrawer.present_model(anchor(), model(), "View")),
     lambda K, Gtk: taken(K.MenuDrawer.present_model(anchor(), model(), title="View"))),
    ("model-untitled", lambda C, Gtk: taken(C.MenuDrawer.present_model(anchor(), model(), None)),
     lambda K, Gtk: taken(K.MenuDrawer.present_model(anchor(), model()))),
    ("floating-menu", lambda C, Gtk: c_menu(C), lambda K, Gtk: py_menu()),
]
