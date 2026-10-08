#!/usr/bin/env python3
"""Mapped GTK proof of the v70 selected tool key palette in both appearances."""
import os

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gio, GLib, Gtk

from luma_appkit import BarAction, install_appkit
from luma_appkit.action_center import make_control


def nodes(node):
    if node is not None:
        yield node
        for child in node.get_children():
            yield from nodes(child)


def rgb(color):
    return tuple(round(255 * channel) for channel in (color.red, color.green, color.blue))


def gradients(widget):
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(widget).snapshot(snapshot, widget.get_width(), widget.get_height())
    return [node for node in nodes(snapshot.to_node())
            if node.get_node_type().value_nick == "linear-gradient-node"]


theme = os.environ["TEST_THEME"]
assert theme in ("dark", "light")
Adw.init()
Gio.Settings.new("org.gnome.desktop.interface").set_string(
    "color-scheme", "prefer-dark" if theme == "dark" else "default")
install_appkit()

parent = Gtk.Box(spacing=8)
parent.add_css_class("lumaui-media")
selected = make_control(BarAction("pencil", tooltip="Pen", active=True), size="tool")
plain = make_control(BarAction("highlighter", tooltip="Highlighter"), size="tool")
ordinary = make_control(BarAction("info", tooltip="Information", active=True))
parent.append(selected)
parent.append(plain)
parent.append(ordinary)
normal = Gtk.Box(spacing=8)
normal_selected = make_control(BarAction("pencil", tooltip="Pen", active=True), size="tool")
normal.append(normal_selected)
root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
root.append(parent)
root.append(normal)
window = Gtk.Window(default_width=300, default_height=100, child=root)
window.add_css_class("luma-app-window")
window.present()
for _ in range(2):
    loop = GLib.MainLoop()
    GLib.timeout_add(180, lambda: (loop.quit(), False)[1])
    loop.run()

assert selected.get_mapped() and selected.has_css_class("tool") and selected.has_css_class("on")
assert selected.get_width() == 32 and selected.get_height() == 32, (selected.get_width(), selected.get_height())
normal_expected = ((231, 232, 234), (206, 207, 210), (21, 22, 25)) if theme == "dark" else (
    (51, 53, 58), (25, 27, 30), (255, 255, 255))
expected = ((237, 238, 240), (211, 212, 215), (21, 22, 25))
stops = [(stop.offset, rgb(stop.color)) for gradient in gradients(selected)
         for stop in gradient.get_color_stops()]
assert stops == [(0.0, expected[0]), (1.0, expected[1])], stops
assert rgb(selected.get_color()) == expected[2], rgb(selected.get_color())
normal_stops = [(stop.offset, rgb(stop.color)) for gradient in gradients(normal_selected)
                for stop in gradient.get_color_stops()]
assert normal_stops == [(0.0, normal_expected[0]), (1.0, normal_expected[1])], normal_stops
assert rgb(normal_selected.get_color()) == normal_expected[2]
assert not gradients(plain), "an unselected tool should remain unfilled"
assert not gradients(ordinary), "a non-tool on action retains its own treatment"
assert rgb(ordinary.get_color()) == (160, 198, 255), rgb(ordinary.get_color())
print(f"PASS {theme}: media selected 32px tool key gradient {stops}, ink {expected[2]}; "
      f"normal key {normal_stops}; plain and other on actions unchanged")
window.destroy()
