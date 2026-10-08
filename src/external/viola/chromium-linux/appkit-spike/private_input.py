# SPDX-License-Identifier: GPL-3.0-only
"""Owned private-compositor input only; never the user's desktop bus."""
import os
from gi.repository import Gio, GLib


class PrivateInput:
    def __init__(self):
        if (not os.environ.get('WAYLAND_DISPLAY', '').startswith('viola-qa-')
                or not os.environ.get('DBUS_SESSION_BUS_ADDRESS', '').startswith('unix:path=/tmp/viola-qa-bus-')):
            raise RuntimeError('Input qualification requires the owned private QA compositor')
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.service = 'org.gnome.Mutter.RemoteDesktop'
        self.session = self.call('/org/gnome/Mutter/RemoteDesktop', self.service, 'CreateSession')[0]

    def call(self, path, interface, method, parameters=None):
        result = self.connection.call_sync(self.service, path, interface, method, parameters,
            None, Gio.DBusCallFlags.NONE, 5000, None)
        return result.unpack() if result else None

    def interface(self):
        return self.call(self.session, 'org.freedesktop.DBus.Introspectable', 'Introspect')[0]

    def start(self):
        self.call(self.session, self.service + '.Session', 'Start')
        # Establish the virtual keyboard/keymap before the fixture's first
        # actionable key; a neutral modifier press/release has no command.
        self.key(0xffe1)

    def move(self, x, y):
        def relative(dx, dy):
            self.call(self.session, self.service + '.Session', 'NotifyPointerMotionRelative',
                      GLib.Variant('(dd)', (float(dx), float(dy))))
        relative(-10000, -10000)
        # Let Mutter process and clamp the first motion before the second;
        # otherwise coalesced relative motion cannot establish an origin.
        GLib.timeout_add(25, lambda: relative(x, y) or False)

    def click(self, button=272):
        for pressed in (True, False):
            self.call(self.session, self.service + '.Session', 'NotifyPointerButton',
                      GLib.Variant('(ib)', (button, pressed)))

    def key(self, keysym):
        def send(pressed):
            self.call(self.session, self.service + '.Session', 'NotifyKeyboardKeycode' if keysym == 0xff1b else 'NotifyKeyboardKeysym',
                      GLib.Variant('(ub)', (1 if keysym == 0xff1b else keysym, pressed)))
        send(True)
        GLib.timeout_add(60, lambda: send(False) or False)

    def chord(self, modifier, key):
        def modifier_state(pressed):
            keys = modifier if isinstance(modifier, tuple) else (modifier,)
            for value in keys if pressed else reversed(keys):
                self.call(self.session, self.service + '.Session', 'NotifyKeyboardKeysym',
                          GLib.Variant('(ub)', (value, pressed)))
        modifier_state(True)
        GLib.timeout_add(50, lambda: self.key(key) or False)
        GLib.timeout_add(150, lambda: modifier_state(False) or False)

    def close(self):
        self.call(self.session, self.service + '.Session', 'Stop')


if __name__ == '__main__':
    device = PrivateInput()
    try:
        print(device.interface())
    finally:
        device.close()
