#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""A window that records every key it is given: typer.py APP_ID TITLE LOG.

Ground truth for the handover check: what the text box ends up holding is
what the person would have seen, and the key lines say when each key
arrived, so a key that never came and a key that came late are told apart.
"""
import sys, gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib

app_id, title, log = sys.argv[1], sys.argv[2], sys.argv[3]
f = open(log, 'w', buffering=1)


def note(kind, detail=''):
    f.write('%d %s %s\n' % (GLib.get_monotonic_time(), kind, detail))
    f.flush()


note('open', app_id)


app = Gtk.Application(application_id=app_id)


def activate(a):
    w = Gtk.ApplicationWindow(application=a, title=title)
    w.set_default_size(900, 600)
    entry = Gtk.Entry()
    entry.connect('changed', lambda e: note('text', e.get_text()))
    w.set_child(entry)
    keys = Gtk.EventControllerKey()
    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def pressed(_c, keyval, keycode, state):
        note('key', Gdk.keyval_name(keyval) or str(keyval))
        return False

    keys.connect('key-pressed', pressed)
    w.add_controller(keys)
    w.connect('notify::is-active', lambda win, _p: note('active', str(win.is_active())))
    w.connect('map', lambda _w: note('mapped'))
    w.present()
    entry.grab_focus()
    note('started')


app.connect('activate', activate)
app.run([])
