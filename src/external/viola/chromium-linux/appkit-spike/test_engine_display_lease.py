# SPDX-License-Identifier: GPL-3.0-only
"""Leased headless bus validation; identity admission has its separate owner.

These controls use real owned UNIX sockets, not a compositor/signature fixture.
"""
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from gi.repository import GLib
from engine_display import EngineDisplay


class Reply:
    def __init__(self, record, events): self.record, self.events = record, events
    def call_sync(self, *args):
        self.events.append(args[3])
        if args[3] == 'Acquire': return GLib.Variant('(s)', (json.dumps(self.record),))


class LeaseBus(unittest.TestCase):
    def setUp(self):
        root = Path('/run/user')/str(os.getuid())/'luma-viola'
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.display = tempfile.TemporaryDirectory(prefix='display-', dir=root)
        self.addCleanup(self.display.cleanup)
        self.bus = tempfile.TemporaryDirectory(prefix='viola-engine-bus-', dir=self.display.name)
        self.addCleanup(self.bus.cleanup)
        self.paths = [Path(self.display.name)/('viola-engine-'+'a'*32),Path(self.bus.name)/'bus']
        self.sockets = []
        for path in self.paths:
            stream=socket.socket(socket.AF_UNIX); stream.bind(str(path)); self.sockets.append(stream)
            self.addCleanup(stream.close)
        self.record = {'handle':'b'*32, 'wayland_display':str(self.paths[0]),
                       'private_bus_address':'unix:path='+str(self.paths[1])}
        self.events=[]

    def open(self, record=None):
        reply=Reply(record or self.record,self.events)
        with patch.dict(os.environ, {'FLATPAK_ID':'com.rhyme.viola'}), \
                patch('gi.repository.Gio.bus_get_sync', return_value=reply), \
                patch.object(EngineDisplay,'establish_pointer', lambda _,address:self.events.append(address)):
            return EngineDisplay(None)

    def test_only_exact_same_lease_bus_connects(self):
        display=self.open()
        self.assertEqual(self.events,['Acquire',self.record['private_bus_address']])
        self.assertEqual(display.socket_path,self.paths[0])
        display.close(); display.close()
        self.assertEqual(self.events.count('Release'),1)

    def test_bus_missing_or_outside_lease_refuses_and_releases(self):
        for address in [None,'unix:path=/run/user/0/bus','tcp:host=127.0.0.1',
                        'unix:path='+str(self.paths[1])+';unix:path=/tmp/bus']:
            self.events.clear(); record=dict(self.record)
            if address is None:record.pop('private_bus_address')
            else:record['private_bus_address']=address
            with self.assertRaises((ValueError,FileNotFoundError,PermissionError)):
                self.open(record)
            self.assertEqual(self.events,['Acquire','Release'])

    def test_unsafe_bus_directory_and_non_socket_refused(self):
        Path(self.bus.name).chmod(0o755)
        with self.assertRaises(PermissionError):self.open()
        Path(self.bus.name).chmod(0o700)
        self.paths[1].unlink();self.paths[1].write_bytes(b'not a socket')
        with self.assertRaises(PermissionError):self.open()
        self.assertNotIn(self.record['private_bus_address'],self.events)

    def test_private_pointer_stops_before_lease_release(self):
        display=self.open()
        events=self.events
        class Pointer:
            def call_sync(self,*args):events.append(args[3])
            def close_sync(self,*args):events.append('ConnectionClosed')
        display.pointer_connection=Pointer();display.pointer_session='/private/session'
        display.close();display.close()
        self.assertEqual(events[-3:],['Stop','ConnectionClosed','Release'])


if __name__=='__main__':unittest.main()
