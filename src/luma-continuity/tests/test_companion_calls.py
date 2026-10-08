"""Bluetooth hands-free calls for a companion phone. Synthetic D-Bus models; no bus, no Bluetooth, no gi."""
import io
import logging
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

try:
    import prairie_apps.phone_backend  # noqa: F401
except ImportError:  # the documented command sets PYTHONPATH to src/luma-continuity only
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'prairie-core'))
from prairie_apps.phone_backend import CallPhase, CallSession, NativeCall

from luma_continuity.companion_calls import (AG_INTERFACE, BONDS_FILE, CALL_INTERFACE, OBJECT_MANAGER, PROPERTIES,
                                             ROLES_WITH_HANDS_FREE, TELEPHONY_ROOT, TRANSPORT_INTERFACE, WIREPLUMBER_DROP_IN,
                                             BlueZ, BluetoothBonding, BluetoothCallProvider, CallActive,
                                             PipeWireTelephony, Variant, WirePlumberRoles, normalize_number)

PHONE = 'AA:BB:CC:DD:EE:01'
OTHER = 'AA:BB:CC:DD:EE:02'
DESKTOP = '00:1A:7D:DA:71:13'
NUMBER = '+15551234567'
FINGERPRINT = 'ab' * 32


class DBusError(Exception):
    pass


class FakePipeWire:
    """org.pipewire.Telephony as described in README-Telephony.md and telephony.c (PipeWire 1.6)."""

    def __init__(self, default_reject=True):
        self.default_reject = default_reject
        self.gateways = {}
        self.handlers = {}
        self.methods = []
        self.fail = {}

    # ----- bus surface

    def subscribe(self, interface, member, handler):
        self.handlers.setdefault((interface, member), []).append(handler)
        return lambda: self.handlers[(interface, member)].remove(handler)

    def emit(self, path, interface, member, *args):
        for handler in list(self.handlers.get((interface, member), [])): handler(path, args)

    def call(self, path, interface, method, signature, values):
        self.methods.append((path, interface, method, values))
        if (interface, method) in self.fail: raise self.fail[(interface, method)]
        if (interface, method) == (OBJECT_MANAGER, 'GetManagedObjects'):
            if path == TELEPHONY_ROOT:
                return ({p: self._gateway_interfaces(p) for p in self.gateways},)
            return ({c: {CALL_INTERFACE: dict(props)} for c, props in self.gateways[path]['calls'].items()},)
        if interface == PROPERTIES and method == 'Get':
            return (self.gateways[path]['transport'][values[1]],)
        if interface == PROPERTIES and method == 'Set':
            assert signature == '(ssv)' and values[0] == TRANSPORT_INTERFACE and values[1] == 'RejectSCO'
            assert isinstance(values[2], Variant) and values[2].signature == 'b'
            self.gateways[path]['transport']['RejectSCO'] = values[2].value
            return ()
        return ()

    def _gateway_interfaces(self, path):
        gateway = self.gateways[path]
        return {AG_INTERFACE: {'Address': gateway['address'], 'SpeakerVolume': 7, 'MicrophoneVolume': 7},
                TRANSPORT_INTERFACE: dict(gateway['transport'])}

    # ----- phone behaviour

    def connect(self, number, address):
        path = f'{TELEPHONY_ROOT}/ag{number}'
        self.gateways[path] = {'address': address, 'calls': {},
                               'transport': {'State': 'idle', 'Codec': 1, 'RejectSCO': self.default_reject}}
        self.emit(TELEPHONY_ROOT, OBJECT_MANAGER, 'InterfacesAdded', path, self._gateway_interfaces(path))
        return path

    def disconnect(self, path):
        del self.gateways[path]
        self.emit(TELEPHONY_ROOT, OBJECT_MANAGER, 'InterfacesRemoved', path, [AG_INTERFACE, TRANSPORT_INTERFACE])

    def add_call(self, gateway, number, state, line=NUMBER):
        path = f'{gateway}/call{number}'
        props = {'LineIdentification': line, 'IncomingLine': '', 'Name': 'Alex Example', 'Multiparty': False,
                 'State': state}
        self.gateways[gateway]['calls'][path] = props
        self.emit(gateway, OBJECT_MANAGER, 'InterfacesAdded', path, {CALL_INTERFACE: dict(props)})
        return path

    def call_state(self, path, state):
        gateway = path.rsplit('/', 1)[0]
        self.gateways[gateway]['calls'][path]['State'] = state
        self.emit(path, PROPERTIES, 'PropertiesChanged', CALL_INTERFACE, {'State': state}, [])

    def remove_call(self, path):
        gateway = path.rsplit('/', 1)[0]
        del self.gateways[gateway]['calls'][path]
        self.emit(gateway, OBJECT_MANAGER, 'InterfacesRemoved', path, [CALL_INTERFACE])

    def transport_state(self, gateway, state):
        self.gateways[gateway]['transport']['State'] = state
        self.emit(gateway, PROPERTIES, 'PropertiesChanged', TRANSPORT_INTERFACE, {'State': state}, [])

    def reject(self, gateway):
        return self.gateways[gateway]['transport']['RejectSCO']

    def invoked(self, method):
        return [entry for entry in self.methods if entry[2] == method]


