#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# A real disposable Wayland window for Meta.CloseDialog presentation checks.
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
app = Gtk.Application(application_id='org.projectluma.NativeCloseDialogFixture')
def activate(application):
    window = Gtk.ApplicationWindow(application=application, title='Native Close Dialog Fixture')
    window.set_default_size(800, 550)
    window.set_child(Gtk.Label(label='Disposable native close-dialog recipient'))
    window.present()
app.connect('activate', activate)
raise SystemExit(app.run(None))
