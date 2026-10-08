"""Phone calls with real audio from a Luma Connect companion phone (ADR-021).

The computer acts as a Bluetooth hands-free unit (HFP HF) for the paired
Android phone. PipeWire's native bluez5 backend already owns that role on
Fedora 44 (PipeWire 1.6, WirePlumber 0.5.14, BlueZ 5.87): it answers AT call
control and carries SCO audio, and exposes both through its telephony D-Bus
service. Nothing here speaks RFCOMM, touches SCO sockets or uses oFono
(Fedora ships no oFono provider for GNOME Calls, oFono's bus policy blocks
normal users, and BlueZ allows one owner per profile UUID, so oFono and
PipeWire's HF role conflict).

Pieces, all with injected D-Bus callers so they are tested without a bus:

* `PipeWireTelephony` mirrors `org.pipewire.Telephony` gateways and calls.
* `BluetoothCallProvider` is the Prairie Phone call provider for the one
  gateway that belongs to the Luma-selected companion.
* `BluetoothBonding` opens a short BlueZ pairing window for that phone.
* `WirePlumberRoles` turns the hands-free role on for the user.

Consent for audio (`RejectSCO`)
-------------------------------
Every gateway starts with `AudioGatewayTransport1.RejectSCO=true` (the
per-user WirePlumber drop-in sets `bluez5.telephony.default-reject-sco`, and
the provider enforces it again), so call audio stays on the phone. Only an
explicit choice on the computer, answering there or "Use computer audio",
sets it to false and calls `Activate()`. The end of the call, a disconnect,
revocation or closing the provider restores true. Gateways of any other
bonded phone are kept at true and never shown.

The computer never initiates the HFP connection: Android makes the most
recently connected HFP device its active call device, so only the phone
connects. The drop-in removes `bluez5.auto-connect` for the bonded address.

Privacy: phone numbers and caller names are never logged. Log lines carry
object paths, states and exception type names only.

Source references, PipeWire 1.6.8 (`spa/plugins/bluez5/telephony.c` has no
changes between 1.6.0 and 1.6.8):

* README-Telephony.md:26-44 manager, :68-88 AudioGateway, :224-345 Call.
* telephony.c:16-26 service name, root path and interfaces.
* telephony.c:112-145 AudioGateway1 (`Address`, volumes) and
  AudioGatewayTransport1 (`State`, `Codec`, writable `RejectSCO`, `Activate`).
* telephony.c:153-175 Call1 properties; :1401-1409 call state strings.
* telephony.c:248-253 error names; :550-559 transport states
  error/idle/pending/active.
* telephony.c:862-908 `Properties.Set`: `RejectSCO` is stored and NO
  PropertiesChanged is emitted, so the writer updates its own model.
* telephony.c:911-926 `Dial` accepts `[0-9A-D#*+,]{1,80}`; :1119 new
  gateways copy `default-reject-sco`.
* telephony.c:424-433, :460-466 `bluez5.telephony-dbus-service`,
  `.use-system-bus`, `.provide-ofono`, `.default-reject-sco`.
* telephony.c:1147-1200 gateways are announced by ObjectManager
  InterfacesAdded on the root; :1647-1705 calls by InterfacesAdded on the
  gateway path.
* backend-native.c:3015-3018 an incoming SCO connection is dropped while
  `RejectSCO` is true; :1888-1915 `Activate` sends AT+BCC (codec negotiation)
  or opens CVSD SCO.
* media-source.c:2228,1288 and media-sink.c:2665-2669,1898: SCO nodes of an
  audio-gateway card are `Stream/Output/Audio` and `Stream/Input/Audio`, so
  WirePlumber links them to the default speakers and microphone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import secrets
import threading
import time

LOG = logging.getLogger(__name__)

TELEPHONY_BUS = 'org.pipewire.Telephony'
TELEPHONY_ROOT = '/org/pipewire/Telephony'
AG_INTERFACE = 'org.pipewire.Telephony.AudioGateway1'
TRANSPORT_INTERFACE = 'org.pipewire.Telephony.AudioGatewayTransport1'
CALL_INTERFACE = 'org.pipewire.Telephony.Call1'
OBJECT_MANAGER = 'org.freedesktop.DBus.ObjectManager'
PROPERTIES = 'org.freedesktop.DBus.Properties'

GATEWAY_PATH = re.compile(r'/org/pipewire/Telephony/ag[0-9]{1,9}\Z')
CALL_PATH = re.compile(r'(/org/pipewire/Telephony/ag[0-9]{1,9})/call[0-9]{1,9}\Z')
ADDRESS = re.compile(r'[0-9A-F]{2}(?::[0-9A-F]{2}){5}\Z')
NUMBER = re.compile(r'[0-9A-D#*+,]{1,80}\Z')  # telephony.c:911-926, README-Telephony.md:110-115
TONES = re.compile(r'[0-9A-D#*]{1,32}\Z')  # telephony.c validate_tones; the length cap is Luma's
DIAL_SEPARATORS = re.compile(r'[\s().-]')
FINGERPRINT = re.compile(r'[0-9a-f]{64}\Z')
CALL_STATES = frozenset({'active', 'held', 'dialing', 'alerting', 'incoming', 'waiting', 'disconnected'})
TRANSPORT_STATES = frozenset({'error', 'idle', 'pending', 'active'})
LIVE_STATES = CALL_STATES - {'disconnected'}

# PipeWire state -> Prairie `CallPhase` value (prairie_apps.phone_backend.CallPhase).
PHASES = {'incoming': 'incoming', 'dialing': 'dialling', 'alerting': 'ringing-outgoing', 'active': 'active',
          'held': 'held', 'waiting': 'waiting', 'disconnected': 'ended'}


class Variant:
    """A D-Bus variant for injected callers: `Variant('b', True)` becomes `GLib.Variant('b', True)`."""

    __slots__ = ('signature', 'value')

    def __init__(self, signature, value):
        self.signature, self.value = signature, value

    def __eq__(self, other):
        return isinstance(other, Variant) and (self.signature, self.value) == (other.signature, other.value)

    def __repr__(self):
        return f'Variant({self.signature!r}, {self.value!r})'


class CallActive(PermissionError):
    """Refused because restarting the audio policy now would drop a live call."""


def _gvariant(signature, values):
    from gi.repository import GLib
    converted = tuple(GLib.Variant(v.signature, v.value) if isinstance(v, Variant) else v for v in values)
    return GLib.Variant(signature, converted) if signature != '()' else None


def _gio_caller(bus_type, name, *, timeout=3000):
    """`call(path, interface, method, signature, values)` on `name`, never auto-starting it."""
    from gi.repository import Gio
    connection = Gio.bus_get_sync(bus_type, None)

    def call(path, interface, method, signature, values):
        result = connection.call_sync(name, path, interface, method, _gvariant(signature, values), None,
                                      Gio.DBusCallFlags.NO_AUTO_START, timeout, None)
        return result.unpack() if result is not None else ()
    return connection, call


# --------------------------------------------------------------------------- PipeWire telephony

@dataclass(frozen=True)
class TelephonyCall:
    path: str
    state: str
    # The line identification and network name are personal data: kept out of repr so
    # that a snapshot that reaches a log line never carries a number.
    line: str = field(default='', repr=False)
    name: str = field(default='', repr=False)
    multiparty: bool = False

    @property
    def live(self):
        return self.state in LIVE_STATES


@dataclass(frozen=True)
class Gateway:
    path: str
    address: str
    transport: str = 'idle'
    reject_sco: bool | None = None
    calls: tuple = ()

    @property
    def live(self):
        return any(call.live for call in self.calls)


def _string(value, limit=256):
    return value if isinstance(value, str) and len(value) <= limit else ''


class PipeWireTelephony:
    """Mirror of `org.pipewire.Telephony` on the session bus.

    `call(path, interface, method, signature, values)` performs a method call
    and returns the unpacked reply tuple. `subscribe(interface, member,
    handler)` delivers `handler(path, args)` for signals from the service's
    current owner and returns an unsubscribe callable. Signal handlers and
    `reload` must run on one thread (the daemon's main context); controls may
    run on workers.
    """

    def __init__(self, call, subscribe=None):
        self._call, self._subscribe = call, subscribe
        self._lock = threading.RLock()
        self._gateways = {}
        self._listeners = {}
        self._token = 0
        self._subscriptions = []
        self.available = False

    # ----- lifecycle

    def start(self):
        if self._subscribe and not self._subscriptions:
            self._subscriptions = [self._subscribe(OBJECT_MANAGER, 'InterfacesAdded', self._interfaces_added),
                                   self._subscribe(OBJECT_MANAGER, 'InterfacesRemoved', self._interfaces_removed),
                                   self._subscribe(PROPERTIES, 'PropertiesChanged', self._properties_changed)]
        self.reload()

    def stop(self):
        for unsubscribe in self._subscriptions: unsubscribe()
        self._subscriptions = []
        self.vanished()

    def vanished(self):
        """The service left the bus (WirePlumber stopped or restarted)."""
        with self._lock:
            self._gateways, self.available = {}, False
        self._emit()

    def reload(self):
        try:
            objects, = self._call(TELEPHONY_ROOT, OBJECT_MANAGER, 'GetManagedObjects', '()', ())
            gateways = {}
            for path, interfaces in dict(objects).items():
                gateway = self._gateway_record(path, interfaces)
                if gateway is not None:
                    gateway['calls'] = self._load_calls(path)
                    gateways[path] = gateway
        except Exception as error:
            LOG.info('PipeWire telephony unavailable (%s)', type(error).__name__)
            self.vanished()
            return
        with self._lock:
            self._gateways, self.available = gateways, True
        self._emit()

    def _gateway_record(self, path, interfaces):
        if not isinstance(path, str) or not GATEWAY_PATH.fullmatch(path) or not isinstance(interfaces, dict):
            return None
        properties = interfaces.get(AG_INTERFACE)
        if not isinstance(properties, dict): return None
        address = properties.get('Address')
        address = address.upper() if isinstance(address, str) else ''
        if not ADDRESS.fullmatch(address):
            LOG.info('Ignoring telephony gateway %s without a Bluetooth address', path)
            return None
        record = {'address': address, 'transport': 'idle', 'reject_sco': None, 'calls': {}}
        self._apply_transport(record, interfaces.get(TRANSPORT_INTERFACE) or {})
        return record

    @staticmethod
    def _apply_transport(record, properties):
        state = properties.get('State')
        if isinstance(state, str) and state in TRANSPORT_STATES: record['transport'] = state
        if type(properties.get('RejectSCO')) is bool: record['reject_sco'] = properties['RejectSCO']

    def _load_calls(self, gateway_path):
        objects, = self._call(gateway_path, OBJECT_MANAGER, 'GetManagedObjects', '()', ())
        calls = {}
        for path, interfaces in dict(objects).items():
            record = self._call_record(gateway_path, path, interfaces)
            if record is not None: calls[path] = record
        return calls

    @staticmethod
    def _call_record(gateway_path, path, interfaces):
        match = CALL_PATH.fullmatch(path) if isinstance(path, str) else None
        if not match or match.group(1) != gateway_path or not isinstance(interfaces, dict): return None
        properties = interfaces.get(CALL_INTERFACE)
        if not isinstance(properties, dict) or properties.get('State') not in CALL_STATES: return None
        return {'state': properties['State'], 'line': _string(properties.get('LineIdentification'), 128),
                'name': _string(properties.get('Name'), 256), 'multiparty': properties.get('Multiparty') is True}

    # ----- signals

    def _interfaces_added(self, sender_path, args):
        try: path, interfaces = args
        except (TypeError, ValueError): return
        if isinstance(path, str) and GATEWAY_PATH.fullmatch(path) and sender_path == TELEPHONY_ROOT:
            gateway = self._gateway_record(path, interfaces)
            if gateway is None: return
            try: gateway['calls'] = self._load_calls(path)
            except Exception as error:
                LOG.info('Telephony gateway %s calls unavailable (%s)', path, type(error).__name__)
            with self._lock: self._gateways[path] = gateway
            LOG.info('Telephony gateway %s added', path)
        elif isinstance(path, str) and CALL_PATH.fullmatch(path) and sender_path == CALL_PATH.fullmatch(path).group(1):
            record = self._call_record(sender_path, path, interfaces)
            with self._lock:
                gateway = self._gateways.get(sender_path)
                if record is None or gateway is None: return
                gateway['calls'][path] = record
            LOG.info('Telephony call %s added (%s)', path, record['state'])
        else:
            return
        self._emit()

    def _interfaces_removed(self, sender_path, args):
        try: path, interfaces = args
        except (TypeError, ValueError): return
        with self._lock:
            if path in self._gateways and sender_path == TELEPHONY_ROOT and AG_INTERFACE in interfaces:
                del self._gateways[path]
                LOG.info('Telephony gateway %s removed', path)
            elif (isinstance(path, str) and CALL_PATH.fullmatch(path) and CALL_INTERFACE in interfaces
                    and sender_path in self._gateways and path in self._gateways[sender_path]['calls']):
                del self._gateways[sender_path]['calls'][path]
                LOG.info('Telephony call %s removed', path)
            else:
                return
        self._emit()

    def _properties_changed(self, path, args):
        try: interface, changed, _invalidated = args
        except (TypeError, ValueError): return
        if not isinstance(changed, dict): return
        with self._lock:
            if interface == TRANSPORT_INTERFACE and path in self._gateways:
                self._apply_transport(self._gateways[path], changed)
            elif interface == CALL_INTERFACE and isinstance(path, str) and CALL_PATH.fullmatch(path):
                gateway = self._gateways.get(CALL_PATH.fullmatch(path).group(1))
                record = gateway and gateway['calls'].get(path)
                if not record: return
                if changed.get('State') in CALL_STATES:
                    record['state'] = changed['State']
                    LOG.info('Telephony call %s is %s', path, record['state'])
                if 'LineIdentification' in changed: record['line'] = _string(changed['LineIdentification'], 128)
                if 'Name' in changed: record['name'] = _string(changed['Name'], 256)
                if type(changed.get('Multiparty')) is bool: record['multiparty'] = changed['Multiparty']
            else:
                return
        self._emit()

    # ----- model

    def on_change(self, callback):
        with self._lock:
            self._token += 1
            token = self._token
            self._listeners[token] = callback
        return lambda: self._listeners.pop(token, None)

    def _emit(self):
        with self._lock: listeners = list(self._listeners.values())
        for listener in listeners:
            try: listener()
            except Exception as error:
                LOG.warning('Telephony listener failed (%s)', type(error).__name__)

    def snapshot(self):
        with self._lock:
            return tuple(Gateway(path, g['address'], g['transport'], g['reject_sco'],
                                 tuple(TelephonyCall(p, c['state'], c['line'], c['name'], c['multiparty'])
                                       for p, c in sorted(g['calls'].items())))
                         for path, g in sorted(self._gateways.items()))

    def gateway(self, address):
        return next((g for g in self.snapshot() if g.address == address), None)

    def call_active(self):
        """True while any gateway, selected or not, has a call that is not disconnected."""
        return any(gateway.live for gateway in self.snapshot())

    # ----- controls

    def _gateway_path(self, address):
        if not isinstance(address, str) or not ADDRESS.fullmatch(address): raise ValueError('invalid Bluetooth address')
        gateway = self.gateway(address)
        if gateway is None: raise LookupError('phone is not connected for calls')
        return gateway.path

    def _known_call(self, path):
        match = CALL_PATH.fullmatch(path) if isinstance(path, str) else None
        with self._lock:
            if not match or path not in self._gateways.get(match.group(1), {}).get('calls', {}):
                raise LookupError('call has ended')

    def answer(self, call_path):
        self._known_call(call_path)
        self._call(call_path, CALL_INTERFACE, 'Answer', '()', ())

    def hangup(self, call_path):
        self._known_call(call_path)
        self._call(call_path, CALL_INTERFACE, 'Hangup', '()', ())

    def hold_and_answer(self, address):
        self._call(self._gateway_path(address), AG_INTERFACE, 'HoldAndAnswer', '()', ())

    def dial(self, address, number):
        # The number never appears in an exception or log message.
        if not isinstance(number, str) or not NUMBER.fullmatch(number): raise ValueError('invalid phone number')
        self._call(self._gateway_path(address), AG_INTERFACE, 'Dial', '(s)', (number,))

    def send_tones(self, address, tones):
        if not isinstance(tones, str) or not TONES.fullmatch(tones): raise ValueError('invalid tones')
        self._call(self._gateway_path(address), AG_INTERFACE, 'SendTones', '(s)', (tones,))

    def read_reject_sco(self, address):
        path = self._gateway_path(address)
        value, = self._call(path, PROPERTIES, 'Get', '(ss)', (TRANSPORT_INTERFACE, 'RejectSCO'))
        if type(value) is not bool: raise ValueError('invalid RejectSCO')
        with self._lock:
            if path in self._gateways: self._gateways[path]['reject_sco'] = value
        return value

    def set_reject_sco(self, address, reject):
        if type(reject) is not bool: raise ValueError('boolean required')
        path = self._gateway_path(address)
        self._call(path, PROPERTIES, 'Set', '(ssv)', (TRANSPORT_INTERFACE, 'RejectSCO', Variant('b', reject)))
        # telephony.c:897-904 stores the value without a PropertiesChanged signal.
        with self._lock:
            if path in self._gateways: self._gateways[path]['reject_sco'] = reject
        LOG.info('Telephony gateway %s RejectSCO=%s', path, reject)

    def activate_audio(self, address):
        self._call(self._gateway_path(address), TRANSPORT_INTERFACE, 'Activate', '()', ())

    @classmethod
    def gio(cls):
        """Session-bus instance bound to the service's unique owner. Create on the main thread."""
        from gi.repository import Gio
        connection, call = _gio_caller(Gio.BusType.SESSION, TELEPHONY_BUS)
        state = {'owner': None, 'ids': [], 'wanted': []}

        def resubscribe():
            for identifier in state['ids']: connection.signal_unsubscribe(identifier)
            state['ids'] = []
            if state['owner'] is None: return
            for interface, member, handler in state['wanted']:
                state['ids'].append(connection.signal_subscribe(
                    state['owner'], interface, member, None, None, Gio.DBusSignalFlags.NONE,
                    lambda _c, _s, path, _i, _m, parameters, handler=handler:
                        handler(path, parameters.unpack()) if path.startswith(TELEPHONY_ROOT) else None))

        def subscribe(interface, member, handler):
            entry = (interface, member, handler)
            state['wanted'].append(entry)
            resubscribe()
            return lambda: (state['wanted'].remove(entry) if entry in state['wanted'] else None, resubscribe())

        telephony = cls(call, subscribe)

        def appeared(_connection, _name, owner):
            state['owner'] = owner
            resubscribe()
            telephony.reload()

        def vanished(*_):
            state['owner'] = None
            resubscribe()
            telephony.vanished()

        watch = Gio.bus_watch_name_on_connection(connection, TELEPHONY_BUS, Gio.BusNameWatcherFlags.NONE,
                                                 appeared, vanished)
        original_stop = telephony.stop
        telephony.stop = lambda: (Gio.bus_unwatch_name(watch), original_stop())
        return telephony


# --------------------------------------------------------------------------- Prairie Phone provider

def _prairie_types():
    """`NativeCall` and `CallPhase` from prairie_apps (gi-free), with a transport-tagged record."""
    from dataclasses import dataclass as _dataclass
    from prairie_apps.phone_backend import CallPhase, NativeCall

    @_dataclass(frozen=True)
    class BluetoothCall(NativeCall):
        # NativeCall has no transport field; this subclass stays accepted by
        # CallSession.from_native and every isinstance check on NativeCall.
        transport: str = 'bluetooth'

    return NativeCall, CallPhase, BluetoothCall


_TYPES = None


def _types():
    global _TYPES
    if _TYPES is None: _TYPES = _prairie_types()
    return _TYPES


def normalize_number(text):
    """Removes visual separators a contact card may contain; raises ValueError without echoing the input."""
    if not isinstance(text, str) or len(text) > 128: raise ValueError('invalid phone number')
    number = DIAL_SEPARATORS.sub('', text)
    if not NUMBER.fullmatch(number): raise ValueError('invalid phone number')
    return number


class BluetoothCallProvider:
    """Prairie Phone `call_provider` for one companion phone's hands-free gateway.

    Same surface as `call_provider.CallProvider`: `start(listener)`,
    `invalidate()`, `close()`, `closed`, `calls()`, `control_authorized()`,
    `dial`, `accept`, `decline`, `hangup`, `audio_state`, `start_audio`,
    `stop_audio`, `mute_audio`.

    `address()` returns the selected companion's bonded Bluetooth address (or
    None); `authorized()` is True while that companion's pairing and call
    grant are current. `dispatch(callback)` runs the listener on the UI main
    context. `on_incoming()` is called once per newly ringing call.

    Call IDs are `<generation>:<call object path>`. The generation increases
    whenever the selected phone's gateway (re)appears, because PipeWire reuses
    `agN`/`callN` numbers after a reconnect. Start and answer times are
    observed on this computer.
    """

    def __init__(self, telephony, *, address, authorized, dispatch=lambda callback: callback(), now=time.time,
                 mute=None, on_incoming=lambda: None):
        self.telephony, self.address, self.authorized = telephony, address, authorized
        self.dispatch, self.now, self.on_incoming = dispatch, now, on_incoming
        self._mute = mute
        self.lock = threading.RLock()
        self.closed = False
        self.listener = lambda *_: None
        self.generation = 0
        self._gateway_path = None
        self._records = {}
        self._consent = False
        self._muted = False
        self._live = False
        self._was_authorized = False
        self._disconnect = lambda: None

    # ----- lifecycle

    def start(self, listener=None):
        if listener is not None: self.listener = listener
        self._disconnect = self.telephony.on_change(self.invalidate)
        self.invalidate()

    def close(self):
        with self.lock:
            if self.closed: return
            self.closed = True
            self.generation += 1
        self._disconnect()
        # Closing the Phone window never leaves the computer accepting call audio.
        self._restore_all(consent_only=True)
        self._unmute()
        with self.lock: self._records.clear(); self._gateway_path = None

    def revoke(self):
        """The companion was removed or lost its call grant: fail closed on every gateway."""
        with self.lock:
            self._consent = False
            self._records.clear()
            self._gateway_path = None
        self._unmute()
        self._restore_all(consent_only=False)
        self._publish()

    # ----- state

    def _selected(self):
        if self.closed or not self.authorized(): return None
        address = self.address()
        if not isinstance(address, str) or not ADDRESS.fullmatch(address): return None
        return self.telephony.gateway(address)

    def invalidate(self, *_args):
        if self.closed: return
        authorized = bool(self.authorized())
        if self._was_authorized and not authorized:
            self._was_authorized = False
            self.revoke()
            return
        self._was_authorized = authorized
        selected_address = self.address() if authorized else None
        ringing = False
        restore = []
        with self.lock:
            gateway = self._selected()
            if gateway is None:
                if self._gateway_path is not None:
                    LOG.info('Selected telephony gateway disconnected')
                self._gateway_path, self._consent, self._live = None, False, False
                self._records.clear()
            else:
                if gateway.path != self._gateway_path:
                    self.generation += 1
                    self._gateway_path, self._consent, self._live = gateway.path, False, False
                    self._records.clear()
                    restore.append((gateway.address, True))  # a new connection starts without consent
                moment = int(self.now())
                seen = set()
                for call in gateway.calls:
                    seen.add(call.path)
                    record = self._records.get(call.path)
                    if record is None:
                        record = self._records[call.path] = {
                            'started_at': moment, 'answered_at': 0,
                            'direction': 'incoming' if call.state in ('incoming', 'waiting') else 'outgoing'}
                        ringing = ringing or call.state == 'incoming'
                    if call.state == 'active' and not record['answered_at']: record['answered_at'] = moment
                for path in set(self._records) - seen: del self._records[path]
                ended = self._live and not gateway.live
                self._live = gateway.live
                if ended:
                    # Written even when this process never consented: another process (Phone)
                    # may have, and a Set emits no signal that would have updated this model.
                    LOG.info('Call ended; call audio returns to the phone')
                    self._consent = False
                if not restore and (ended or (not self._consent and gateway.reject_sco is not True)):
                    restore.append((gateway.address, True))
            # Every other phone's gateway stays closed to SCO and hidden.
            for other in self.telephony.snapshot():
                if other.address != selected_address and other.reject_sco is not True:
                    restore.append((other.address, True))
        for address, reject in restore: self._write_reject(address, reject)
        if not self._consent: self._unmute()
        self._publish()
        if ringing: self.on_incoming()

    def _write_reject(self, address, reject):
        try: self.telephony.set_reject_sco(address, reject)
        except Exception as error:
            LOG.warning('Could not set RejectSCO on a telephony gateway (%s)', type(error).__name__)

    def _restore_all(self, *, consent_only):
        with self.lock:
            had_consent, self._consent = self._consent, False
        if consent_only and not had_consent: return
        for gateway in self.telephony.snapshot():
            self._write_reject(gateway.address, True)

    def _publish(self):
        ready = bool(self.telephony.available and not self.closed and self.authorized())
        with self.lock:
            available = ready and self._selected() is not None
            snapshot = {'calls': [row[0] for row in self._rows()], 'dial_token': None, 'voice_available': available}

        def publish():
            if not self.closed: self.listener(snapshot, ready)
            return False
        self.dispatch(publish)

    def _rows(self):
        with self.lock:
            gateway = self._selected()
            if gateway is None or gateway.path != self._gateway_path: return ()
            rows = []
            for call in gateway.calls:
                record = self._records.get(call.path)
                if record is None: continue
                address = call.line if call.line and call.line != 'withheld' else ''
                rows.append((f'{self.generation}:{call.path}', address, record['direction'], PHASES[call.state],
                             record['started_at'], record['answered_at']))
            return tuple(rows)

    def calls(self):
        _native, CallPhase, BluetoothCall = _types()
        return tuple(BluetoothCall(call_id, address, direction, CallPhase(phase), started_at=started, answered_at=answered)
                     for call_id, address, direction, phase, started, answered in self._rows())

    def control_authorized(self):
        return self._selected() is not None

    # ----- controls

    def _current(self, call_id):
        if not isinstance(call_id, str) or ':' not in call_id: raise PermissionError('call has changed')
        generation, path = call_id.split(':', 1)
        with self.lock:
            gateway = self._selected()
            if (gateway is None or generation != str(self.generation) or gateway.path != self._gateway_path
                    or path not in self._records):
                raise PermissionError('call has changed')
            call = next((c for c in gateway.calls if c.path == path), None)
            if call is None or not call.live: raise PermissionError('call has ended')
            return gateway, call

    def accept(self, call_id):
        gateway, call = self._current(call_id)
        if call.state not in ('incoming', 'waiting'): raise PermissionError('call is not ringing')
        # Answering on this computer is the person's choice of computer audio.
        with self.lock: self._consent = True
        try:
            self.telephony.set_reject_sco(gateway.address, False)
            if call.state == 'waiting': self.telephony.hold_and_answer(gateway.address)
            else: self.telephony.answer(call.path)
        except Exception:
            with self.lock: self._consent = False
            self._write_reject(gateway.address, True)
            raise
        self.invalidate()

    def decline(self, call_id):
        _gateway, call = self._current(call_id)
        if call.state not in ('incoming', 'waiting'): raise PermissionError('call is not ringing')
        self.telephony.hangup(call.path)  # README-Telephony.md:265-267: UDUB for a waiting call
        self.invalidate()

    def hangup(self, call_id):
        _gateway, call = self._current(call_id)
        self.telephony.hangup(call.path)
        self.invalidate()

    def dial(self, number):
        number = normalize_number(number)
        gateway = self._selected()
        if gateway is None: raise PermissionError('Your phone cannot make calls right now.')
        if gateway.live: raise PermissionError('phone already has a call')
        # Audio stays on the phone until the person chooses this computer.
        self.telephony.dial(gateway.address, number)
        self.invalidate()
        return ''

    def audio_state(self):
        gateway = self._selected()
        with self.lock: consent, muted = self._consent, self._muted
        if gateway is None or gateway.path != self._gateway_path: return {'status': 'phone', 'muted': False}
        # An open SCO link is reported as it is, whoever opened it.
        status = 'connected' if gateway.transport == 'active' else 'connecting' if consent else 'phone'
        return {'status': status, 'muted': muted and status == 'connected'}

    def start_audio(self, call_id):
        gateway, call = self._current(call_id)
        if call.state != 'active': raise PermissionError('call is not active')
        with self.lock: self._consent = True
        try:
            self.telephony.set_reject_sco(gateway.address, False)
            if gateway.transport != 'active': self.telephony.activate_audio(gateway.address)
        except Exception as error:
            with self.lock: self._consent = False
            self._write_reject(gateway.address, True)
            raise PermissionError('call audio unavailable') from error
        self.invalidate()

    def stop_audio(self):
        """Stops accepting call audio. PipeWire's HF API cannot release an open SCO link;
        the phone's own audio switch returns an already connected call."""
        with self.lock: self._consent = False
        gateway = self._selected()
        if gateway is not None: self._write_reject(gateway.address, True)
        self._unmute()
        self.invalidate()

    def mute_audio(self, muted):
        if type(muted) is not bool: raise ValueError('boolean required')
        with self.lock:
            if not muted and not self._muted: return  # never unmute a microphone this provider did not mute
        if muted and self.audio_state()['status'] != 'connected': raise PermissionError('call audio is not on this computer')
        self._set_muted(muted)
        self.invalidate()

    def _set_muted(self, muted):
        mute = self._mute
        if mute is None:
            from prairie_apps.phone_backend import set_audio_input_muted
            mute = set_audio_input_muted
        mute(muted)
        with self.lock: self._muted = muted

    def _unmute(self):
        with self.lock: muted = self._muted
        if not muted: return
        try: self._set_muted(False)
        except Exception as error:
            LOG.warning('Could not unmute the microphone (%s)', type(error).__name__)


# --------------------------------------------------------------------------- BlueZ bonding

BLUEZ_BUS = 'org.bluez'
ADAPTER_INTERFACE = 'org.bluez.Adapter1'
DEVICE_INTERFACE = 'org.bluez.Device1'
BONDS_FILE = 'companion-bluetooth.json'
MAJOR_CLASS_PHONE = 0x02  # Bluetooth Assigned Numbers, Class of Device major class "Phone"


class BlueZ:
    """The few BlueZ calls bonding needs (doc/org.bluez.Adapter.rst, doc/org.bluez.Device.rst)."""

    def __init__(self, call):
        self.call = call

    def _objects(self):
        objects, = self.call('/', OBJECT_MANAGER, 'GetManagedObjects', '()', ())
        return dict(objects)

    def adapter(self):
        """(object path, address) of the first powered adapter."""
        for path, interfaces in sorted(self._objects().items()):
            properties = interfaces.get(ADAPTER_INTERFACE)
            if isinstance(properties, dict) and properties.get('Powered') is True:
                address = properties.get('Address')
                if isinstance(address, str) and ADDRESS.fullmatch(address.upper()):
                    return path, address.upper()
        raise LookupError('Bluetooth is off or unavailable')

    def devices(self, adapter_path):
        """{device path: {'address', 'bonded', 'class'}} on `adapter_path`."""
        result = {}
        for path, interfaces in self._objects().items():
            properties = interfaces.get(DEVICE_INTERFACE)
            if not isinstance(properties, dict) or properties.get('Adapter') != adapter_path: continue
            address = properties.get('Address')
            if not isinstance(address, str) or not ADDRESS.fullmatch(address.upper()): continue
            # BlueZ 5.73+ separates Bonded (stored keys) from Paired; use Bonded when present.
            bonded = properties['Bonded'] if 'Bonded' in properties else properties.get('Paired')
            result[path] = {'address': address.upper(), 'bonded': bonded is True,
                            'class': properties.get('Class') if type(properties.get('Class')) is int else None}
        return result

    def bonded(self, adapter_path):
        return {path: device for path, device in self.devices(adapter_path).items() if device['bonded']}

    def get(self, path, interface, name):
        return self.call(path, PROPERTIES, 'Get', '(ss)', (interface, name))[0]

    def set(self, path, interface, name, variant):
        self.call(path, PROPERTIES, 'Set', '(ssv)', (interface, name, variant))

    def remove_device(self, adapter_path, device_path):
        self.call(adapter_path, ADAPTER_INTERFACE, 'RemoveDevice', '(o)', (device_path,))

    @classmethod
    def gio(cls):
        from gi.repository import Gio
        _connection, call = _gio_caller(Gio.BusType.SYSTEM, BLUEZ_BUS, timeout=5000)
        return cls(call)


def _private_json_write(path, data):
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(data, sort_keys=True))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class BluetoothBonding:
    """Bonds the selected companion phone with this computer for hands-free calls.

    Flow (`bluetooth.bond`, desktop to phone): the computer reads its adapter
    address from BlueZ, records the bonded devices, and makes the adapter
    pairable and discoverable with 120 s BlueZ timeouts (so a crash cannot
    leave it discoverable). It then sends `{address, name}` to the phone,
    which only asks its person with a notification and answers
    `{"error": "needs-user"}`. No phone-to-desktop capability reports the
    outcome, so the computer polls BlueZ inside the same 120 s window. It
    accepts only when exactly ONE new bonded device appeared in that window
    (confirmed on a second poll) and the phone acknowledged the request; two
    or more new bonds, a device whose class is not a phone, or no bond are all
    refused. The accepted BlueZ address is stored in a private 0600 sidecar
    keyed by companion fingerprint, never in the journal. This computer never
    connects any profile to the phone.

    Pairing still needs the numeric-comparison confirmation from a BlueZ
    agent on this computer (GNOME Settings' Bluetooth panel or a Luma agent).
    """

    WINDOW = 120
    INTERVAL = 2

    def __init__(self, bluez, directory, *, call=None, now=time.monotonic, sleep=time.sleep, cancelled=lambda: False):
        if call is None:
            from .companion import call
        self.bluez, self.directory, self.call = bluez, Path(directory), call
        self.now, self.sleep, self.cancelled = now, sleep, cancelled
        self._lock = threading.Lock()

    @property
    def path(self):
        return self.directory / BONDS_FILE

    def _load(self):
        try: data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError): return {}
        return data if isinstance(data, dict) else {}

    def remembered(self, fingerprint):
        value = self._load().get(fingerprint)
        return value if isinstance(value, str) and ADDRESS.fullmatch(value) else None

    def _remember(self, fingerprint, address):
        with self._lock:
            data = self._load()
            if address is None: data.pop(fingerprint, None)
            else: data[fingerprint] = address
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            _private_json_write(self.path, data)

    def desktop_address(self):
        return self.bluez.adapter()[1]

    def begin(self, fingerprint, *, name):
        """Worker. Returns 'bonded', 'refused', 'not-found', 'ambiguous', 'not-a-phone' or 'cancelled'."""
        if not isinstance(fingerprint, str) or not FINGERPRINT.fullmatch(fingerprint): raise ValueError('invalid device')
        if not isinstance(name, str) or not 0 < len(name) <= 128 or any(ord(c) < 0x20 or ord(c) == 0x7f for c in name):
            raise ValueError('invalid computer name')
        adapter_path, address = self.bluez.adapter()
        before = set(self.bluez.bonded(adapter_path))
        deadline = self.now() + self.WINDOW
        keys = ('PairableTimeout', 'DiscoverableTimeout', 'Pairable', 'Discoverable')
        previous = {key: self.bluez.get(adapter_path, ADAPTER_INTERFACE, key) for key in keys}
        try:
            self.bluez.set(adapter_path, ADAPTER_INTERFACE, 'PairableTimeout', Variant('u', self.WINDOW))
            self.bluez.set(adapter_path, ADAPTER_INTERFACE, 'DiscoverableTimeout', Variant('u', self.WINDOW))
            self.bluez.set(adapter_path, ADAPTER_INTERFACE, 'Pairable', Variant('b', True))
            self.bluez.set(adapter_path, ADAPTER_INTERFACE, 'Discoverable', Variant('b', True))
            receipt = self.call(self.directory, fingerprint, 'bluetooth.bond', {'address': address, 'name': name},
                                lifetime=self.WINDOW)
            if (not isinstance(receipt, dict) or receipt.get('state') != 'complete'
                    or receipt.get('result') != {'error': 'needs-user'}):
                return 'refused'
            outcome, device_path, device = self._wait(adapter_path, before, deadline)
        finally:
            self._restore(adapter_path, previous)
        if outcome != 'bonded': return outcome
        try: self.bluez.set(device_path, DEVICE_INTERFACE, 'Trusted', Variant('b', True))
        except Exception as error:
            LOG.warning('Could not mark the bonded phone trusted (%s)', type(error).__name__)
        self._remember(fingerprint, device['address'])
        LOG.info('Companion phone bonded over Bluetooth')
        return 'bonded'

    def _wait(self, adapter_path, before, deadline):
        candidate, grace = None, False
        while True:
            if self.cancelled(): return 'cancelled', None, None
            fresh = {path: device for path, device in self.bluez.bonded(adapter_path).items() if path not in before}
            if len(fresh) > 1:
                LOG.warning('More than one Bluetooth device bonded during the window; refusing')
                return 'ambiguous', None, None
            if len(fresh) == 1:
                path, device = next(iter(fresh.items()))
                if candidate == path:
                    major = (device['class'] >> 8) & 0x1f if device['class'] is not None else MAJOR_CLASS_PHONE
                    if major != MAJOR_CLASS_PHONE: return 'not-a-phone', None, None
                    return 'bonded', path, device
                candidate = path
            else:
                candidate = None
            if self.now() >= deadline:
                if candidate is None or grace: return 'not-found', None, None
                grace = True  # one confirmation poll for a bond seen at the very end
            self.sleep(self.INTERVAL)

    def _restore(self, adapter_path, previous):
        # Timeouts first: BlueZ re-arms or cancels its timers when a timeout changes.
        order = [('DiscoverableTimeout', 'u'), ('PairableTimeout', 'u'), ('Discoverable', 'b'), ('Pairable', 'b')]
        for key, signature in order:
            value = previous.get(key)
            if value is None: continue
            try: self.bluez.set(adapter_path, ADAPTER_INTERFACE, key, Variant(signature, value))
            except Exception as error:
                LOG.warning('Could not restore Bluetooth %s (%s)', key, type(error).__name__)

    def forget(self, fingerprint):
        """Removes the phone's BlueZ bond (if still present) and the sidecar entry. Returns True if a bond was removed."""
        address = self.remembered(fingerprint)
        if address is None: return False
        removed = False
        try:
            adapter_path, _ = self.bluez.adapter()
            for path, device in self.bluez.devices(adapter_path).items():
                if device['address'] == address:
                    self.bluez.remove_device(adapter_path, path)
                    removed = True
        finally:
            self._remember(fingerprint, None)
        return removed


