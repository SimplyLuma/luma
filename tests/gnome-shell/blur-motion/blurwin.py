#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
# A translucent GTK 4 window that asks the compositor for a background blur
# through LumaUI.SurfaceBackdrop, like Luma applications in Frost and Glass.
# Usage: blurwin.py APP_ID TITLE WIDTH HEIGHT
import sys

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('LumaUI', '1')
from gi.repository import Gtk, LumaUI  # noqa: E402

app_id, title = sys.argv[1], sys.argv[2]
width, height = int(sys.argv[3]), int(sys.argv[4])

CSS = b"""
window.blur-motion { background: rgba(250, 250, 252, 0.55); }
window.blur-motion label { font-size: 28px; }
"""


def activate(app):
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS, len(CSS))
    Gtk.StyleContext.add_provider_for_display(
        Gtk.Widget.get_display(Gtk.Label()), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    win = Gtk.ApplicationWindow(application=app, title=title)
    win.add_css_class('blur-motion')
    win.set_default_size(width, height)
    win.set_titlebar(Gtk.HeaderBar())
    win.set_child(Gtk.Label(label=title))
    win.backdrop = LumaUI.SurfaceBackdrop.new(win)

    def report(*_):
        print(f'{app_id} backdrop available={win.backdrop.get_available()} '
              f'active={win.backdrop.get_active()}', flush=True)
    win.backdrop.connect('notify::active', report)
    win.present()
    report()


app = Gtk.Application(application_id=app_id)
app.connect('activate', activate)
app.run([])