class TelephonyTest(unittest.TestCase):
    def setUp(self):
        self.bus = FakePipeWire()
        self.telephony = PipeWireTelephony(self.bus.call, self.bus.subscribe)

    def test_reload_maps_gateways_to_addresses_and_calls(self):
        gateway = self.bus.connect(1, PHONE)
        self.bus.add_call(gateway, 1, 'incoming')
        self.bus.gateways[f'{TELEPHONY_ROOT}/ag9'] = {'address': 'not-an-address', 'calls': {}, 'transport': {}}
        self.telephony.start()
        snapshot = self.telephony.snapshot()
        self.assertEqual([g.address for g in snapshot], [PHONE])
        self.assertEqual(snapshot[0].reject_sco, True)
        self.assertEqual([(c.path, c.state, c.line) for c in snapshot[0].calls], [(f'{gateway}/call1', 'incoming', NUMBER)])
        self.assertNotIn(NUMBER, repr(snapshot))
        self.assertTrue(self.telephony.call_active())

    def test_signals_follow_gateways_calls_and_transport(self):
        self.telephony.start()
        changes = []
        self.telephony.on_change(lambda: changes.append(1))
        gateway = self.bus.connect(1, PHONE.lower())
        call = self.bus.add_call(gateway, 1, 'dialing')
        self.bus.call_state(call, 'alerting')
        self.bus.transport_state(gateway, 'pending')
        self.assertEqual(self.telephony.gateway(PHONE).calls[0].state, 'alerting')
        self.assertEqual(self.telephony.gateway(PHONE).transport, 'pending')
        self.bus.remove_call(call)
        self.assertEqual(self.telephony.gateway(PHONE).calls, ())
        self.bus.disconnect(gateway)
        self.assertIsNone(self.telephony.gateway(PHONE))
        self.assertEqual(len(changes), 6)
        # A call announced by some other path is not attached to this gateway.
        self.bus.connect(1, PHONE)
        self.telephony._interfaces_added(TELEPHONY_ROOT, (f'{TELEPHONY_ROOT}/ag1/call3', {CALL_INTERFACE: {'State': 'active'}}))
        self.assertEqual(self.telephony.gateway(PHONE).calls, ())

    def test_dial_and_tones_are_validated_before_the_bus(self):
        self.bus.connect(1, PHONE)
        self.telephony.start()
        for bad in ('', '1' * 81, '555 1234', '12a', '+1;ATH', None, 5551234):
            with self.assertRaises(ValueError) as caught:
                self.telephony.dial(PHONE, bad)
            self.assertNotIn('555', str(caught.exception))
        self.telephony.dial(PHONE, '*31#+1,2ABCD')
        self.assertEqual(self.bus.invoked('Dial')[-1][3], ('*31#+1,2ABCD',))
        with self.assertRaises(ValueError): self.telephony.send_tones(PHONE, '+')
        self.telephony.send_tones(PHONE, '12#')
        with self.assertRaises(LookupError): self.telephony.dial(OTHER, '123')
        self.assertEqual(len(self.bus.invoked('Dial')), 1)

    def test_reject_sco_write_updates_the_model_without_a_signal(self):
        gateway = self.bus.connect(1, PHONE)
        self.telephony.start()
        self.telephony.set_reject_sco(PHONE, False)
        self.assertFalse(self.bus.reject(gateway))
        self.assertFalse(self.telephony.gateway(PHONE).reject_sco)
        self.bus.gateways[gateway]['transport']['RejectSCO'] = True
        self.assertTrue(self.telephony.read_reject_sco(PHONE))
        with self.assertRaises(ValueError): self.telephony.set_reject_sco(PHONE, 0)

    def test_unavailable_service_and_unknown_calls(self):
        self.bus.fail[(OBJECT_MANAGER, 'GetManagedObjects')] = DBusError('ServiceUnknown')
        self.telephony.start()
        self.assertFalse(self.telephony.available)
        del self.bus.fail[(OBJECT_MANAGER, 'GetManagedObjects')]
        self.bus.connect(1, PHONE)
        self.telephony.reload()
        self.assertTrue(self.telephony.available)
        with self.assertRaises(LookupError): self.telephony.answer(f'{TELEPHONY_ROOT}/ag1/call7')
        with self.assertRaises(LookupError): self.telephony.hangup('/org/example')
        self.telephony.vanished()
        self.assertEqual(self.telephony.snapshot(), ())


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self.bus = FakePipeWire()
        self.telephony = PipeWireTelephony(self.bus.call, self.bus.subscribe)
        self.telephony.start()
        self.clock = [1_700_000_000]
        self.authorized = [True]
        self.selected = [PHONE]
        self.published, self.incoming, self.mutes = [], [], []
        self.provider = BluetoothCallProvider(
            self.telephony, address=lambda: self.selected[0], authorized=lambda: self.authorized[0],
            now=lambda: self.clock[0], mute=self.mutes.append, on_incoming=lambda: self.incoming.append(1))
        self.provider.start(lambda snapshot, ready: self.published.append((snapshot, ready)))

    def ringing(self, state='incoming'):
        gateway = self.bus.connect(1, PHONE)
        call = self.bus.add_call(gateway, 1, state)
        return gateway, call, self.provider.calls()[0].call_id

    def test_state_mapping_records_and_times(self):
        gateway = self.bus.connect(1, PHONE)
        expected = {'incoming': CallPhase.INCOMING, 'dialing': CallPhase.DIALLING, 'alerting': CallPhase.RINGING_OUTGOING,
                    'active': CallPhase.ACTIVE, 'held': CallPhase.HELD, 'waiting': CallPhase.WAITING,
                    'disconnected': CallPhase.ENDED}
        for index, state in enumerate(expected, 1):
            self.bus.add_call(gateway, index, state)
        calls = {call.call_id.split(':', 1)[1].rsplit('call', 1)[1]: call for call in self.provider.calls()}
        for index, (state, phase) in enumerate(expected.items(), 1):
            call = calls[str(index)]
            self.assertIsInstance(call, NativeCall)
            self.assertIs(call.phase, phase)
            self.assertEqual(call.transport, 'bluetooth')
            self.assertEqual(call.direction, 'incoming' if state in ('incoming', 'waiting') else 'outgoing')
            self.assertEqual(call.address, NUMBER)
            self.assertEqual(call.started_at, self.clock[0])
            self.assertEqual(call.answered_at, self.clock[0] if state == 'active' else 0)
        session = CallSession.from_native(calls['4'])
        self.assertEqual(session.connected_at, self.clock[0])
        self.assertEqual(self.published[-1], ({'calls': [c.call_id for c in self.provider.calls()], 'dial_token': None,
                                               'voice_available': True}, True))

    def test_answer_time_is_observed_and_withheld_numbers_are_blank(self):
        gateway, call, call_id = self.ringing()
        self.bus.add_call(gateway, 2, 'waiting', line='withheld')
        self.clock[0] += 12
        self.bus.call_state(call, 'active')
        rows = {c.call_id: c for c in self.provider.calls()}
        self.assertEqual(rows[call_id].answered_at, self.clock[0])
        self.assertEqual(rows[call_id].started_at, self.clock[0] - 12)
        self.assertEqual([c.address for c in rows.values() if c.phase is CallPhase.WAITING], [''])
        self.assertEqual(self.incoming, [1])

    def test_generation_changes_with_each_connection(self):
        gateway, call, first = self.ringing()
        self.assertTrue(first.startswith(f'{self.provider.generation}:{call}'))
        self.bus.disconnect(gateway)
        self.assertEqual(self.provider.calls(), ())
        self.assertFalse(self.published[-1][0]['voice_available'])
        self.bus.connect(1, PHONE)
        self.bus.add_call(gateway, 1, 'incoming')
        second = self.provider.calls()[0].call_id
        self.assertNotEqual(first, second)
        self.assertEqual(second.split(':', 1)[1], call)  # PipeWire reused the object path
        with self.assertRaises(PermissionError): self.provider.accept(first)
        self.assertEqual(self.bus.invoked('Answer'), [])
        with self.assertRaises(PermissionError): self.provider.hangup('garbage')

    def test_answer_decline_and_hangup(self):
        gateway, call, call_id = self.ringing()
        with self.assertRaises(PermissionError): self.provider.start_audio(call_id)
        self.provider.accept(call_id)
        methods = [m[2] for m in self.bus.methods if m[2] in ('Set', 'Answer')]
        self.assertEqual(methods[-2:], ['Set', 'Answer'])
        self.assertFalse(self.bus.reject(gateway))
        self.bus.call_state(call, 'active')
        with self.assertRaises(PermissionError): self.provider.decline(call_id)
        self.provider.hangup(call_id)
        self.assertEqual(self.bus.invoked('Hangup')[-1][0], call)
        waiting = self.bus.add_call(gateway, 2, 'waiting')
        waiting_id = next(c.call_id for c in self.provider.calls() if c.call_id.endswith(waiting))
        self.provider.accept(waiting_id)
        self.assertEqual(len(self.bus.invoked('HoldAndAnswer')), 1)
        self.provider.decline(waiting_id)
        self.assertEqual(self.bus.invoked('Hangup')[-1][0], waiting)

    def test_dial_normalizes_and_refuses_during_a_call(self):
        gateway = self.bus.connect(1, PHONE)
        self.assertEqual(self.provider.dial('+1 (555) 123-4567'), '')
        self.assertEqual(self.bus.invoked('Dial')[-1][3], (NUMBER,))
        self.assertTrue(self.bus.reject(gateway), 'dialling does not move audio to the computer')
        for bad in ('call me', '555;ATH', '1' * 90):
            with self.assertRaises(ValueError): self.provider.dial(bad)
        self.bus.add_call(gateway, 1, 'dialing')
        with self.assertRaises(PermissionError): self.provider.dial('123')
        self.assertEqual(normalize_number('*#06#'), '*#06#')
        self.authorized[0] = False
        self.provider.invalidate()
        with self.assertRaises(PermissionError): self.provider.dial('123')
        self.assertFalse(self.provider.control_authorized())

    def test_reject_sco_consent_follows_the_call(self):
        bus = FakePipeWire(default_reject=False)  # even if the drop-in default were missing
        telephony = PipeWireTelephony(bus.call, bus.subscribe)
        telephony.start()
        provider = BluetoothCallProvider(telephony, address=lambda: PHONE, authorized=lambda: True, mute=self.mutes.append)
        provider.start(lambda *_: None)
        gateway = bus.connect(1, PHONE)
        self.assertTrue(bus.reject(gateway), 'a new connection starts closed')
        call = bus.add_call(gateway, 1, 'active')
        call_id = provider.calls()[0].call_id
        self.assertEqual(provider.audio_state(), {'status': 'phone', 'muted': False})
        provider.start_audio(call_id)
        self.assertFalse(bus.reject(gateway))
        self.assertEqual(len(bus.invoked('Activate')), 1)
        self.assertEqual(provider.audio_state()['status'], 'connecting')
        bus.transport_state(gateway, 'active')
        self.assertFalse(bus.reject(gateway), 'consent holds while the call is live')
        self.assertEqual(provider.audio_state()['status'], 'connected')
        provider.mute_audio(True)
        self.assertEqual(provider.audio_state(), {'status': 'connected', 'muted': True})
        bus.call_state(call, 'disconnected')
        self.assertTrue(bus.reject(gateway), 'end of call restores RejectSCO')
        self.assertEqual(self.mutes, [True, False])
        bus.remove_call(call)
        # Explicit return to the phone.
        call = bus.add_call(gateway, 2, 'active')
        provider.start_audio(provider.calls()[0].call_id)
        provider.stop_audio()
        self.assertTrue(bus.reject(gateway))
        # Disconnect during computer audio, then reconnect: closed again.
        provider.start_audio(provider.calls()[0].call_id)
        bus.gateways[gateway]['transport']['RejectSCO'] = False
        bus.disconnect(gateway)
        gateway = bus.connect(1, PHONE)
        self.assertTrue(bus.reject(gateway))
        provider.close()

    def test_activate_failure_restores_and_close_restores(self):
        gateway, call, call_id = self.ringing('active')
        self.bus.fail[(TRANSPORT_INTERFACE, 'Activate')] = DBusError('NotSupported')
        with self.assertRaises(PermissionError): self.provider.start_audio(call_id)
        self.assertTrue(self.bus.reject(gateway))
        del self.bus.fail[(TRANSPORT_INTERFACE, 'Activate')]
        self.provider.start_audio(call_id)
        self.assertFalse(self.bus.reject(gateway))
        self.provider.close()
        self.assertTrue(self.bus.reject(gateway))
        self.assertTrue(self.provider.closed)

    def test_revocation_restores_every_gateway(self):
        gateway, call, call_id = self.ringing('active')
        self.provider.start_audio(call_id)
        self.assertFalse(self.bus.reject(gateway))
        self.authorized[0] = False
        self.provider.invalidate()
        self.assertTrue(self.bus.reject(gateway))
        self.assertEqual(self.provider.calls(), ())
        self.assertEqual(self.published[-1], ({'calls': [], 'dial_token': None, 'voice_available': False}, False))
        with self.assertRaises(PermissionError): self.provider.start_audio(call_id)

    def test_call_end_restores_even_when_another_process_consented(self):
        gateway, call, call_id = self.ringing('active')
        self.bus.gateways[gateway]['transport']['RejectSCO'] = False  # set by Phone; no signal reaches this process
        self.bus.call_state(call, 'disconnected')
        self.assertTrue(self.bus.reject(gateway))

    def test_other_phones_stay_closed_and_hidden(self):
        mine = self.bus.connect(1, PHONE)
        self.bus.default_reject = False  # e.g. WirePlumber started without Luma's drop-in default
        theirs = self.bus.connect(2, OTHER)
        self.assertTrue(self.bus.reject(theirs))
        foreign = self.bus.add_call(theirs, 1, 'incoming')
        self.assertEqual(self.provider.calls(), ())
        self.assertEqual(self.incoming, [])
        with self.assertRaises(PermissionError): self.provider.accept(f'{self.provider.generation}:{foreign}')
        self.bus.add_call(mine, 1, 'incoming')
        self.assertEqual(len(self.provider.calls()), 1)
        self.assertEqual(self.bus.invoked('Answer'), [])
        self.selected[0] = None
        self.provider.invalidate()
        self.assertEqual(self.provider.calls(), ())
        self.assertTrue(self.bus.reject(mine))

    def test_numbers_and_names_never_reach_logs(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter('%(levelname)s %(name)s %(message)s'))
        logger = logging.getLogger('luma_continuity')
        previous = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            gateway, call, call_id = self.ringing()
            self.provider.accept(call_id)
            self.bus.call_state(call, 'active')
            self.bus.emit(call, PROPERTIES, 'PropertiesChanged', CALL_INTERFACE, {'LineIdentification': '+15559998888'}, [])
            self.provider.hangup(call_id)
            self.bus.call_state(call, 'disconnected')
            self.bus.remove_call(call)
            self.bus.fail[(AG_INTERFACE, 'Dial')] = DBusError('org.pipewire.Telephony.Error.CME')
            with self.assertRaises(DBusError): self.provider.dial(NUMBER)
            with self.assertRaises(ValueError): self.provider.dial('555-0100 x;')
            self.bus.fail[(PROPERTIES, 'Set')] = DBusError('Failed')
            self.bus.disconnect(gateway)
            self.bus.connect(1, PHONE)
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        output = stream.getvalue()
        self.assertIn('Telephony call', output)  # logging did happen
        for secret in (NUMBER, '15551234567', '5559998888', '555-0100', 'Alex Example'):
            self.assertNotIn(secret, output)