# --------------------------------------------------------------------------- WirePlumber roles

WIREPLUMBER_DROP_IN = '90-luma-connect-calls.conf'
# PipeWire 1.6 defaults (doc/dox/config/pipewire-props.7.md:1105; bluez5-dbus.c parse_roles keeps
# asha_sink unless roles are given; backend-native.c:111 adds hfp_hf and hfp_ag). WirePlumber
# appends nested arrays from later fragments, so the complete list is written with `override.`
# (WirePlumber docs/rst/daemon/configuration/modifying_configuration.rst:222-257).
ROLES_WITH_HANDS_FREE = ('a2dp_sink', 'a2dp_source', 'bap_sink', 'bap_source', 'bap_bcast_sink',
                         'bap_bcast_source', 'asha_sink', 'hfp_hf', 'hfp_ag')


class WirePlumberRoles:
    """Per-user WirePlumber drop-in that enables the hands-free role for Luma Connect calls.

    Fragments in `$XDG_CONFIG_HOME/wireplumber/wireplumber.conf.d` load after
    the system's (WirePlumber docs/rst/daemon/locations.rst, "Configuration
    fragments"), so this file overrides Luma's
    `/etc/wireplumber/wireplumber.conf.d/60-luma-bluetooth-roles.conf`.
    WirePlumber reads configuration only at startup, and the bluez5 monitor
    runs inside it, so a change restarts `wireplumber.service`. That drops
    every Bluetooth audio connection, so it is refused while any call is live.
    `bluez5.codecs` is deliberately not set: a codec list without cvsd, msbc
    and lc3_swb can disable HFP speech codecs.
    """

    def __init__(self, restart, *, call_active, directory=None):
        self.restart, self.call_active = restart, call_active
        base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
        self.directory = Path(directory) if directory is not None else base / 'wireplumber' / 'wireplumber.conf.d'

    @property
    def path(self):
        return self.directory / WIREPLUMBER_DROP_IN

    @staticmethod
    def content(address=None):
        """Drop-in text. `address` (the bonded phone) adds a rule that keeps this computer from
        auto-connecting to it; PipeWire's default is already no auto-connect (bluez5-dbus.c:262),
        so the rule is defensive against other fragments.

        Enable before bonding (`address=None`): Android records the computer's service UUIDs
        during bonding, so the hands-free role must already be registered with BlueZ."""
        if address is not None and (not isinstance(address, str) or not ADDRESS.fullmatch(address)):
            raise ValueError('invalid Bluetooth address')
        roles = ' '.join(ROLES_WITH_HANDS_FREE)
        rules = '' if address is None else (
            'monitor.bluez.rules = [\n'
            '  {\n'
            f'    matches = [ {{ api.bluez5.address = "{address}" }} ]\n'
            '    # Only the phone connects hands-free; this computer must not become its active call device.\n'
            '    actions = { update-props = { bluez5.auto-connect = [ ] } }\n'
            '  }\n'
            ']\n')
        return (
            '# Written by Luma Connect for calls through a paired Android phone (ADR-021).\n'
            '# Removing this file and restarting WirePlumber returns to the Luma default,\n'
            '# /etc/wireplumber/wireplumber.conf.d/60-luma-bluetooth-roles.conf.\n'
            'monitor.bluez.properties = {\n'
            f'  override.bluez5.roles = [ {roles} ]\n'
            '  bluez5.telephony-dbus-service = true\n'
            '  # Call audio stays on the phone until the person chooses this computer.\n'
            '  bluez5.telephony.default-reject-sco = true\n'
            '}\n'
        ) + rules

    def _guard(self):
        if self.call_active(): raise CallActive('a call is in progress; try again after it ends')

    def enable(self, address=None):
        text = self.content(address)
        try:
            if self.path.read_text() == text: return False
        except FileNotFoundError:
            pass
        self._guard()
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / ('.' + WIREPLUMBER_DROP_IN + '-' + secrets.token_hex(8))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        try:
            os.fchmod(fd, 0o644)
            with os.fdopen(fd, 'w') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.restart()
        return True

    def disable(self):
        if not self.path.exists() and not self.path.is_symlink(): return False
        self._guard()
        self.path.unlink()
        self.restart()
        return True

    @staticmethod
    def systemd_restart(call):
        """`restart` for systemd's user manager: RestartUnit("wireplumber.service", "replace")."""
        return lambda: call('/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager', 'RestartUnit', '(ss)',
                            ('wireplumber.service', 'replace'))

    @classmethod
    def gio(cls, *, call_active):
        from gi.repository import Gio
        _connection, call = _gio_caller(Gio.BusType.SESSION, 'org.freedesktop.systemd1', timeout=10000)
        return cls(cls.systemd_restart(call), call_active=call_active)
