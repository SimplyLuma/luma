# SPDX-License-Identifier: Apache-2.0
"""Fixed Monitor API: sampled identities, owned pidfds, never paths or commands."""
import time
import threading
import os
from gi.repository import Gio, GLib
from .model import Sampler, filesystems
from .integration import DesktopCatalog, battery_info, performance_profile, inhibitors
from .live_data import resource_facts
from .host_protocol import BUS, OBJECT, encode, decode

XML = '<node><interface name="'+BUS+'"><method name="Sample"><arg type="b" direction="in"/><arg type="s" direction="out"/></method><method name="Inspect"><arg type="u" direction="in"/><arg type="t" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method><method name="ForceQuit"><arg type="a(ut)" direction="in"/><arg type="u" direction="out"/><arg type="u" direction="out"/></method><method name="QuitApp"><arg type="s" direction="in"/><arg type="b" direction="out"/></method></interface></node>'

class Broker:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.catalog = DesktopCatalog()
        self.sampler = Sampler(resolve=self.catalog.resolve)
        self.active = False
        self.last_used = time.monotonic()
        self.last_sample = 0
        self.last_memory = False
        self.cached = None
        self.snapshot = None
        self.operation_active = False
        self.owner = Gio.bus_own_name(Gio.BusType.SESSION, BUS, Gio.BusNameOwnerFlags.NONE, self._acquired, None, lambda *_: self.loop.quit())
        GLib.timeout_add_seconds(15, self._idle)

    def _idle(self):
        if not self.active and not self.operation_active and time.monotonic() - self.last_used > 60:
            self.loop.quit(); return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _acquired(self, connection, *_):
        connection.register_object(OBJECT, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self._call, None, None)

    def _call(self, connection, sender, _path, _interface, method, args, invocation):
        try:
            from luma_installer.app_data_broker import authenticate
            if authenticate(connection, sender) != 'io.luma.Monitor':
                raise PermissionError('Only installed Luma Monitor can read host activity.')
            self.last_used = time.monotonic()
            if method != 'Sample':
                self._operation(method, args, invocation); return
            memory, = args.unpack()
            if type(memory) is not bool: raise ValueError('Invalid sampling mode.')
            self.last_used = time.monotonic()
            if self.active: raise ValueError('A host activity sample is already running.')
            # Reuse an actual measured sample rather than resampling at caller speed.
            if self.cached is not None and self.last_used - self.last_sample < .15 and (self.last_memory or not memory):
                invocation.return_value(GLib.Variant('(s)', (self.cached,))); return
        except Exception as error:
            invocation.return_dbus_error(BUS+'.Refused', str(error)); return
        self.active = True
        def work():
            try:
                self.catalog.refresh_running()
                sample = self.sampler.sample(memory=memory)
                sample['battery'] = battery_info(); sample['profile'] = performance_profile()
                sample['filesystems'] = filesystems(); sample['advanced_facts'] = resource_facts(sample)
                disk = os.statvfs('/'); sample['disk_free'] = (disk.f_bavail*disk.f_frsize, disk.f_blocks*disk.f_frsize)
                held = inhibitors()
                for row in sample['rows']:
                    if held is not None and not row['background']: row['inhibits'] = any(p.pid in held for p in row['members'])
                payload, error = encode(sample, self.sampler.users), None
            except Exception as failure: payload, error = None, str(failure)
            GLib.idle_add(finish, payload, error, None if error else sample)
        def finish(payload, error, sample):
            self.active = False
            if error: invocation.return_dbus_error(BUS+'.Refused', error)
            else:
                self.snapshot = sample
                self.cached = payload; self.last_sample = time.monotonic(); self.last_memory = memory
                invocation.return_value(GLib.Variant('(s)', (payload,)))
            return GLib.SOURCE_REMOVE
        threading.Thread(target=work, name='monitor-host-sample', daemon=False).start()

    def _operation(self, method, args, invocation):
        import json
        from .host_protocol import MAX_BYTES
        if self.snapshot is None: raise ValueError('Read a host activity sample first.')
        if self.operation_active: raise ValueError('Another Monitor operation is running.')
        snapshot = self.snapshot
        def process(pid, start):
            item = next((p for p in snapshot['processes'] if p.pid == pid and p.start == start), None)
            if item is None: raise ProcessLookupError('Refresh the selected process.')
            return item
        if method == 'Inspect':
            pid, start, kind = args.unpack()
            if kind not in ('properties', 'files', 'maps'): raise ValueError('Unknown process detail.')
            item = process(pid, start)
            def operation():
                from .inspection import inspect_properties, inspect_table
                result = inspect_properties(item) if kind == 'properties' else inspect_table(item, kind)
                text = json.dumps(result, allow_nan=False)
                if len(text.encode()) > MAX_BYTES: raise ValueError('Process detail exceeds its safe size.')
                return GLib.Variant('(s)', (text,))
        elif method == 'ForceQuit':
            identities, = args.unpack()
            if not 1 <= len(identities) <= 1024 or len(set(identities)) != len(identities): raise ValueError('Invalid process selection.')
            items = tuple(process(pid, start) for pid, start in identities)
            def operation():
                from .integration import force_quit_processes
                return GLib.Variant('(uu)', force_quit_processes(items))
        elif method == 'QuitApp':
            identifier, = args.unpack()
            if len(identifier) > 256: raise ValueError('Invalid application identity.')
            row = next((r for r in snapshot['rows'] if r['id'] == identifier and not r['background']), None)
            if row is None or any(p.uid != os.getuid() for p in row['members']): raise PermissionError('This application is not owned by this user.')
            def operation():
                from .integration import quit_row
                return GLib.Variant('(b)', (quit_row(row),))
        else: raise ValueError('Unsupported Monitor operation.')
        self.operation_active = True
        def work():
            try: result, error = operation(), None
            except Exception as failure: result, error = None, str(failure)
            GLib.idle_add(finish, result, error)
        def finish(result, error):
            self.operation_active = False
            if error: invocation.return_dbus_error(BUS+'.Refused', error)
            else: invocation.return_value(result)
            return GLib.SOURCE_REMOVE
        threading.Thread(target=work, name='monitor-host-operation', daemon=False).start()

    def run(self):
        try: self.loop.run()
        finally: self.catalog.close(); Gio.bus_unown_name(self.owner)

class HostSampler:
    """Client shape matches Sampler; response preserves PID/start identity."""
    host = True
    def __init__(self): self.previous = None; self.users = {}
    def reset(self): self.previous = None
    def sample(self, *, memory=False, now=None):
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        response = connection.call_sync(BUS, OBJECT, BUS, 'Sample', GLib.Variant('(b)', (memory,)), GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 15000, None)
        result = decode(response.unpack()[0]); self.users = result.pop('users'); self.previous = {'time': result['time']}
        return result

class HostCatalog:
    def refresh_running(self): pass
    def close(self): pass
    def resolve(self, *_): raise RuntimeError('Host identities are supplied by the fixed activity API.')


def host_call(method, arguments, result_type):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    try:
        return bus.call_sync(BUS, OBJECT, BUS, method, arguments, GLib.VariantType.new(result_type), Gio.DBusCallFlags.NONE, 15000, None).unpack()
    except GLib.Error as error:
        raise OSError(error.message) from error

if __name__ == '__main__': Broker().run()