class FakeBlueZ:
    """org.bluez on the system bus: one adapter and its devices (doc/org.bluez.Adapter.rst, Device.rst)."""

    ADAPTER = '/org/bluez/hci0'

    def __init__(self):
        self.adapter = {'Address': DESKTOP, 'Powered': True, 'Pairable': True, 'PairableTimeout': 0,
                        'Discoverable': False, 'DiscoverableTimeout': 180}
        self.devices = {}
        self.writes = []
        self.removed = []

    def add(self, suffix, address, *, bonded=True, device_class=0x5a020c):
        path = f'{self.ADAPTER}/dev_{suffix}'
        self.devices[path] = {'Address': address, 'Adapter': self.ADAPTER, 'Paired': bonded, 'Bonded': bonded,
                              'Trusted': False, 'Class': device_class}
        return path

    def call(self, path, interface, method, signature, values):
        if method == 'GetManagedObjects':
            objects = {'/org/bluez': {'org.bluez.AgentManager1': {}}, self.ADAPTER: {'org.bluez.Adapter1': dict(self.adapter)}}
            objects.update({p: {'org.bluez.Device1': dict(d)} for p, d in self.devices.items()})
            return (objects,)
        if method == 'Get':
            return (self.adapter[values[1]],)
        if method == 'Set':
            self.writes.append((path, values[1], values[2].value))
            target = self.adapter if path == self.ADAPTER else self.devices[path]
            target[values[1]] = values[2].value
            return ()
        if method == 'RemoveDevice':
            self.removed.append(values[0])
            del self.devices[values[0]]
            return ()
        raise AssertionError(method)


class BondingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bluez = FakeBlueZ()
        self.bluez.add('OLD', 'AA:AA:AA:AA:AA:AA')  # bonded before the window: never a candidate
        self.clock = [0.0]
        self.requests = []
        self.events = {}  # poll number -> callable
        self.polls = 0
        self.receipt = {'state': 'complete', 'result': {'error': 'needs-user'}}

    def call(self, directory, fingerprint, capability, payload, *, lifetime):
        self.requests.append((fingerprint, capability, payload, lifetime))
        self.assertTrue(self.bluez.adapter['Discoverable'] and self.bluez.adapter['Pairable'])
        return self.receipt

    def sleep(self, seconds):
        self.clock[0] += seconds
        self.polls += 1
        if self.polls in self.events: self.events[self.polls]()

    def bonding(self):
        return BluetoothBonding(BlueZ(self.bluez.call), self.temp.name, call=self.call,
                                now=lambda: self.clock[0], sleep=self.sleep)

    def assert_restored(self):
        self.assertEqual({k: self.bluez.adapter[k] for k in ('Pairable', 'PairableTimeout', 'Discoverable', 'DiscoverableTimeout')},
                         {'Pairable': True, 'PairableTimeout': 0, 'Discoverable': False, 'DiscoverableTimeout': 180})

    def test_exactly_one_new_bond_is_accepted_and_stored_privately(self):
        self.events[3] = lambda: self.bluez.add('NEW', PHONE)
        bonding = self.bonding()
        self.assertEqual(bonding.desktop_address(), DESKTOP)
        self.assertEqual(bonding.begin(FINGERPRINT, name='Nick’s Luma'), 'bonded')
        self.assertEqual(self.requests, [(FINGERPRINT, 'bluetooth.bond', {'address': DESKTOP, 'name': 'Nick’s Luma'}, 120)])
        self.assertIn((FakeBlueZ.ADAPTER, 'DiscoverableTimeout', 120), self.bluez.writes)
        self.assert_restored()
        self.assertTrue(self.bluez.devices[f'{FakeBlueZ.ADAPTER}/dev_NEW']['Trusted'])
        path = Path(self.temp.name) / BONDS_FILE
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(bonding.remembered(FINGERPRINT), PHONE)
        self.assertNotIn(PHONE, (Path(self.temp.name) / 'continuity.db').read_text() if (Path(self.temp.name) / 'continuity.db').exists() else '')
        self.assertTrue(bonding.forget(FINGERPRINT))
        self.assertEqual(self.bluez.removed, [f'{FakeBlueZ.ADAPTER}/dev_NEW'])
        self.assertIsNone(bonding.remembered(FINGERPRINT))
        self.assertFalse(bonding.forget(FINGERPRINT))

    def test_timeout_without_a_bond(self):
        self.assertEqual(self.bonding().begin(FINGERPRINT, name='Desk'), 'not-found')
        self.assertGreaterEqual(self.clock[0], BluetoothBonding.WINDOW)
        self.assert_restored()
        self.assertFalse((Path(self.temp.name) / BONDS_FILE).exists())

    def test_a_bond_after_the_window_is_not_accepted(self):
        self.events[BluetoothBonding.WINDOW // BluetoothBonding.INTERVAL + 2] = lambda: self.bluez.add('LATE', PHONE)
        self.assertEqual(self.bonding().begin(FINGERPRINT, name='Desk'), 'not-found')

    def test_two_new_bonds_are_refused(self):
        def both():
            self.bluez.add('NEW', PHONE)
            self.bluez.add('EAR', OTHER, device_class=0x240404)
        self.events[2] = both
        with self.assertLogs('luma_continuity.companion_calls', 'WARNING'):
            self.assertEqual(self.bonding().begin(FINGERPRINT, name='Desk'), 'ambiguous')
        self.assert_restored()
        self.assertIsNone(self.bonding().remembered(FINGERPRINT))

    def test_second_bond_during_confirmation_is_refused(self):
        self.events[2] = lambda: self.bluez.add('NEW', PHONE)
        self.events[3] = lambda: self.bluez.add('EAR', OTHER)
        # The first sighting is confirmed on the next poll, which already sees two.
        with self.assertLogs('luma_continuity.companion_calls', 'WARNING'):
            self.assertEqual(self.bonding().begin(FINGERPRINT, name='Desk'), 'ambiguous')

    def test_phone_must_acknowledge_and_the_device_must_be_a_phone(self):
        self.receipt = {'state': 'complete', 'result': {'error': 'unavailable'}}
        self.events[1] = lambda: self.bluez.add('NEW', PHONE)
        self.assertEqual(self.bonding().begin(FINGERPRINT, name='Desk'), 'refused')
        self.assertEqual(self.polls, 0)
        self.assert_restored()
        self.receipt = {'state': 'complete', 'result': {'error': 'needs-user'}}
        self.bluez.devices.clear()
        self.polls = 0
        self.events = {1: lambda: self.bluez.add('EAR', OTHER, device_class=0x240404)}
        self.assertEqual(self.bonding().begin(FINGERPRINT, name='Desk'), 'not-a-phone')
        with self.assertRaises(ValueError): self.bonding().begin('nothex', name='Desk')
        with self.assertRaises(ValueError): self.bonding().begin(FINGERPRINT, name='bad\nname')

    def test_adapter_is_restored_when_the_request_fails(self):
        def fail(*_args, **_kwargs): raise ConnectionError('phone unreachable')
        bonding = BluetoothBonding(BlueZ(self.bluez.call), self.temp.name, call=fail, now=lambda: 0, sleep=self.sleep)
        with self.assertRaises(ConnectionError): bonding.begin(FINGERPRINT, name='Desk')
        self.assert_restored()


class WirePlumberTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.restarts, self.active = [], [False]
        self.roles = WirePlumberRoles(lambda: self.restarts.append(1), call_active=lambda: self.active[0],
                                      directory=Path(self.temp.name) / 'wireplumber.conf.d')

    def test_drop_in_content(self):
        text = WirePlumberRoles.content(PHONE)
        roles = 'override.bluez5.roles = [ ' + ' '.join(ROLES_WITH_HANDS_FREE) + ' ]'
        self.assertIn(roles, text)
        for role in ('hfp_hf', 'hfp_ag', 'a2dp_sink', 'a2dp_source', 'bap_sink', 'bap_source', 'asha_sink'):
            self.assertIn(role, ROLES_WITH_HANDS_FREE)
        self.assertIn('bluez5.telephony-dbus-service = true', text)
        self.assertIn('bluez5.telephony.default-reject-sco = true', text)
        self.assertIn(f'matches = [ {{ api.bluez5.address = "{PHONE}" }} ]', text)
        self.assertIn('bluez5.auto-connect = [ ]', text)
        self.assertNotIn('bluez5.codecs', text)
        before_bonding = WirePlumberRoles.content()
        self.assertIn(roles, before_bonding)
        self.assertNotIn('monitor.bluez.rules', before_bonding)
        for bad in ('aa:bb:cc:dd:ee:ff', 'AA:BB:CC:DD:EE:FF" } ] } ]\nfoo = [', 5):
            with self.assertRaises(ValueError): WirePlumberRoles.content(bad)

    def test_enable_writes_atomically_and_restarts_once(self):
        self.assertTrue(self.roles.enable(PHONE))
        path = self.roles.path
        self.assertEqual(path.name, WIREPLUMBER_DROP_IN)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertEqual(path.read_text(), WirePlumberRoles.content(PHONE))
        self.assertEqual(os.listdir(path.parent), [WIREPLUMBER_DROP_IN])
        self.assertFalse(self.roles.enable(PHONE))
        self.assertEqual(self.restarts, [1])
        self.assertTrue(self.roles.disable())
        self.assertFalse(path.exists())
        self.assertFalse(self.roles.disable())
        self.assertEqual(self.restarts, [1, 1])

    def test_refuses_to_restart_during_a_call(self):
        self.active[0] = True
        with self.assertRaises(CallActive): self.roles.enable(PHONE)
        self.assertFalse(self.roles.path.exists())
        self.active[0] = False
        self.roles.enable(PHONE)
        self.active[0] = True
        with self.assertRaises(CallActive): self.roles.enable(OTHER)
        with self.assertRaises(CallActive): self.roles.disable()
        self.assertEqual(self.roles.path.read_text(), WirePlumberRoles.content(PHONE))
        self.assertEqual(self.restarts, [1])

    def test_call_active_comes_from_every_gateway(self):
        bus = FakePipeWire()
        telephony = PipeWireTelephony(bus.call, bus.subscribe)
        telephony.start()
        roles = WirePlumberRoles(lambda: self.restarts.append(1), call_active=telephony.call_active,
                                 directory=Path(self.temp.name) / 'd')
        gateway = bus.connect(3, OTHER)
        bus.add_call(gateway, 1, 'held')
        with self.assertRaises(CallActive): roles.enable(PHONE)

    def test_systemd_restart_call(self):
        calls = []
        WirePlumberRoles.systemd_restart(lambda *args: calls.append(args))()
        self.assertEqual(calls, [('/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager', 'RestartUnit', '(ss)',
                                  ('wireplumber.service', 'replace'))])


if __name__ == '__main__':
    unittest.main()
