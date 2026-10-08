# SPDX-License-Identifier: Apache-2.0
"""Open the host's supported, fixed GNOME display panel action."""
from gi.repository import Gio, GLib

def open_display_settings(failed):
    def connected(_source, result):
        try:
            connection = Gio.bus_get_finish(result)
        except GLib.Error:
            failed(); return
        def completed(source, reply):
            try: source.call_finish(reply)
            except GLib.Error: failed()
        connection.call('org.gnome.Settings', '/org/gnome/Settings',
            'org.gtk.Actions', 'Activate',
            GLib.Variant('(sava{sv})', ('launch-panel',
                [GLib.Variant('(sav)', ('display', []))], {})),
            GLib.VariantType.new('()'), Gio.DBusCallFlags.NONE, 5000,
            None, completed)
    Gio.bus_get(Gio.BusType.SESSION, None, connected)
