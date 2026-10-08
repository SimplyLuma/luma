# SPDX-License-Identifier: Apache-2.0
"""Signed Clock client bridge into the existing native managed alarm agent."""
import json
import hashlib
import re
import os
from pathlib import Path
from gi.repository import Gio, GLib
from .clock_backend import ClockStore, valid_uid
from .clock_host_protocol import BUS, OBJECT, MAX_BYTES, validate_plan

XML = '<node><interface name="'+BUS+'"><method name="GetPlan"><arg type="s" direction="out"/></method><method name="SetPlan"><arg type="s" direction="in"/><arg type="s" direction="in"/></method><method name="Stop"><arg type="s" direction="in"/></method><method name="Snooze"><arg type="s" direction="in"/></method><signal name="Changed"/></interface></node>'

def revision(data):
    plan = {key: data.get(key, []) for key in ('alarms', 'timers')}
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

class HostInterface:
    def __init__(self, connection, service):
        self.connection, self.service = connection, service
        self.registration = connection.register_object(OBJECT, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], self._call, None, None)
        self.owner = Gio.bus_own_name_on_connection(connection, BUS, Gio.BusNameOwnerFlags.DO_NOT_QUEUE, None, None)

    def changed(self): self.connection.emit_signal(None, OBJECT, BUS, 'Changed', None)

    def close(self):
        Gio.bus_unown_name(self.owner); self.connection.unregister_object(self.registration)

    def _call(self, connection, sender, _path, _interface, method, args, invocation):
        try:
            from luma_installer.app_data_broker import authenticate
            if authenticate(connection, sender) != 'org.projectluma.Clock': raise PermissionError('Only installed Luma Clock can change its alarms.')
            if method == 'GetPlan':
                data = self.service.store._read()
                result = {'alarms': data.get('alarms', []), 'timers': data.get('timers', []), 'snoozes': self.service.state.snoozes, 'ringing': self.service.ringing, 'revision': revision(data)}
                text = json.dumps(result, separators=(',', ':'), allow_nan=False)
                if len(text.encode()) > MAX_BYTES: raise ValueError('Clock plan exceeds its safe size.')
                invocation.return_value(GLib.Variant('(s)', (text,))); return
            if method == 'SetPlan':
                expected, text = args.unpack()
                if not re.fullmatch('[a-f0-9]{64}', expected): raise ValueError('Invalid Clock revision.')
                plan = validate_plan(text)
                data = self.service.store._read()
                if revision(data) != expected: raise ValueError('The alarm plan changed; refresh Clock and try again.')
                data.update(plan)
                self.service.store._write(data); self.service.reschedule()
            elif method in ('Stop', 'Snooze'):
                uid = valid_uid(args.unpack()[0])
                if not any(a.uid == uid for a in self.service.store.alarms()): raise ValueError('This alarm does not exist.')
                (self.service.stop if method == 'Stop' else self.service.snooze)(uid)
            else: raise ValueError('Unsupported Clock operation.')
            self.changed(); invocation.return_value(GLib.Variant('()', ()))
        except Exception as error: invocation.return_dbus_error(BUS+'.Refused', str(error))

def host_available():
    # Other Linux desktops retain Clock's supported Background/Notification
    # portal scheduler. Luma's fixed host service is used where it is installed.
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        active = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'NameHasOwner', GLib.Variant('(s)', (BUS,)), GLib.VariantType.new('(b)'), Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        if active: return True
        names = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', 'ListActivatableNames', None, GLib.VariantType.new('(as)'), Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
        return BUS in names
    except GLib.Error: return False


def call(method, value=None):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    try:
        response = bus.call_sync(BUS, OBJECT, BUS, method, None if value is None else GLib.Variant('(ss)' if method == 'SetPlan' else '(s)', value if method == 'SetPlan' else (value,)), GLib.VariantType.new('(s)' if method == 'GetPlan' else '()'), Gio.DBusCallFlags.NONE, 15000, None)
    except GLib.Error as error:
        raise OSError(error.message) from error
    return response.unpack()[0] if method == 'GetPlan' else None

class HostClockStore(ClockStore):
    host_scheduled = True
    def __init__(self, path=None):
        # Only city/world data stays in the sandbox. Native alarms remain owned
        # by the one existing managed agent, including across window close.
        data_home = Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))
        self.path = data_home/'prairie/clock/state.json'
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.host_status = {}
        if not self.path.exists(): ClockStore._write(self, {'world': [], 'world_setup_done': False})
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.subscription = self.connection.signal_subscribe(BUS, BUS, 'Changed', OBJECT, None, Gio.DBusSignalFlags.NONE, self._changed)

    def _read(self):
        data = ClockStore._read(self)
        plan = json.loads(call('GetPlan'))
        self.host_status = {'snoozes': plan['snoozes'], 'ringing': plan['ringing']}
        data['_host_revision'] = plan['revision']
        data['alarms'], data['timers'] = plan['alarms'], plan['timers']
        return data

    def _write(self, data):
        plan = {'alarms': data.get('alarms', []), 'timers': data.get('timers', [])}
        text = json.dumps(plan, separators=(',', ':'), allow_nan=False)
        validate_plan(text)
        expected = data.get('_host_revision')
        if expected is None: raise ValueError('Read the native alarm plan before saving it.')
        call('SetPlan', (expected, text))
        ClockStore._write(self, {key: value for key, value in data.items() if key != '_host_revision'})

    def action(self, method, uid): call(method, valid_uid(uid))

    def _changed(self, *_):
        # Mirror a spent timer/alarm into the app's watched private file;
        # never send the host's change back as another SetPlan.
        try: ClockStore._write(self, {key: value for key, value in self._read().items() if key != '_host_revision'})
        except Exception:
            import logging
            logging.getLogger('prairie.clock.host').exception('Could not refresh the native alarm plan')

    def close(self):
        if self.subscription:
            self.connection.signal_unsubscribe(self.subscription); self.subscription = 0
