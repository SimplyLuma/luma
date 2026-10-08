# SPDX-License-Identifier: Apache-2.0
"""Parity: LumaSidebarToggle (C) and structure_sidebar.SidebarToggle (Python).

The case is the title row's toggle over a box holding the sidebar, so the
revealer the toggle puts the sidebar in is compared too."""


def _layout(Gtk, make_toggle, shown):
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    body = Gtk.Box()
    sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    sidebar.append(Gtk.Label(label="Inbox"))
    body.append(sidebar)
    body.append(Gtk.Label(label="content"))
    outer.append(make_toggle(sidebar, shown))
    outer.append(body)
    return outer


CASES = [
    ("shown", lambda C, Gtk: _layout(Gtk, lambda s, on: C.SidebarToggle.new(s, on), True),
     lambda K, Gtk: _layout(Gtk, lambda s, on: K.SidebarToggle(s, shown=on), True)),
    ("hidden", lambda C, Gtk: _layout(Gtk, lambda s, on: C.SidebarToggle.new(s, on), False),
     lambda K, Gtk: _layout(Gtk, lambda s, on: K.SidebarToggle(s, shown=on), False)),
]
