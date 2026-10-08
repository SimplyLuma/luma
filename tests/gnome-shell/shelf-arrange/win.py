#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""An ordinary GTK 4 window for work-area checks: win.py APP_ID TITLE."""
import sys, gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
app = Gtk.Application(application_id=sys.argv[1])
def activate(a):
    w = Gtk.ApplicationWindow(application=a, title=sys.argv[2])
    w.set_default_size(900, 600)
    w.set_child(Gtk.Label(label=sys.argv[2]))
    w.present()
app.connect('activate', activate)
app.run([])
