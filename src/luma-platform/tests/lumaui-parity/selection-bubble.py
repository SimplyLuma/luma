# SPDX-License-Identifier: Apache-2.0
"""Parity: SelectionBubble (action_bubble.py), the bubble itself."""
from luma_appkit import action_bubble


def c_bubble(C, Gtk, active=False):
    items = []
    for icon, tooltip in (("bold", "Bold"), ("italic", "Italic"), (None, None), ("link", "Link")):
        if icon is None:
            items.append(C.BarItem.new_separator())
            continue
        item = C.BarItem.new_action(icon, None, None)
        item.set_tooltip(tooltip)
        items.append(item)
    bubble = C.SelectionBubble.new(Gtk.TextView(), items, None)
    if active:
        bubble.set_active("bold", True)
    return bubble


def py_bubble(K, Gtk, active=False):
    bubble = action_bubble.SelectionBubble(Gtk.TextView(), [
        K.BarAction("bold", tooltip="Bold"), K.BarAction("italic", tooltip="Italic"), K.SEPARATOR,
        K.BarAction("link", tooltip="Link"),
    ])
    if active:
        bubble.set_active("bold", True)
    return bubble


CASES = [
    ("marks", lambda C, Gtk: c_bubble(C, Gtk), lambda K, Gtk: py_bubble(K, Gtk)),
    ("active", lambda C, Gtk: c_bubble(C, Gtk, True), lambda K, Gtk: py_bubble(K, Gtk, True)),
]
