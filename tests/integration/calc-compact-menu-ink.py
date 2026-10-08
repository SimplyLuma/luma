#!/usr/bin/env python3
"""Mapped GTK proof for Calculator compact menu disabled ink and selected check."""
import os
import gi

gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw, Gio, GLib, Gtk
from luma_appkit import Command, CommandGroup, CommandRegistry, Menu, install_appkit
from luma_appkit.window_frame import WindowIdentity


def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from descendants(child)
        child = child.get_next_sibling()


def nodes(node):
    if node is not None:
        yield node
        for child in node.get_children():
            yield from nodes(child)


def rgb(widget):
    color = widget.get_color()
    return tuple(round(255 * channel) for channel in (color.red, color.green, color.blue))


theme = os.environ.get('TEST_THEME')
assert theme in ('dark', 'light'), 'set TEST_THEME=dark or TEST_THEME=light'
Adw.init()
Gio.Settings.new('org.gnome.desktop.interface').set_string(
    'color-scheme', 'prefer-dark' if theme == 'dark' else 'default')
install_appkit()
registry = CommandRegistry((CommandGroup(None, (
    Command('calc.basic', 'Basic', lambda: None, checked=lambda: True),
    Command('calc.scientific', 'Scientific', lambda: None, checked=lambda: False),
)), CommandGroup(None, (
    Command('calc.copy', 'Copy result', lambda: None, enabled=lambda: False),
    Command('calc.clear', 'Clear tape', lambda: None, enabled=lambda: False),
))))
window = Gtk.Window(title='Calculator', default_width=390, default_height=820)
title = Gtk.Box()
title.set_margin_start(8)
title.set_margin_top(7)
window.set_child(title)
menu = Menu(registry, variant='compact', keep_parent=True, phone_placement='popover')
identity = WindowIdentity(window, menu)
title.append(identity)
window.present()
for _ in range(2):
    loop = GLib.MainLoop()
    GLib.timeout_add(180, lambda: (loop.quit(), False)[1])
    loop.run()
identity.set_active(True)
for _ in range(2):
    loop = GLib.MainLoop()
    GLib.timeout_add(180, lambda: (loop.quit(), False)[1])
    loop.run()
assert menu.get_mapped()
rows = [w for w in descendants(menu) if w.has_css_class('luma-menu-row')]
marks = [w for w in descendants(menu) if w.has_css_class('luma-menu-check')]
labels = {w.get_label(): w for w in descendants(menu) if isinstance(w, Gtk.Label)}
assert len(rows) == 4 and len(marks) == 2
assert all(mark.get_pixel_size() == 16 for mark in marks)
assert marks[0].get_visible() and not marks[1].get_visible()
ink = (242, 243, 245) if theme == 'dark' else (20, 22, 26)
disabled = (123, 128, 132) if theme == 'dark' else (139, 144, 148)
assert rgb(marks[0]) == ink, (rgb(marks[0]), ink)
for index, label in ((2, 'Copy result'), (3, 'Clear tape')):
    row = rows[index]
    assert not row.is_sensitive()
    assert rgb(labels[label]) == disabled, (label, rgb(labels[label]), disabled)
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(row).snapshot(snapshot, row.get_width(), row.get_height())
    assert not any(node.get_node_type().value_nick == 'opacity-node' and node.get_opacity() < 0.99
                   for node in nodes(snapshot.to_node())), label

# Full menus retain the existing 14px check; the compact styling has no effect.
ordinary = Menu(registry, variant='app')
ordinary_marks = [w for w in descendants(ordinary) if w.has_css_class('luma-menu-check')]
assert len(ordinary_marks) == 2 and all(mark.get_pixel_size() == 14 for mark in ordinary_marks)
menu.popdown()
window.destroy()
print(f'PASS {theme} compact disabled ink and 16px check; full menu remains 14px')
