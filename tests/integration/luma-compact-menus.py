#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Measure installed native menu surfaces, including RTL and long accelerators.

Run under a disposable display/session with LUMA_TOOLKIT_MENU=1. Does not
change user settings or execute any file operation.
"""
import os
import gi
assert os.environ.get('LUMA_TOOLKIT_MENU') == '1', 'Requires the candidate toolkit'
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk
Adw.init()
def descendants(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from descendants(child)
        child = child.get_next_sibling()
def settle():
    loop = GLib.MainLoop()
    GLib.timeout_add(300, lambda: (loop.quit(), False)[1])
    loop.run()
window = Gtk.Window(default_width=500, default_height=400)
anchor = Gtk.MenuButton(label='Menu')
window.set_child(anchor)
window.present()
settle()
style = Adw.StyleManager.get_default()
count = 0
for dark in (False, True):
    style.set_color_scheme(Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
    for rtl in (False, True):
        window.set_direction(Gtk.TextDirection.RTL if rtl else Gtk.TextDirection.LTR)
        for variant, expected in (('app', 220), ('desktop', 220), ('dock', 186), ('submenu', 220)):
            for long in (False, True):
                model = Gio.Menu()
                model.append('A deliberately long translated command label' if long else 'Open', 'test.open')
                model.append('Copy', 'test.copy')
                actions = Gio.SimpleActionGroup()
                for name in ('open', 'copy'):
                    actions.add_action(Gio.SimpleAction.new(name, None))
                window.insert_action_group('test', actions)
                menu = Gtk.PopoverMenu.new_from_model(model)
                menu.add_css_class('luma-menu-' + variant)
                anchor.set_popover(menu)
                menu.popup()
                settle()
                contents = next(w for w in descendants(menu) if w.get_css_name() == 'contents')
                width = contents.get_allocated_width()
                assert width >= expected, (dark, rtl, variant, long, width)
                if not long:
                    assert width == expected, (dark, rtl, variant, width, expected)
                else:
                    assert width > expected, 'Long native labels must have room to grow'
                print('PASS', 'dark' if dark else 'light', 'rtl' if rtl else 'ltr', variant, 'long' if long else 'short', width, flush=True)
                menu.popdown()
                anchor.set_popover(None)
                count += 1
window.destroy()
print('PASS native compact menus', count)
