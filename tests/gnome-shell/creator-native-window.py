# SPDX-License-Identifier: GPL-2.0-or-later
"""Real Wayland window for the disposable packaged-Shell geometry fixture."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, Gio
from luma_appkit import AppWindow, CommandRegistry
app = Adw.Application(application_id='org.example.CreatorPin.Integration',
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
def activated(application):
    window = AppWindow(application=application,
                       app_id='org.example.CreatorPin.Integration',
                       title='Creator native pin fixture',
                       icon_name='org.gnome.Contacts',
                       commands=CommandRegistry(()),
                       default_width=540, default_height=380)
    window.set_body(Gtk.Label(label='Actual LumaUI/Wayland geometry fixture'))
    window.present()
app.connect('activate', activated)
app.run([])
