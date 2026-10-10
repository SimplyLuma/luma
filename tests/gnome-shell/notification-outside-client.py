#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real Wayland client for the disposable Shell notification-routing test."""
import os
from pathlib import Path
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, Gtk

app = Gtk.Application(application_id='org.example.NotificationOutsideProbe', flags=Gio.ApplicationFlags.NON_UNIQUE)
clicks = 0

def clicked(button):
    global clicks
    clicks += 1
    Path(os.environ['LUMA_NOTIFICATION_CLIENT_CLICKS']).write_text(str(clicks))

def activate(application):
    window = Gtk.ApplicationWindow(application=application, title='Notification outside-click probe')
    window.set_default_size(850, 520)
    button = Gtk.Button(label='Application content')
    button.connect('clicked', clicked)
    window.set_child(button)
    window.present()

app.connect('activate', activate)
app.run()
