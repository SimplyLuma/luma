# SPDX-License-Identifier: GPL-3.0-only
"""Real private D-Bus lifetime/refusal; positive admission is a unit stand-in.

Installed signed engine/compositor and frames require separate qualification.
"""
import json
from pathlib import Path
import threading
import time
import unittest
from gi.repository import Gio, GLib
from viola_host import Service, BUS, PATH


class Display:
    socket_path = Path('/fixture/private-display')
    private_bus_address = 'unix:path=/fixture/private-bus'
    def __init__(self): self.closed = False
    def close(self): self.closed = True


class Boundary(unittest.TestCase):
    def start(self, admission=None):
        self.displays = []
        def factory():
            display = Display(); self.displays.append(display); return display
        self.server = self.connection()
        self.service = Service(self.server, factory, **({'admission': admission} if admission else {}))
        self.owner = Gio.bus_own_name_on_connection(self.server, BUS, Gio.BusNameOwnerFlags.NONE, None, None)
        self.loop = self.service.loop
        self.thread = threading.Thread(target=self.loop.run); self.thread.start()
        self.client = self.connection()
        self.addCleanup(self.stop)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.client.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus',
                    'org.freedesktop.DBus','NameHasOwner',GLib.Variant('(s)',(BUS,)),
                    None,Gio.DBusCallFlags.NONE,1000,None).unpack()[0]: return
            time.sleep(.01)
        self.fail('Actual private host bus name did not register')

    def connection(self):
        return Gio.DBusConnection.new_for_address_sync(__import__('os').environ['DBUS_SESSION_BUS_ADDRESS'],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)

    def call(self, method, params=None, connection=None):
        return (connection or self.client).call_sync(BUS,PATH,BUS,method,params,None,Gio.DBusCallFlags.NONE,3000,None)

    def stop(self):
        if not self.client.is_closed():self.client.close_sync(None)
        GLib.idle_add(self.loop.quit); self.thread.join(3)
        self.service.close(); Gio.bus_unown_name(self.owner); self.server.close_sync(None)
        self.assertFalse(self.thread.is_alive())

    def test_real_unsigned_native_actor_is_refused_before_factory(self):
        self.start()
        with self.assertRaises(GLib.Error) as rejected:self.call('Acquire')
        self.assertIn('Refused',str(rejected.exception)); self.assertEqual(self.displays,[])

    def test_other_app_admission_never_creates_display(self):
        self.start(lambda *args:'org.projectluma.Notes')
        with self.assertRaises(GLib.Error):self.call('Acquire')
        self.assertEqual(self.displays,[])

    def test_sender_owner_and_release_with_actual_wire(self):
        self.start(lambda *args:'com.rhyme.viola')
        record=json.loads(self.call('Acquire').unpack()[0]); other=self.connection()
        try:
            with self.assertRaises(GLib.Error):self.call('Release',GLib.Variant('(s)',(record['handle'],)),other)
        finally:other.close_sync(None)
        self.assertFalse(self.displays[0].closed)
        self.call('Release',GLib.Variant('(s)',(record['handle'],))); self.assertTrue(self.displays[0].closed)

    def test_real_sender_loss_closes_lease(self):
        self.start(lambda *args:'com.rhyme.viola')
        self.call('Acquire');self.client.close_sync(None)
        deadline=time.monotonic()+3
        while not self.displays[0].closed and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(self.displays[0].closed);self.assertTrue(self.service.leases.empty())

    def test_capacity_and_invalid_handle_wire_refused(self):
        self.start(lambda *args:'com.rhyme.viola')
        for _ in range(4):self.call('Acquire')
        with self.assertRaises(GLib.Error):self.call('Acquire')
        with self.assertRaises(GLib.Error):self.call('Release',GLib.Variant('(s)',('../command',)))
        self.assertEqual(len(self.displays),4)


if __name__=='__main__':unittest.main()
