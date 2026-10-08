"""Desktop owners for companion effects (ADR-021, decision 8).

Each class hands one validated companion effect to the component that owns it
on the desktop: notifications to `org.freedesktop.Notifications`, the clipboard
to Luma's GNOME Shell interface, media to MPRIS, discovery to Avahi, pointer
and keyboard input to Mutter's remote desktop session, and sound to GSound.
Nothing here opens a link, launches a file or changes a grant without an
explicit user action. D-Bus callers are injected, so the classes are tested
with recorders; the `gio` constructors import GObject lazily and are the only
parts that need a running session.

`CompanionService` joins the pieces for the Connect daemon: pairing sessions,
the companion listener, the allowlisted desktop-to-phone calls and chunked
file sending. It is GLib-free; the daemon supplies a main-loop `dispatch` and
worker `submit` functions.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import secrets
import stat
import threading
import time
from urllib.parse import urlparse

from . import companion
from .policy import IDENTIFIER
from .transport import _unique

LOG = logging.getLogger(__name__)
APP_ID = 'org.projectluma.Connect'
APP_NAME = 'Luma Connect'

# What a phone may be allowed to do on this computer, and what this computer may
# ask of a phone, as offered by the pairing screen. Media streams are not offered
# until the PipeWire camera owner exists.
PHONE_TO_DESKTOP = frozenset({'device.status', 'device.ring', 'clipboard.write', 'clipboard.read', 'links.open',
                              'files.write', 'notifications.mirror', 'media.mirror', 'input.control', 'dnd.set',
                              'auth.response', 'device.rotate'})
DESKTOP_TO_PHONE = frozenset({'device.ring', 'clipboard.write', 'links.open', 'files.write', 'notifications.act',
                              'media.control', 'dnd.set', 'hotspot.request', 'auth.request', 'input.control',
                              'camera.stream', 'screen.view', 'screen.control', 'messages.read', 'messages.send',
                              'bluetooth.bond', 'device.rotate'})
from .companion_contract import DEFAULT_PHONE_TO_DESKTOP, DEFAULT_DESKTOP_TO_PHONE
# CompanionInvoke is not a generic forwarder: files use CompanionSendFile, and
# anything else needs its own reviewed method.
INVOKE_CAPABILITIES = frozenset({'device.ring', 'clipboard.write', 'links.open', 'notifications.act', 'media.control'})
MAX_INVOKE_PAYLOAD = 64 * 1024
FILE_CHUNK = 512 * 1024
REPLY_TTL = 300


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------- D-Bus plumbing

class BusService:
    """Calls one well-known service on its current unique owner.

    Like `notifications.NativeNotifications.gio_caller`, requests go to the
    unique name so a later owner of the well-known name cannot receive them.
    Signal subscriptions are bound to that unique owner and renewed when it
    changes. Create on the main thread: watch callbacks use its context.
    """

    def __init__(self, bus_type, name, path, interface, *, signals=(), on_owner=None, timeout=5000):
        from gi.repository import Gio
        self.Gio = Gio
        self.connection = Gio.bus_get_sync(bus_type, None)
        self.name, self.path, self.interface, self.timeout = name, path, interface, timeout
        self.signals, self.on_owner = tuple(signals), on_owner
        self.owner, self._subscriptions = None, []
        self._lock = threading.Lock()
        self._watch = Gio.bus_watch_name_on_connection(self.connection, name, Gio.BusNameWatcherFlags.NONE,
                                                       self._appeared, self._vanished)

    def _appeared(self, _connection, _name, owner):
        with self._lock: self.owner = owner
        self._unsubscribe()
        for member, handler in self.signals:
            self._subscriptions.append(self.connection.signal_subscribe(
                owner, self.interface, member, self.path, None, self.Gio.DBusSignalFlags.NONE,
                lambda *args, handler=handler: handler(*args[-1].unpack())))
        if self.on_owner: self.on_owner(owner)

    def _vanished(self, *_):
        with self._lock: self.owner = None
        self._unsubscribe()
        if self.on_owner: self.on_owner(None)

    def _unsubscribe(self):
        for subscription in self._subscriptions: self.connection.signal_unsubscribe(subscription)
        self._subscriptions = []

    def resolve(self):
        from gi.repository import GLib
        with self._lock: owner = self.owner
        if owner: return owner
        try:
            owner = self.connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                                              'GetNameOwner', GLib.Variant('(s)', (self.name,)), None,
                                              self.Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        except GLib.Error:
            raise LookupError(f'{self.name} is not running') from None
        if not isinstance(owner, str) or not owner.startswith(':'): raise LookupError('unique owner required')
        return owner

    def __call__(self, method, signature, values, *, path=None, interface=None):
        from gi.repository import GLib
        parameters = GLib.Variant(signature, values) if signature != '()' else None
        result = self.connection.call_sync(self.resolve(), path or self.path, interface or self.interface, method,
                                           parameters, None, self.Gio.DBusCallFlags.NO_AUTO_START, self.timeout, None)
        return result.unpack() if result is not None else ()

    def close(self):
        self._unsubscribe()
        self.Gio.bus_unwatch_name(self._watch)


def _escape(text):
    # GNOME Shell parses a small markup subset in notification bodies.
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# --------------------------------------------------------------------------- notifications

class FreedesktopNotifier:
    """Presents mirrored notifications, links and received files.

    `caller(method, signature, values)` calls org.freedesktop.Notifications.
    `act(peer, payload)` asks the phone to run `notifications.act`; `reply(peer,
    request)` asks the Luma Connect app for a reply prompt (the notification
    specification has no inline reply); `launch(uri)` and `show_file(path)` run
    only after the person clicks.
    """

    BUS, PATH, INTERFACE = 'org.freedesktop.Notifications', '/org/freedesktop/Notifications', 'org.freedesktop.Notifications'
    DISMISSED = 2
    LIMIT = 128

    def __init__(self, caller, *, act, reply, launch, show_file, can_act=lambda peer: True,
                 icon='phone-symbolic'):
        self.caller, self.act, self.reply = caller, act, reply
        self.launch, self.show_file, self.can_act, self.icon = launch, show_file, can_act, icon
        self._ids = OrderedDict()   # (peer, key) -> notification id
        self._records = {}          # notification id -> record
        self._lock = threading.Lock()

    def __call__(self, kind, peer, value):
        if kind == 'notification':
            op = value['op']
            if op == 'post': self.post(peer, value)
            elif op == 'remove': self.remove(peer, value['key'])
            else: self.forget(peer)
        elif kind == 'link':
            self._show(peer, ('link', value['url']), f"Link from {value['device']}", value.get('title') or value['url'],
                       ['default', 'Open', 'open', 'Open'], {'kind': 'link', 'url': value['url']})
        elif kind == 'file':
            self._show(peer, ('file', value['path']), f"{value['device']} sent a file", value['name'],
                       ['default', 'Open', 'open', 'Open', 'show', 'Show in Files'], {'kind': 'file', 'path': value['path']})

    def post(self, peer, value):
        actions, mapping = [], {}
        actionable = bool(self.can_act(peer))
        if actionable:
            actions += ['default', 'Open']
            for action in value['actions']:
                mapping[action['id']] = action
                actions += [action['id'], action['label']]
        body = value['text']
        if value['conversation']:
            body = '\n'.join(f"{message['sender']}: {message['text']}" for message in value['conversation'][-3:])
        summary = value['title'] or value['app']
        body = f"{body}\n{value['app']} on {value['device']}" if body else f"{value['app']} on {value['device']}"
        self._show(peer, value['key'], summary, body, actions,
                   {'kind': 'notification', 'key': value['key'], 'actions': mapping, 'title': summary,
                    'app': value['app'], 'device': value['device'], 'actionable': actionable}, silent=value['silent'])

    def _show(self, peer, key, summary, body, actions, record, *, silent=False):
        values = {'desktop-entry': ('s', APP_ID), 'suppress-sound': ('b', bool(silent))}
        hints = self._hints(values)
        with self._lock:
            replaces = self._ids.get((peer, key), 0)
            identity, = self.caller('Notify', '(susssasa{sv}i)',
                                    (APP_NAME, replaces, self.icon, summary[:512], _escape(body[:4096]), actions, hints, -1))
            if replaces and replaces != identity: self._records.pop(replaces, None)
            self._ids[(peer, key)] = identity
            self._ids.move_to_end((peer, key))
            self._records[identity] = {**record, 'peer': peer, 'id_key': key}
            while len(self._ids) > self.LIMIT:
                (_, oldest) = self._ids.popitem(last=False)
                self._records.pop(oldest, None)
                self._close(oldest)
        return identity

    @staticmethod
    def _hints(values):
        try:
            from gi.repository import GLib
        except ImportError:
            return {name: value for name, (_, value) in values.items()}
        return {name: GLib.Variant(signature, value) for name, (signature, value) in values.items()}

    def _close(self, identity):
        try: self.caller('CloseNotification', '(u)', (identity,))
        except Exception: pass  # already closed by the person or the Shell

    def remove(self, peer, key):
        with self._lock:
            identity = self._ids.pop((peer, key), None)
            if identity is None: return
            self._records.pop(identity, None)
        self._close(identity)

    def forget(self, peer):
        with self._lock:
            identities = [identity for (owner, _), identity in self._ids.items() if owner == peer]
            for key in [key for key in self._ids if key[0] == peer]: del self._ids[key]
            for identity in identities: self._records.pop(identity, None)
        for identity in identities: self._close(identity)

    def _take(self, identity):
        with self._lock:
            record = self._records.pop(identity, None)
            if record: self._ids.pop((record['peer'], record['id_key']), None)
        return record

    def action_invoked(self, identity, action):
        # Invoking an action closes the Shell notification with reason 2; the
        # record is taken first, so that close is not mistaken for a dismissal.
        record = self._take(identity)
        if record is None: return
        peer = record['peer']
        if record['kind'] == 'notification':
            if not record['actionable']: return
            if action == 'default':
                self.act(peer, {'key': record['key'], 'action': 'open'})
            elif action in record['actions']:
                chosen = record['actions'][action]
                if chosen['reply']:
                    self.reply(peer, {'key': record['key'], 'action': action, 'label': chosen['label'],
                                      'title': record['title'], 'app': record['app'], 'device': record['device']})
                else:
                    self.act(peer, {'key': record['key'], 'action': action})
        elif record['kind'] == 'link' and action in {'default', 'open'}:
            self.launch(record['url'])
        elif record['kind'] == 'file':
            if action in {'default', 'open'}: self.launch(Path(record['path']).as_uri())
            elif action == 'show': self.show_file(record['path'])

    def notification_closed(self, identity, reason):
        record = self._take(identity)
        if record and record['kind'] == 'notification' and record['actionable'] and reason == self.DISMISSED:
            self.act(record['peer'], {'key': record['key'], 'action': 'dismiss'})

    def owner_changed(self, _owner):
        with self._lock:
            self._ids.clear(); self._records.clear()

    @classmethod
    def gio(cls, **callbacks):
        from gi.repository import Gio
        holder = []
        service = BusService(Gio.BusType.SESSION, cls.BUS, cls.PATH, cls.INTERFACE, signals=(
            ('ActionInvoked', lambda identity, action: holder[0].action_invoked(identity, action)),
            ('NotificationClosed', lambda identity, reason: holder[0].notification_closed(identity, reason))),
            on_owner=lambda owner: holder and holder[0].owner_changed(owner))
        notifier = cls(service, **callbacks)
        notifier.service = service
        holder.append(notifier)
        return notifier


def gio_launch(uri):
    from gi.repository import Gio, GLib
    def launch():
        try: Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error: LOG.warning('No application could open a companion link or file')
        return False
    GLib.idle_add(launch)


def gio_show_file(path):
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.call('org.freedesktop.FileManager1', '/org/freedesktop/FileManager1', 'org.freedesktop.FileManager1',
             'ShowItems', GLib.Variant('(ass)', ([Path(path).as_uri()], '')), None, Gio.DBusCallFlags.NONE, 5000,
             None, None)


# --------------------------------------------------------------------------- clipboard

class ShellClipboard:
    """Client of Luma's GNOME Shell clipboard owner.

    ABI (implemented by the Shell patch): bus org.gnome.Shell, object
    /org/projectluma/Connect/Clipboard, interface org.projectluma.Connect.Clipboard1,
    `SetText(s text, b sensitive) -> ()` and `GetText() -> (b available, s text)`.
    """

    BUS, PATH, INTERFACE = 'org.gnome.Shell', '/org/projectluma/Connect/Clipboard', 'org.projectluma.Connect.Clipboard1'
    LIMIT = 262144  # UTF-8 bytes, the Shell's bound for both methods

    def __init__(self, caller):
        self.caller = caller

    def set_text(self, text, sensitive):
        if not isinstance(text, str) or len(text.encode('utf-8', 'surrogatepass')) > self.LIMIT or type(sensitive) is not bool:
            raise ValueError('invalid clipboard text')
        self.caller('SetText', '(sb)', (text, sensitive))

    def get_text(self):
        # Locked, Busy, AccessDenied or no Shell: the phone is told nothing is available.
        try: available, text = self.caller('GetText', '()', ())
        except Exception: return None
        if type(available) is not bool or not isinstance(text, str): raise ValueError('invalid clipboard reply')
        return text if available else None

    @classmethod
    def gio(cls):
        from gi.repository import Gio
        return cls(BusService(Gio.BusType.SESSION, cls.BUS, cls.PATH, cls.INTERFACE))


# --------------------------------------------------------------------------- MPRIS

MPRIS_PATH = '/org/mpris/MediaPlayer2'
MPRIS_XML = '''<node>
 <interface name="org.mpris.MediaPlayer2">
  <method name="Raise"/><method name="Quit"/>
  <property name="CanQuit" type="b" access="read"/><property name="CanRaise" type="b" access="read"/>
  <property name="HasTrackList" type="b" access="read"/><property name="Identity" type="s" access="read"/>
  <property name="DesktopEntry" type="s" access="read"/>
  <property name="SupportedUriSchemes" type="as" access="read"/><property name="SupportedMimeTypes" type="as" access="read"/>
 </interface>
 <interface name="org.mpris.MediaPlayer2.Player">
  <method name="Next"/><method name="Previous"/><method name="Pause"/><method name="PlayPause"/>
  <method name="Stop"/><method name="Play"/>
  <method name="Seek"><arg name="Offset" type="x" direction="in"/></method>
  <method name="SetPosition"><arg name="TrackId" type="o" direction="in"/><arg name="Position" type="x" direction="in"/></method>
  <method name="OpenUri"><arg name="Uri" type="s" direction="in"/></method>
  <signal name="Seeked"><arg name="Position" type="x"/></signal>
  <property name="PlaybackStatus" type="s" access="read"/><property name="Rate" type="d" access="read"/>
  <property name="Metadata" type="a{sv}" access="read"/><property name="Volume" type="d" access="read"/>
  <property name="Position" type="x" access="read"/><property name="MinimumRate" type="d" access="read"/>
  <property name="MaximumRate" type="d" access="read"/><property name="CanGoNext" type="b" access="read"/>
  <property name="CanGoPrevious" type="b" access="read"/><property name="CanPlay" type="b" access="read"/>
  <property name="CanPause" type="b" access="read"/><property name="CanSeek" type="b" access="read"/>
  <property name="CanControl" type="b" access="read"/>
 </interface>
</node>'''
ROOT_INTERFACE, PLAYER_INTERFACE = 'org.mpris.MediaPlayer2', 'org.mpris.MediaPlayer2.Player'


def mpris_bus_name(peer):
    return 'org.mpris.MediaPlayer2.LumaConnect.p' + peer[:12]


def mpris_properties(payload, *, device, position_us):
    """Maps a validated `media.mirror` payload to typed MPRIS properties."""
    actions = set(payload['actions'])
    identity = hashlib.sha256('\x00'.join((payload['app'], payload['title'], payload['artist'], payload['album'])).encode())
    metadata = {'mpris:trackid': ('o', '/org/projectluma/Connect/track/t' + identity.hexdigest()[:16]),
                'xesam:title': ('s', payload['title']),
                'xesam:artist': ('as', [payload['artist']] if payload['artist'] else []),
                'xesam:album': ('s', payload['album'])}
    if payload['duration_ms'] is not None: metadata['mpris:length'] = ('x', payload['duration_ms'] * 1000)
    status = {'playing': 'Playing', 'paused': 'Paused'}.get(payload['state'], 'Stopped')
    root = {'CanQuit': ('b', False), 'CanRaise': ('b', False), 'HasTrackList': ('b', False),
            'Identity': ('s', f"{payload['app']} on {device}" if payload['app'] else device),
            'DesktopEntry': ('s', APP_ID), 'SupportedUriSchemes': ('as', []), 'SupportedMimeTypes': ('as', [])}
    player = {'PlaybackStatus': ('s', status), 'Rate': ('d', 1.0), 'MinimumRate': ('d', 1.0), 'MaximumRate': ('d', 1.0),
              'Volume': ('d', 1.0), 'Metadata': ('a{sv}', metadata), 'Position': ('x', position_us),
              'CanGoNext': ('b', 'next' in actions), 'CanGoPrevious': ('b', 'previous' in actions),
              'CanPlay': ('b', 'play' in actions), 'CanPause': ('b', 'pause' in actions),
              'CanSeek': ('b', 'seek' in actions and payload['duration_ms'] is not None), 'CanControl': ('b', True)}
    return {ROOT_INTERFACE: root, PLAYER_INTERFACE: player}


class MprisBridge:
    """One MPRIS player per phone while something is playing or paused on it.

    `exporter(bus_name, method, properties)` returns an object with
    `changed(interface, values)`, `seeked(position_us)` and `close()`. `control(peer,
    payload)` invokes `media.control`. Call from the main loop.
    """

    def __init__(self, exporter, *, control, device_name=lambda peer: 'Phone', now=time.monotonic):
        self.exporter, self.control, self.device_name, self.now = exporter, control, device_name, now
        self.players = {}

    def _position(self, player):
        payload = player['payload']
        if payload['position_ms'] is None: return 0
        position = payload['position_ms'] * 1000
        if payload['state'] == 'playing': position += int((self.now() - player['reported']) * 1_000_000)
        if payload['duration_ms'] is not None: position = min(position, payload['duration_ms'] * 1000)
        return position

    def properties(self, peer):
        player = self.players[peer]
        return mpris_properties(player['payload'], device=self.device_name(peer), position_us=self._position(player))

    def update(self, peer, payload):
        if payload['state'] == 'none':
            self.remove(peer); return
        player = self.players.get(peer)
        expected = self._position(player) if player else None
        before = self.properties(peer) if player else None
        if player is None:
            player = self.players[peer] = {'payload': payload, 'reported': self.now()}
            player['export'] = self.exporter(mpris_bus_name(peer), lambda method, args: self.method(peer, method, args),
                                             lambda: self.properties(peer))
        player.update(payload=payload, reported=self.now())
        after = self.properties(peer)
        for interface, values in after.items():
            changed = {name: value for name, value in values.items()
                       if name != 'Position' and (before is None or before[interface].get(name) != value)}
            if changed and before is not None: player['export'].changed(interface, changed)
        position = self._position(player)
        if expected is not None and payload['position_ms'] is not None and abs(position - expected) > 2_000_000:
            player['export'].seeked(position)

    def method(self, peer, method, args):
        player = self.players.get(peer)
        if player is None: raise LookupError('player closed')
        payload, actions = player['payload'], set(player['payload']['actions'])
        if method in {'Raise', 'Quit', 'OpenUri'}: raise NotImplementedError(method)
        if method == 'PlayPause': method = 'Pause' if payload['state'] == 'playing' else 'Play'
        if method == 'Stop': method = 'Pause'
        command = {'Play': 'play', 'Pause': 'pause', 'Next': 'next', 'Previous': 'previous'}.get(method)
        request = {'command': command} if command else None
        if method in {'Seek', 'SetPosition'} and 'seek' in actions and payload['duration_ms'] is not None:
            if method == 'Seek':
                target = self._position(player) + int(args[0])
            else:
                track = self.properties(peer)[PLAYER_INTERFACE]['Metadata'][1]['mpris:trackid'][1]
                if args[0] != track: return  # the specification says to ignore stale track ids
                target = int(args[1])
                if target < 0 or target > payload['duration_ms'] * 1000: return
            request = {'command': 'seek', 'position_ms': max(0, min(target, payload['duration_ms'] * 1000)) // 1000}
        if request is None or request['command'] not in actions: raise PermissionError('not available on this phone')
        self.control(peer, request)

    def remove(self, peer):
        player = self.players.pop(peer, None)
        if player: player['export'].close()

    def close(self):
        for peer in list(self.players): self.remove(peer)


class GioMprisExport:
    """A private session-bus connection per phone, so each player has its own object tree."""

    def __init__(self, bus_name, method, properties):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib, self.method, self.properties = Gio, GLib, method, properties
        address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        self.connection = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        info = Gio.DBusNodeInfo.new_for_xml(MPRIS_XML)
        self.registrations = [self.connection.register_object(MPRIS_PATH, interface, self._call, self._get, None)
                              for interface in info.interfaces]
        self.ownership = Gio.bus_own_name_on_connection(self.connection, bus_name, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                                                        None, None)

    def _variant(self, signature, value):
        if signature == 'a{sv}':
            return self.GLib.Variant('a{sv}', {name: self.GLib.Variant(kind, item) for name, (kind, item) in value.items()})
        return self.GLib.Variant(signature, value)

    def _get(self, _connection, _sender, _path, interface, name):
        signature, value = self.properties()[interface][name]
        return self._variant(signature, value)

    def _call(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        try:
            self.method(method, parameters.unpack())
            invocation.return_value(None)
        except NotImplementedError:
            invocation.return_dbus_error('org.freedesktop.DBus.Error.NotSupported', 'Not supported for a phone')
        except (PermissionError, LookupError):
            invocation.return_dbus_error('org.freedesktop.DBus.Error.AccessDenied', 'Not available on this phone')

    def changed(self, interface, values):
        changed = {name: self._variant(signature, value) for name, (signature, value) in values.items()}
        self.connection.emit_signal(None, MPRIS_PATH, 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                                    self.GLib.Variant('(sa{sv}as)', (interface, changed, [])))

    def seeked(self, position):
        self.connection.emit_signal(None, MPRIS_PATH, PLAYER_INTERFACE, 'Seeked', self.GLib.Variant('(x)', (position,)))

    def close(self):
        self.Gio.bus_unown_name(self.ownership)
        for registration in self.registrations: self.connection.unregister_object(registration)
        self.connection.close_sync(None)


# --------------------------------------------------------------------------- Avahi

class AvahiPublisher:
    """Publishes `_luma-connect._tcp` with TXT `v=1` through avahi-daemon's D-Bus API.

    `call(path, interface, method, signature, values)` reaches org.freedesktop.Avahi
    on the system bus; `subscribe(path, handler)` delivers EntryGroup.StateChanged
    and returns an unsubscribe callable. The instance name is random so the
    advertisement names neither the person nor the computer.
    """

    SERVICE, TXT = '_luma-connect._tcp', [b'v=1']
    SERVER, GROUP = 'org.freedesktop.Avahi.Server', 'org.freedesktop.Avahi.EntryGroup'
    COLLISION, FAILURE = 3, 4
    IF_UNSPEC = PROTO_UNSPEC = -1

    def __init__(self, port, *, call, subscribe, name=None, retry=None):
        if type(port) is not int or not 1 <= port <= 65535: raise ValueError('invalid port')
        self.port, self.call, self.subscribe, self.retry = port, call, subscribe, retry
        self.name = name or 'Luma Connect ' + secrets.token_hex(3)
        self.group = self._unsubscribe = None

    def start(self):
        if self.group: return
        self.group, = self.call('/', self.SERVER, 'EntryGroupNew', '()', ())
        self._unsubscribe = self.subscribe(self.group, self._state)
        self._add()

    def _add(self):
        # AddService(i interface, i protocol, u flags, s name, s type, s domain, s host, q port, aay txt)
        self.call(self.group, self.GROUP, 'AddService', '(iiussssqaay)',
                  (self.IF_UNSPEC, self.PROTO_UNSPEC, 0, self.name, self.SERVICE, '', '', self.port, self.TXT))
        self.call(self.group, self.GROUP, 'Commit', '()', ())

    def _state(self, state, _error):
        if not self.group: return
        if state == self.COLLISION:
            self.name, = self.call('/', self.SERVER, 'GetAlternativeServiceName', '(s)', (self.name,))
            self.call(self.group, self.GROUP, 'Reset', '()', ())
            self._add()
        elif state == self.FAILURE:
            self.stop()
            if self.retry: self.retry(self.start)

    def stop(self):
        group, self.group = self.group, None
        if self._unsubscribe: self._unsubscribe(); self._unsubscribe = None
        if group:
            try: self.call(group, self.GROUP, 'Free', '()', ())
            except Exception: pass  # avahi-daemon restarted; the group is already gone

    @classmethod
    def gio(cls, port):
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        def call(path, interface, method, signature, values):
            parameters = GLib.Variant(signature, values) if signature != '()' else None
            result = bus.call_sync('org.freedesktop.Avahi', path, interface, method, parameters, None,
                                   Gio.DBusCallFlags.NO_AUTO_START, 5000, None)
            return result.unpack() if result is not None else ()
        def subscribe(path, handler):
            subscription = bus.signal_subscribe('org.freedesktop.Avahi', cls.GROUP, 'StateChanged', path, None,
                                                Gio.DBusSignalFlags.NONE, lambda *args: handler(*args[-1].unpack()))
            return lambda: bus.signal_unsubscribe(subscription)
        return cls(port, call=call, subscribe=subscribe,
                   retry=lambda callback: GLib.timeout_add_seconds(30, lambda: (callback(), False)[1]))


# --------------------------------------------------------------------------- local addresses

LAN_TYPES = frozenset({'802-3-ethernet', '802-11-wireless', 'bond', 'vlan'})


def select_addresses(candidates, limit=4):
    """Explicit private unicast addresses; never unspecified, loopback or scoped link-local IPv6."""
    result = []
    for candidate in candidates:
        try: address = ipaddress.ip_address(candidate)
        except ValueError: continue
        if (address.is_unspecified or address.is_loopback or address.is_multicast or not address.is_private
                or address.version == 6 and address.is_link_local):
            continue  # link-local IPv6 needs a scope id the listener cannot bind without
        if str(address) not in result: result.append(str(address))
    return sorted(result, key=lambda value: ':' in value)[:limit]


def networkmanager_addresses():
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    def get(path, interface, name):
        return bus.call_sync('org.freedesktop.NetworkManager', path, 'org.freedesktop.DBus.Properties', 'Get',
                             GLib.Variant('(ss)', (interface, name)), None, Gio.DBusCallFlags.NO_AUTO_START,
                             2000, None).unpack()[0]
    base = 'org.freedesktop.NetworkManager'
    primary = get('/org/freedesktop/NetworkManager', base, 'PrimaryConnection')
    active = get('/org/freedesktop/NetworkManager', base, 'ActiveConnections')
    addresses = []
    for path in sorted(active, key=lambda item: item != primary):
        interface = base + '.Connection.Active'
        if get(path, interface, 'State') != 2 or get(path, interface, 'Type') not in LAN_TYPES: continue
        for config, kind in (('Ip4Config', 'IP4Config'), ('Ip6Config', 'IP6Config')):
            config_path = get(path, interface, config)
            if config_path and config_path != '/':
                addresses += [entry['address'] for entry in get(config_path, f'{base}.{kind}', 'AddressData') if 'address' in entry]
    return addresses


def route_addresses():
    """Fallback without NetworkManager. Gio has no interface-address API, so ask the
    kernel which source address a documentation-prefix route would use (no packet is sent)."""
    import socket
    addresses = []
    for family, target in ((socket.AF_INET, ('192.0.2.1', 9)), (socket.AF_INET6, ('2001:db8::1', 9))):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as probe:
                probe.connect(target); addresses.append(probe.getsockname()[0])
        except OSError: pass
    return addresses


def local_addresses(*, sources=(networkmanager_addresses, route_addresses), limit=4):
    for source in sources:
        try: selected = select_addresses(source(), limit)
        except Exception: selected = []  # NetworkManager absent or not permitted
        if selected: return selected
    return []


def desktop_name():
    from gi.repository import Gio, GLib
    name = ''
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        name = bus.call_sync('org.freedesktop.hostname1', '/org/freedesktop/hostname1', 'org.freedesktop.DBus.Properties',
                             'Get', GLib.Variant('(ss)', ('org.freedesktop.hostname1', 'PrettyHostname')), None,
                             Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
    except GLib.Error: pass
    name = ''.join(character for character in (name or GLib.get_host_name() or '') if character.isprintable())[:128]
    return name.strip() or 'Luma computer'


# --------------------------------------------------------------------------- sound

class DesktopRinger:
    """Rings until stopped, or for 30 seconds. Call from the main loop.

    `play(done)` starts one sound and returns a cancel callable, calling `done`
    when it ends; `later(seconds, callback)` returns a handle for `cancel`.
    """

    TIMEOUT = 30

    def __init__(self, play, *, later, cancel):
        self.play, self.later, self.cancel = play, later, cancel
        self.active = False
        self._stop_sound = self._timer = None

    def __call__(self, ring):
        self.start() if ring else self.stop()

    def start(self):
        if self.active: return
        self.active = True
        self._timer = self.later(self.TIMEOUT, self.stop)
        self._next()

    def _next(self):
        if self.active: self._stop_sound = self.play(self._next)

    def stop(self):
        if not self.active: return
        self.active = False
        if self._timer is not None: self.cancel(self._timer); self._timer = None
        if self._stop_sound: self._stop_sound(); self._stop_sound = None

    @classmethod
    def gio(cls):
        import gi
        gi.require_version('GSound', '1.0')
        from gi.repository import Gio, GLib, GSound
        context = GSound.Context()
        context.init()
        def play(done):
            cancellable = Gio.Cancellable()
            def finished(source, result):
                try: source.play_full_finish(result)
                except GLib.Error:
                    if cancellable.is_cancelled(): return
                    GLib.timeout_add_seconds(1, lambda: (done(), False)[1]); return  # missing theme sound
                done()
            context.play_full({GSound.ATTR_EVENT_ID: 'phone-incoming-call', GSound.ATTR_MEDIA_ROLE: 'alarm',
                               GSound.ATTR_EVENT_DESCRIPTION: 'Your phone is looking for this computer'},
                              cancellable, finished)
            return cancellable.cancel
        return cls(play, later=lambda seconds, callback: GLib.timeout_add_seconds(seconds, lambda: (callback(), False)[1]),
                   cancel=GLib.source_remove)


# --------------------------------------------------------------------------- input

# Mutter org.gnome.Mutter.RemoteDesktop, verified 2026-09-13 against
# gitlab.gnome.org/GNOME/mutter data/dbus-interfaces/org.gnome.Mutter.RemoteDesktop.xml
# (main, last changed by commit 0b8fae63):
#   RemoteDesktop.CreateSession() -> (o session_path)
#   Session.Start(), Session.Stop(), signal Session.Closed
#   Session.NotifyPointerMotionRelative(d dx, d dy)
#   Session.NotifyPointerButton(i button, b state)        button is a Linux evdev code
#   Session.NotifyPointerAxis(d dx, d dy, u flags)        10.0 = one discrete step;
#                                                         flags 1 finish, 4 source_finger
#   Session.NotifyKeyboardKeysym(u keysym, b state)
REMOTE_DESKTOP_BUS = 'org.gnome.Mutter.RemoteDesktop'
REMOTE_DESKTOP_PATH = '/org/gnome/Mutter/RemoteDesktop'
REMOTE_DESKTOP_SESSION = 'org.gnome.Mutter.RemoteDesktop.Session'
BUTTONS = {'left': 0x110, 'right': 0x111, 'middle': 0x112}  # BTN_LEFT/RIGHT/MIDDLE, linux/input-event-codes.h
KEYSYMS = {'enter': 0xff0d, 'backspace': 0xff08, 'tab': 0xff09, 'escape': 0xff1b, 'delete': 0xffff,
           'insert': 0xff63, 'space': 0x20, 'left': 0xff51, 'up': 0xff52, 'right': 0xff53, 'down': 0xff54,
           'home': 0xff50, 'end': 0xff57, 'page_up': 0xff55, 'page_down': 0xff56, 'shift': 0xffe1,
           'control': 0xffe3, 'alt': 0xffe9, 'super': 0xffeb, 'caps_lock': 0xffe5, 'menu': 0xff67,
           **{f'f{number}': 0xffbe + number - 1 for number in range(1, 13)}}  # xkbcommon-keysyms.h


def keysym(character):
    if character == '\n': return KEYSYMS['enter']
    if character == '\t': return KEYSYMS['tab']
    code = ord(character)
    if 0x20 <= code <= 0x7e or 0xa0 <= code <= 0xff: return code
    return 0x01000000 | code  # Unicode keysym; Mutter types it only if the keymap can produce it


class InputInjector:
    """Phone trackpad and keyboard through one lazily created Mutter session.

    `call(path, interface, method, signature, values)` reaches Mutter. The
    session is created on the first event batch and stopped after 60 idle
    seconds; held buttons and keys are released first. `locked()` returning
    True drops the batch: a phone must not type into the unlock prompt.
    """

    IDLE = 60
    AXIS_FINISH, AXIS_FINGER = 1, 4

    def __init__(self, call, *, locked=lambda: False, now=time.monotonic, timer=True):
        self.call, self.locked, self.now, self.timer = call, locked, now, timer
        self.session, self.last, self._held = None, 0.0, set()
        self._lock = threading.Lock()
        self._reaper = None

    def __call__(self, peer, events):
        return self.inject(events)

    def _open(self):
        if self.session is None:
            path, = self.call(REMOTE_DESKTOP_PATH, REMOTE_DESKTOP_BUS, 'CreateSession', '()', ())
            try: self.call(path, REMOTE_DESKTOP_SESSION, 'Start', '()', ())
            except BaseException:
                try: self.call(path, REMOTE_DESKTOP_SESSION, 'Stop', '()', ())
                except Exception: pass
                raise
            self.session = path
            if self.timer: self._schedule()
        return self.session

    def _schedule(self):
        self._reaper = threading.Timer(self.IDLE, self._tick)
        self._reaper.daemon = True
        self._reaper.start()

    def _tick(self):
        if self.reap() is False and self.timer:
            with self._lock:
                if self.session is not None: self._schedule()

    def inject(self, events):
        if self.locked():
            self.close(); return False
        with self._lock:
            try:
                session = self._open()
                scrolled = False
                for event in events:
                    kind = event['type']
                    if kind == 'move':
                        self.call(session, REMOTE_DESKTOP_SESSION, 'NotifyPointerMotionRelative', '(dd)',
                                  (float(event['dx']), float(event['dy'])))
                    elif kind == 'scroll':
                        scrolled = True
                        self.call(session, REMOTE_DESKTOP_SESSION, 'NotifyPointerAxis', '(ddu)',
                                  (float(event['dx']), float(event['dy']), self.AXIS_FINGER))
                    elif kind == 'button':
                        self._press('b', BUTTONS[event['button']], event['pressed'], session)
                    elif kind == 'key':
                        code = KEYSYMS.get(event['key']) or keysym(event['key'])
                        self._press('k', code, event['pressed'], session)
                    elif kind == 'text':
                        for character in event['text']:
                            for pressed in (True, False):
                                self.call(session, REMOTE_DESKTOP_SESSION, 'NotifyKeyboardKeysym', '(ub)',
                                          (keysym(character), pressed))
                if scrolled:
                    # A batch is one gesture segment: finishing lets kinetic scrolling settle.
                    self.call(session, REMOTE_DESKTOP_SESSION, 'NotifyPointerAxis', '(ddu)',
                              (0.0, 0.0, self.AXIS_FINISH | self.AXIS_FINGER))
                self.last = self.now()
                return True
            except BaseException:
                self.session = None  # Mutter closed it or restarted; recreate on the next batch
                self._held.clear()
                raise

    def _press(self, kind, code, pressed, session):
        if kind == 'b':
            self.call(session, REMOTE_DESKTOP_SESSION, 'NotifyPointerButton', '(ib)', (code, pressed))
        else:
            self.call(session, REMOTE_DESKTOP_SESSION, 'NotifyKeyboardKeysym', '(ub)', (code, pressed))
        (self._held.add if pressed else self._held.discard)((kind, code))

    def reap(self):
        with self._lock:
            if self.session is None: return None
            if self.now() - self.last < self.IDLE: return False
            self._close_locked()
            return True

    def _close_locked(self):
        session, self.session = self.session, None
        try:
            for kind, code in sorted(self._held):
                self._press(kind, code, False, session)
            self.call(session, REMOTE_DESKTOP_SESSION, 'Stop', '()', ())
        except Exception:
            pass
        self._held.clear()

    def close(self):
        with self._lock:
            if self.session is not None: self._close_locked()
            reaper, self._reaper = self._reaper, None
        if reaper: reaper.cancel()

    @classmethod
    def gio(cls):
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        def call(path, interface, method, signature, values):
            parameters = GLib.Variant(signature, values) if signature != '()' else None
            result = bus.call_sync(REMOTE_DESKTOP_BUS, path, interface, method, parameters, None,
                                   Gio.DBusCallFlags.NO_AUTO_START, 2000, None)
            return result.unpack() if result is not None else ()
        screensaver = BusService(Gio.BusType.SESSION, 'org.gnome.ScreenSaver', '/org/gnome/ScreenSaver',
                                 'org.gnome.ScreenSaver', timeout=1000)
        def locked():
            try: return screensaver('GetActive', '()', ())[0] is not False
            except Exception: return True  # unknown lock state fails closed
        injector = cls(call, locked=locked)
        injector.screensaver = screensaver
        return injector


# --------------------------------------------------------------------------- do not disturb

def main_loop_runner(timeout=2.0):
    """Runs a callable on the GLib main loop from a worker thread and waits for its result."""
    from gi.repository import GLib
    def run(work):
        done, box = threading.Event(), {}
        def idle():
            try: box['value'] = work()
            except BaseException as error: box['error'] = error
            finally: done.set()
            return False
        GLib.idle_add(idle)
        if not done.wait(timeout): raise TimeoutError('main loop did not answer')
        if 'error' in box: raise box['error']
        return box['value']
    return run


class GnomeDnd:
    """Do Not Disturb shared with paired phones (`dnd.set`).

    GNOME's Do Not Disturb switch in the notification list writes the GSettings
    key `org.gnome.desktop.notifications show-banners` (b, default true;
    gsettings-desktop-schemas `org.gnome.desktop.notifications.gschema.xml.in`).
    Do Not Disturb is on when banners are hidden.

    `settings` is a `Gio.Settings` (or a recorder with `get_boolean`,
    `set_boolean`, `connect`, `disconnect`) used on the main loop only;
    `run_on_main(callable)` brings the phone's request there. `send(on)` is
    called on the main loop when the person changes Do Not Disturb here and must
    hand the network call to a worker (see `broadcast`). Echo suppression is by
    state: a value received from a phone, or already sent, is never sent again.
    """

    SCHEMA = 'org.gnome.desktop.notifications'
    KEY = 'show-banners'

    def __init__(self, settings, *, send, run_on_main=lambda work: work()):
        self.settings, self.send, self.run_on_main = settings, send, run_on_main
        self._synced = None
        self._lock = threading.Lock()
        self._handler = settings.connect('changed::' + self.KEY, self._changed)

    @property
    def on(self):
        return not self.settings.get_boolean(self.KEY)

    def __call__(self, peer, on):
        """DesktopAdapters `dnd` effect, called on the listener thread. Returns the resulting state."""
        if type(on) is not bool: raise ValueError('invalid do not disturb')
        def apply():
            with self._lock: self._synced = on
            if self.on != on: self.settings.set_boolean(self.KEY, not on)
            return self.on
        return self.run_on_main(apply)

    def _changed(self, *_):
        on = self.on
        with self._lock:
            if on == self._synced: return
            self._synced = on
        self.send(on)

    def close(self):
        if self._handler is not None:
            self.settings.disconnect(self._handler); self._handler = None

    @staticmethod
    def broadcast(call, directory, devices, on):
        """Worker: sends `dnd.set` to every listed phone this computer may set it on. Never retries."""
        for device in devices:
            if 'dnd.set' not in device.get('outgoing', ()): continue
            try: call(directory, device['fingerprint'], 'dnd.set', {'on': bool(on)}, lifetime=30)
            except Exception as error:
                LOG.info('Do Not Disturb was not shared (%s)', type(error).__name__)

    @classmethod
    def gio(cls, *, send, run_on_main=None):
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup(cls.SCHEMA, True) is None: raise LookupError(f'{cls.SCHEMA} is not installed')
        return cls(Gio.Settings.new(cls.SCHEMA), send=send, run_on_main=run_on_main or main_loop_runner())


# --------------------------------------------------------------------------- hotspot

# NetworkManager D-Bus API, checked 2026-09-13 against networkmanager.dev/docs/api/latest:
#   org.freedesktop.NetworkManager.GetDevices() -> ao; ActivateConnection(o connection, o device, o specific) -> o
#   org.freedesktop.NetworkManager property ActiveConnections (ao)
#   org.freedesktop.NetworkManager.Device property DeviceType (u, 2 = NM_DEVICE_TYPE_WIFI)
#   org.freedesktop.NetworkManager.Device.Wireless.RequestScan(a{sv}); GetAllAccessPoints() -> ao
#   org.freedesktop.NetworkManager.AccessPoint property Ssid (ay)
#   org.freedesktop.NetworkManager.Settings.GetConnectionByUuid(s) -> o
#   org.freedesktop.NetworkManager.Settings.Connection.GetSettings() -> a{sa{sv}} (never includes secrets)
#   org.freedesktop.NetworkManager.Connection.Active properties Uuid (s), Type (s)
NM_BUS = 'org.freedesktop.NetworkManager'
NM_PATH = '/org/freedesktop/NetworkManager'
NM_WIFI = 2
HOTSPOTS_FILE = 'companion-hotspots.json'


class NetworkManagerWifi:
    """The few NetworkManager calls the hotspot helper needs. `call(path, interface, method, signature, values)`."""

    def __init__(self, call, get):
        self.call, self.get = call, get

    def wifi_devices(self):
        devices, = self.call(NM_PATH, NM_BUS, 'GetDevices', '()', ())
        return [path for path in devices if self.get(path, NM_BUS + '.Device', 'DeviceType') == NM_WIFI]

    def request_scan(self, device):
        try: self.call(device, NM_BUS + '.Device.Wireless', 'RequestScan', '(a{sv})', ({},))
        except Exception: pass  # a scan is already running, or scanning is not permitted right now

    def visible_ssids(self, device):
        points, = self.call(device, NM_BUS + '.Device.Wireless', 'GetAllAccessPoints', '()', ())
        return {bytes(self.get(point, NM_BUS + '.AccessPoint', 'Ssid')) for point in points}

    def connection_ssid(self, uuid):
        try:
            path, = self.call(NM_PATH + '/Settings', NM_BUS + '.Settings', 'GetConnectionByUuid', '(s)', (uuid,))
            settings, = self.call(path, NM_BUS + '.Settings.Connection', 'GetSettings', '()', ())
        except Exception:
            return None
        if settings.get('connection', {}).get('type') != '802-11-wireless': return None
        ssid = settings.get('802-11-wireless', {}).get('ssid')
        return bytes(ssid) if ssid else None

    def activate(self, uuid, device):
        path, = self.call(NM_PATH + '/Settings', NM_BUS + '.Settings', 'GetConnectionByUuid', '(s)', (uuid,))
        self.call(NM_PATH, NM_BUS, 'ActivateConnection', '(ooo)', (path, device, '/'))

    def active_wifi(self):
        uuids = set()
        for path in self.get(NM_PATH, NM_BUS, 'ActiveConnections'):
            try:
                if self.get(path, NM_BUS + '.Connection.Active', 'Type') == '802-11-wireless':
                    uuids.add(self.get(path, NM_BUS + '.Connection.Active', 'Uuid'))
            except Exception:
                continue  # deactivated while listing
        return uuids

    @classmethod
    def gio(cls):
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        def call(path, interface, method, signature, values):
            parameters = GLib.Variant(signature, values) if signature != '()' else None
            result = bus.call_sync(NM_BUS, path, interface, method, parameters, None,
                                   Gio.DBusCallFlags.NO_AUTO_START, 5000, None)
            return result.unpack() if result is not None else ()
        def get(path, interface, name):
            return call(path, 'org.freedesktop.DBus.Properties', 'Get', '(ss)', (interface, name))[0]
        return cls(call, get)


class HotspotJoiner:
    """One-tap hotspot, desktop side (`hotspot.request`).

    Android offers no public way to turn the hotspot on or to read its name
    without location access, so the phone only asks its person with a tap. The
    computer then rescans Wi-Fi for about a minute. If NetworkManager already has
    a saved connection that this helper learnt belongs to that phone, it is
    activated as soon as it is visible. Otherwise the person picks the network
    in the usual Wi-Fi menu (NetworkManager keeps any password), and the helper
    remembers only that connection's UUID for next time, in a 0600 sidecar.
    """

    ATTEMPTS = 12
    INTERVAL = 5

    def __init__(self, nm, directory, *, call=companion.call, sleep=time.sleep, cancelled=lambda: False):
        self.nm, self.directory, self.call, self.sleep, self.cancelled = nm, Path(directory), call, sleep, cancelled
        self._lock = threading.Lock()

    @property
    def path(self):
        return self.directory / HOTSPOTS_FILE

    def _load(self):
        try: data = json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError): return {}
        return data if isinstance(data, dict) else {}

    def remembered(self, fingerprint):
        value = self._load().get(fingerprint)
        return value if isinstance(value, str) and 0 < len(value) <= 64 else None

    def _remember(self, fingerprint, uuid):
        with self._lock:
            data = self._load()
            if uuid is None: data.pop(fingerprint, None)
            else: data[fingerprint] = uuid
            temporary = self.path.with_name('.' + HOTSPOTS_FILE + '-' + secrets.token_hex(8))
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream: stream.write(json.dumps(data, sort_keys=True))
            os.replace(temporary, self.path)

    def forget(self, fingerprint):
        self._remember(fingerprint, None)

    def request(self, fingerprint):
        """Worker: asks the phone, then waits for its hotspot. Returns 'joined', 'learned', 'not-found' or 'refused'."""
        receipt = self.call(self.directory, fingerprint, 'hotspot.request', {}, lifetime=60)
        result = receipt.get('result') if isinstance(receipt, dict) else None
        if receipt.get('state') != 'complete' or result != {'error': 'needs-user'}: return 'refused'
        return self.join(fingerprint)

    def join(self, fingerprint):
        uuid = self.remembered(fingerprint)
        before = self.nm.active_wifi()
        for _ in range(self.ATTEMPTS):
            if self.cancelled(): break
            devices = self.nm.wifi_devices()
            for device in devices: self.nm.request_scan(device)
            self.sleep(self.INTERVAL)
            if uuid is not None:
                if uuid in self.nm.active_wifi(): return 'joined'
                ssid = self.nm.connection_ssid(uuid)
                if ssid is None:  # the person deleted that saved network; learn again
                    self.forget(fingerprint); uuid = None
                else:
                    for device in devices:
                        if ssid in self.nm.visible_ssids(device):
                            self.nm.activate(uuid, device)
                            return 'joined'
                    continue
            joined = self.nm.active_wifi() - before
            if len(joined) == 1:
                self._remember(fingerprint, next(iter(joined)))
                return 'learned'
        return 'not-found'


# --------------------------------------------------------------------------- outgoing calls

def _reject(_value):
    raise ValueError('floating-point and non-finite values are not allowed')


def _object(raw):
    if not isinstance(raw, str) or len(raw.encode()) > MAX_INVOKE_PAYLOAD: raise ValueError('payload too large')
    value = json.loads(raw, object_pairs_hook=_unique, parse_float=_reject, parse_constant=_reject)
    if not isinstance(value, dict): raise ValueError('object required')
    return value


def validate_invoke(capability, raw):
    """Parses and checks a desktop-to-phone payload from the app. Raises ValueError."""
    if capability not in INVOKE_CAPABILITIES: raise PermissionError('capability is not available from this computer')
    payload = _object(raw)
    if capability == 'device.ring':
        if set(payload) != {'ring'} or type(payload['ring']) is not bool: raise ValueError('invalid ring')
    elif capability == 'clipboard.write':
        if (set(payload) - {'text', 'sensitive'} or not isinstance(payload.get('text'), str)
                or type(payload.get('sensitive', False)) is not bool):
            raise ValueError('invalid clipboard')
    elif capability == 'links.open':
        url = payload.get('url')
        if (set(payload) - {'url', 'title'} or not isinstance(url, str) or not 0 < len(url) <= 4096
                or urlparse(url).scheme not in {'http', 'https'} or not urlparse(url).netloc
                or not isinstance(payload.get('title', ''), str) or len(payload.get('title', '')) > 256):
            raise ValueError('invalid link')
    elif capability == 'notifications.act':
        action = payload.get('action')
        if (set(payload) - {'key', 'action', 'text'} or not isinstance(payload.get('key'), str)
                or not IDENTIFIER.fullmatch(payload['key']) or not isinstance(action, str)
                or not (action in {'dismiss', 'open'} or IDENTIFIER.fullmatch(action))
                or 'text' in payload and (not isinstance(payload['text'], str) or len(payload['text']) > 4096)):
            raise ValueError('invalid notification action')
    elif capability == 'media.control':
        command = payload.get('command')
        if command not in {'play', 'pause', 'next', 'previous', 'seek'}: raise ValueError('invalid media command')
        if command == 'seek':
            if set(payload) != {'command', 'position_ms'} or type(payload['position_ms']) is not int or payload['position_ms'] < 0:
                raise ValueError('invalid seek')
        elif set(payload) != {'command'}:
            raise ValueError('invalid media command')
    return payload


def _file_name(path):
    name = ''.join(character for character in Path(path).name if character.isprintable() and character != '/')
    if len(name) > 128:
        suffix = Path(name).suffix[:16]
        name = name[:128 - len(suffix)] + suffix
    if name in {'', '.', '..'} or not companion.NAME.fullmatch(name): raise ValueError('unsupported file name')
    return name


def send_file(call, fingerprint, path, *, transfer=None, cancelled=lambda: False, progress=lambda sent, size: None,
              chunk=FILE_CHUNK):
    """Sends one regular file with `files.write` in order. Never retries a chunk."""
    transfer = transfer or secrets.token_hex(16)
    if not os.path.isabs(path): raise ValueError('absolute path required')
    name = _file_name(path)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > companion.DesktopAdapters.MAX_FILE:
            raise ValueError('a regular file up to 4 GiB is required')
        size, offset, digest = info.st_size, 0, hashlib.sha256()
        while True:
            if cancelled(): raise Cancelled()
            data = bytearray()
            while len(data) < chunk:
                part = os.read(fd, chunk - len(data))
                if not part: break
                data.extend(part)
            end = offset + len(data)
            if end > size or len(data) < chunk and end != size: raise ValueError('file changed while sending')
            final = end == size
            if final and os.read(fd, 1): raise ValueError('file changed while sending')
            digest.update(data)
            payload = {'transfer': transfer, 'name': name, 'size': size, 'offset': offset,
                       'data': base64.b64encode(bytes(data)).decode(), 'final': final,
                       'sha256': digest.hexdigest() if final else None}
            receipt = call(fingerprint, 'files.write', payload)
            result = receipt.get('result') if isinstance(receipt, dict) else None
            if (receipt.get('state') != 'complete' or not isinstance(result, dict) or result.get('received') != end
                    or final and not isinstance(result.get('name'), str)):
                raise ValueError('the phone did not accept the file')
            offset = end
            progress(offset, size)
            if final: return {'transfer': transfer, 'name': result['name'], 'size': size}
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- daemon service

class CompanionService:
    """Companion state for the Connect daemon.

    `name` is the desktop name or a callable evaluated on the worker.
    `dispatch(callable)` runs on the main loop; `submit(callable)` and
    `submit_transfer(callable)` run on workers. `changed()` is dispatched after
    any snapshot change; `reply_requested(request)` when a mirrored notification
    needs a typed reply. `effects` supplies the DesktopAdapters keywords
    (notify, clipboard_set, clipboard_get, media, ring, input). `publisher(port)`
    returns an object with `start()`/`stop()` and is used on the main loop.
    """

    def __init__(self, directory, *, downloads, name, dispatch, submit, submit_transfer, changed,
                 effects=None, addresses=local_addresses, publisher=None, reply_requested=None,
                 call=companion.call, port=companion.LISTEN_PORT, enabled=True, now=time.time,
                 hotspot=None, approval=None, messages_selection=None, media_sink_factory=None,
                 media_sessions=None, adb=None, scrcpy=None, sleep=time.sleep, bonding=None, roles=None,
                 calls_selection_path=None):
        self.directory, self.downloads, self.name = Path(directory), Path(downloads), name
        self.dispatch, self.submit, self.submit_transfer = dispatch, submit, submit_transfer
        self._changed, self.addresses, self.publisher_factory = changed, addresses, publisher
        self.reply_requested, self.call, self.port, self.enabled, self.now = reply_requested, call, port, enabled, now
        self.adapters = companion.DesktopAdapters(self.directory, downloads=self.downloads,
                                                  changed=lambda *_: self.submit(self.reload), **(effects or {}))
        from .companion_rotation import RotationAdapters
        self.rotation = RotationAdapters(self.directory, changed=lambda *_: self.submit(self.refresh))
        self.hotspot, self.approval, self.messages_selection = hotspot, approval, messages_selection
        self._rotating = False
        self.media_sink_factory, self._media, self.sleep = media_sink_factory, media_sessions, sleep
        self.adb, self.scrcpy = adb, scrcpy
        self.power_state = {}
        self.bonding, self.roles = bonding, roles
        self.calls_selection_path = Path(calls_selection_path) if calls_selection_path else None
        self.listener = self.publisher = self.pairing = None
        self.want_pairing = False
        self.listening_on = ()
        self.devices, self.transfers, self.reply = [], OrderedDict(), None
        self.pairing_state = None
        self._cancelled = set()
        self._lock = threading.RLock()

    # ---- snapshot
    def changed(self):
        self.dispatch(self._changed)

    def snapshot(self):
        with self._lock:
            reply = self.reply if self.reply and self.reply['expires'] > self.now() else None
            return {'companion_devices': [dict(row) for row in self.devices],
                    'companion_pairing': dict(self.pairing_state) if self.pairing_state else None,
                    'companion_transfers': [dict(row) for row in self.transfers.values()],
                    'companion_reply': dict(reply) if reply else None,
                    'companion_listening': bool(self.listener),
                    'companion_power': {key: dict(value) for key, value in self.power_state.items()},
                    'companion_media': [{'fingerprint': session.peer, 'kind': session.kind, 'state': session.state}
                                        for session in (self._media.sessions() if self._media else [])]}

    def outgoing(self, fingerprint):
        with self._lock:
            return next((set(row['outgoing']) for row in self.devices if row['fingerprint'] == fingerprint), set())

    def device_name(self, fingerprint):
        with self._lock:
            return next((row['name'] for row in self.devices if row['fingerprint'] == fingerprint), 'Phone')

    def refresh(self):
        """Worker: reloads devices and starts or stops the listener to match."""
        self.reload(notify=False)
        self._ensure_listener()
        self._rotate_if_due()
        self.changed()

    def _adapters_for(self, peer):
        return {**self.adapters.for_peer(peer), **self.rotation.for_peer(peer)}

    def _after_request(self, peer):
        self.adapters.after_request(peer)
        self.rotation.after_request(peer)

    def _rotate_if_due(self):
        """Worker: renews this computer's certificate with every paired phone 10 days before it expires."""
        from .companion_rotation import needs_rotation, rotate_identity
        with self._lock:
            peers = {row['fingerprint']: None for row in self.devices if 'device.rotate' in row['outgoing']}
            if self._rotating or not peers: return
            self._rotating = True
        try:
            if not (self.directory / 'device.pem').exists() or not needs_rotation(self.directory, days=10): return
            epochs = {pin: row['epoch'] for pin, row in companion.Registry(self.directory).active().items() if pin in peers}
            result = rotate_identity(self.directory, epochs,
                                     lambda pin, capability, payload: self.call(self.directory, pin, capability, payload))
            if result['failures']:
                LOG.warning('%d paired phone(s) could not renew this computer certificate', len(result['failures']))
            if result['rotated']:
                with self._lock: old, self.listener, self.listening_on = self.listener, None, ()
                if old:
                    old.stop(); self.dispatch(self._stop_publisher)
                self._ensure_listener()
        finally:
            with self._lock: self._rotating = False

    # ---- one-tap hotspot, approvals, Messages
    def request_hotspot(self, fingerprint, done):
        """Asks the phone to share its connection, then joins it. `done(result)` on the main loop."""
        if not self.hotspot or 'hotspot.request' not in self.outgoing(fingerprint): raise PermissionError('not allowed for this phone')
        def work():
            try: result = self.hotspot.request(fingerprint)
            except Exception as error:
                LOG.info('Hotspot request failed (%s)', type(error).__name__); result = 'refused'
            self.dispatch(lambda: done(result))
        self.submit_transfer(work)

    def approve(self, fingerprint, reason, app, done, timeout=60):
        """Asks the person to approve on the phone. `done(bool)` on the main loop; never a system authorization by itself."""
        if not self.approval or 'auth.request' not in self.outgoing(fingerprint): raise PermissionError('not allowed for this phone')
        future = self.approval.request(fingerprint, reason, app, timeout)
        future.add_done_callback(lambda f: self.dispatch(lambda: done(bool(not f.cancelled() and f.exception() is None and f.result()))))

    # ---- camera and screen (companion_media, luma-media/1)
    CAMERA_DEFAULTS = {'facing': 'back', 'width': 1920, 'height': 1080, 'fps': 30, 'bitrate': 6_000_000, 'audio': True}
    SCREEN_DEFAULTS = {'max_size': 1920, 'fps': 60, 'bitrate': 8_000_000}

    def _media_sessions(self):
        from . import companion_media
        with self._lock:
            if self._media is None:
                if not self.media_sink_factory: raise PermissionError('camera and screen owners are unavailable')
                addresses = list(self.addresses())
                if not addresses: raise PermissionError('no local network')
                self._media = companion_media.MediaSessions(self.directory, addresses=addresses,
                                                            sink_factory=self.media_sink_factory,
                                                            on_change=lambda _session: self.changed())
            return self._media

    def start_media(self, fingerprint, kind, done, options=None):
        """Opens a media session and asks the phone to join it. `done(state)` on the main loop:
        'needs-user' (the phone shows a tap-to-start notification), 'started' or 'refused'."""
        capability = {'camera': 'camera.stream', 'screen': 'screen.view'}.get(kind)
        if capability is None: raise ValueError('invalid media kind')
        if capability not in self.outgoing(fingerprint): raise PermissionError('not allowed for this phone')
        control = kind == 'screen' and 'screen.control' in self.outgoing(fingerprint)
        payload = dict(self.CAMERA_DEFAULTS if kind == 'camera' else self.SCREEN_DEFAULTS)
        payload.update({key: value for key, value in (options or {}).items() if key in payload})
        def work():
            state, session_id = 'refused', None
            try:
                from . import companion_media
                media = self._media_sessions()
                codecs = list(self.codecs()) if callable(getattr(self, 'codecs', None)) else companion_media.installed_codecs()
                session_id, port = media.open_session(fingerprint, kind, control, codecs)
                receipt = self.call(self.directory, fingerprint, capability,
                                    {'session': session_id, 'port': port, 'codecs': codecs, **payload})
                result = receipt.get('result') if isinstance(receipt, dict) else None
                if receipt.get('state') == 'complete' and isinstance(result, dict):
                    state = 'needs-user' if result.get('error') == 'needs-user' else 'refused' if 'error' in result else 'started'
            except Exception as error:
                LOG.info('Media session did not start (%s)', type(error).__name__)
            if state == 'refused' and session_id and self._media: self._media.close(session_id)
            self.dispatch(lambda: done(state))
        self.submit(work)

    def stop_media(self, fingerprint):
        if self._media: self._media.close_peer(fingerprint)

    # ---- Power Mode (companion_power: wireless debugging + system scrcpy)
    def power_pair(self, fingerprint, done, window=120):
        """Returns the Wireless debugging QR text now; pairs and connects in the background.
        Progress is `power_state[fingerprint]` in the snapshot; `done(code)` on the main loop."""
        from . import companion_power
        if not self.adb: raise PermissionError('adb is not installed')
        if not any(row['fingerprint'] == fingerprint for row in self.devices): raise PermissionError('phone is not paired')
        qr, service, password = companion_power.wifi_pairing_qr()
        with self._lock: self.power_state[fingerprint] = {'state': 'waiting', 'expires': int(self.now()) + window}
        self.changed()
        def work():
            code = 'not-found'
            deadline = self.now() + window
            while self.now() < deadline:
                found = self.adb.discover()
                services = found.data.get('services', []) if found.ok else []
                pairing = companion_power.find_pairing_service(services, service)
                if pairing:
                    paired = self.adb.pair(pairing.host, pairing.port, password)
                    if not paired.ok:
                        code = paired.code; break
                    connect = None
                    for _ in range(10):
                        found = self.adb.discover()
                        connect = companion_power.find_connect_service(found.data.get('services', []) if found.ok else [], pairing.host)
                        if connect: break
                        self.sleep(1)
                    if not connect:
                        code = 'connect-not-found'; break
                    connected = self.adb.connect(connect.host, connect.port)
                    if connected.ok:
                        companion_power.PowerLinks(self.directory).link(fingerprint, connected.data['serial'])
                        code = 'ready'
                    else:
                        code = connected.code
                    break
                self.sleep(2)
            with self._lock: self.power_state[fingerprint] = {'state': code}
            self.changed()
            self.dispatch(lambda: done(code))
        self.submit_transfer(work)
        return qr

    def power_open(self, fingerprint, package=None):
        """Opens the phone's screen, or one app in its own window, with the system scrcpy. Returns a result code."""
        from . import companion_power
        if not self.scrcpy: return 'scrcpy-missing'
        serial = companion_power.PowerLinks(self.directory).serial_for(fingerprint)
        if not serial: return 'not-set-up'
        title = self.device_name(fingerprint)
        result = self.scrcpy.app_window(serial, package, title=title) if package else self.scrcpy.mirror(serial, title=title)
        return result.code

    # ---- calls over Bluetooth hands-free (companion_calls)
    def set_up_calls(self, fingerprint, done):
        """Enables the hands-free role, asks the phone to bond, and selects it for Phone.
        `done(code)` on the main loop: 'bonded', 'refused', 'not-found', 'ambiguous', 'not-a-phone',
        'cancelled', 'call-active' or 'unavailable'."""
        if not self.bonding or not self.roles: raise PermissionError('Bluetooth calls are unavailable on this computer')
        if 'bluetooth.bond' not in self.outgoing(fingerprint): raise PermissionError('not allowed for this phone')
        from .companion_calls import CallActive
        def work():
            try:
                # Android records the computer's services while bonding, so hands-free must be on first.
                self.roles.enable()
                name = self.name() if callable(self.name) else self.name
                code = self.bonding.begin(fingerprint, name=name)
                if code == 'bonded':
                    self._select_for_calls(fingerprint)
                    try: self.roles.enable(self.bonding.remembered(fingerprint))
                    except CallActive: pass
            except CallActive:
                code = 'call-active'
            except Exception as error:
                LOG.info('Calls setup failed (%s)', type(error).__name__); code = 'unavailable'
            self.dispatch(lambda: done(code))
        self.submit_transfer(work)

    def _select_for_calls(self, fingerprint):
        from .message_provider import SelectedPhone
        device = companion.Registry(self.directory).active().get(fingerprint)
        if not device or not device['host'] or not device['port'] or not self.calls_selection_path: return
        selected = SelectedPhone(fingerprint, device['epoch'], None, device['name'][:128], device['host'], device['port'], 'companion')
        parent = self.calls_selection_path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = parent / ('.calls-' + secrets.token_hex(8))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream: stream.write(json.dumps({'version': 1, 'phone': vars(selected)}))
        os.replace(temporary, self.calls_selection_path)

    def _forget_calls(self, fingerprint):
        if self.bonding:
            try: self.bonding.forget(fingerprint)
            except Exception as error: LOG.info('Bluetooth bond was not removed (%s)', type(error).__name__)
        path = self.calls_selection_path
        try:
            selected = json.loads(path.read_text())['phone']['peer'] if path and path.exists() else None
        except (OSError, ValueError, KeyError, TypeError):
            selected = None
        if selected == fingerprint:
            path.unlink(missing_ok=True)
            if self.roles:
                try: self.roles.disable()
                except Exception as error: LOG.info('Hands-free role stays until the call ends (%s)', type(error).__name__)

    def select_for_messages(self, fingerprint):
        """Makes this phone the message source for Prairie Messages on this computer."""
        if not self.messages_selection: raise PermissionError('Messages integration unavailable')
        self.messages_selection(fingerprint)

    def reload(self, notify=True):
        """Worker: reloads device rows only (status reports arrive often)."""
        rows = []
        if (self.directory / 'device.pem').exists():
            for row in companion.Registry(self.directory).devices():
                if row['revoked']: continue
                rows.append({'fingerprint': row['fingerprint'], 'name': row['name'], 'model': row['model'],
                             'platform': row['platform'], 'paired_at': row['paired_at'], 'seen_at': row['seen_at'],
                             'status': row['status'], 'incoming': row['incoming'], 'outgoing': row['outgoing'],
                             'reachable': bool(row['host'] and row['port'])})
        with self._lock: self.devices = rows
        if notify: self.changed()

    def _ensure_listener(self):
        # Worker only. The single companion worker serializes listener changes;
        # the lock is never held while a listener thread is joined.
        with self._lock: wanted = self.enabled and bool(self.devices or self.want_pairing)
        addresses = tuple(self.addresses()) if wanted else ()
        if self.listener and addresses == self.listening_on: return
        with self._lock:
            old, self.listener, self.listening_on = self.listener, None, ()
        if old:
            old.stop(); self.dispatch(self._stop_publisher)
        if not addresses: return
        listener = companion.CompanionListener(self.directory, addresses=list(addresses),
                                               adapter_factory=self._adapters_for, port=self.port,
                                               enabled=lambda: self.enabled, after=self._after_request)
        try: port = listener.start()
        except OSError:
            LOG.warning('Companion listener port is unavailable'); return
        with self._lock: self.listener, self.listening_on = listener, addresses
        self.dispatch(lambda: self._start_publisher(port))

    def _start_publisher(self, port):
        if not self.publisher_factory or not self.listener: return
        self._stop_publisher()
        try:
            self.publisher = self.publisher_factory(port); self.publisher.start()
        except Exception:
            self.publisher = None
            LOG.warning('Local network advertisement is unavailable; paired phones use their last address')

    def _stop_publisher(self):
        publisher, self.publisher = self.publisher, None
        if publisher: publisher.stop()

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.submit(self.refresh)

    def network_changed(self):
        self.submit(self._ensure_listener)

    # ---- pairing (main loop entry points)
    def start_pairing(self, phone_to_desktop, desktop_to_phone, done):
        incoming, outgoing = set(phone_to_desktop), set(desktop_to_phone)
        if (len(incoming) != len(phone_to_desktop) or len(outgoing) != len(desktop_to_phone)
                or not incoming <= PHONE_TO_DESKTOP or not outgoing <= DESKTOP_TO_PHONE or not (incoming or outgoing)):
            raise ValueError('unsupported permissions')
        if not self.enabled: raise PermissionError('Luma Connect is off')
        def work():
            uri = None
            try:
                self._cancel_pairing()
                if not self.directory.exists():
                    from .bootstrap import create_identity
                    self.directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    create_identity(self.directory)
                addresses = self.addresses()
                if not addresses: raise LookupError('no local network')
                with self._lock: self.want_pairing = True
                self.refresh()
                if not self.listener: raise LookupError('companion listener unavailable')
                port = self.listener.port
                name = self.name() if callable(self.name) else self.name
                session = companion.PairingSession(self.directory, name=name, addresses=addresses,
                                                   phone_to_desktop=sorted(incoming), desktop_to_phone=sorted(outgoing),
                                                   listen_port=port, on_paired=lambda _result: self.submit(self.refresh))
                with self._lock:
                    self.pairing = session
                    self.pairing_state = {'state': 'waiting', 'sas': None, 'name': None, 'model': None,
                                          'fingerprint': None, 'expires': session.created + companion.PAIRING_TTL}
                uri = session.start()
                threading.Thread(target=self._watch_pairing, args=(session,), name='connect-companion-pairing-watch',
                                 daemon=True).start()
                self.changed()
            except Exception as error:
                LOG.warning('Companion pairing could not start (%s)', type(error).__name__)
                with self._lock:
                    self.want_pairing = False
                    self.pairing_state = {'state': 'unavailable', 'sas': None, 'name': None,
                                                       'model': None, 'fingerprint': None, 'expires': None}
                self.changed()
            self.dispatch(lambda: done(uri))
        self.submit(work)

    def _watch_pairing(self, session):
        session.join()
        with self._lock:
            if self.pairing is not session: return
            result = session.result or {}
            self.pairing_state = {'state': session.state, 'sas': result.get('sas'), 'name': result.get('name'),
                                  'model': result.get('model'), 'fingerprint': result.get('fingerprint'),
                                  'expires': None}
            self.pairing, self.want_pairing = None, False
        self.submit(self.refresh)

    def _cancel_pairing(self):
        with self._lock: session, self.pairing, self.want_pairing = self.pairing, None, False
        if session: session.cancel()

    def cancel_pairing(self):
        """Stops a waiting invitation, or acknowledges a finished one ("Codes match")."""
        def work():
            self._cancel_pairing()
            with self._lock: self.pairing_state = None
            self.refresh()
        self.submit(work)

    # ---- device administration
    def remove(self, fingerprint, removed=lambda peer: None):
        if not isinstance(fingerprint, str) or len(fingerprint) != 64: raise ValueError('invalid device')
        def work():
            companion.Registry(self.directory).revoke(fingerprint)
            for owner in (self.approval, self.hotspot):
                if owner:
                    try: owner.forget(fingerprint)
                    except Exception as error: LOG.info('Could not forget phone state (%s)', type(error).__name__)
            self.stop_media(fingerprint)
            self._forget_calls(fingerprint)
            try:
                from .companion_power import forget_companion
                forget_companion(self.directory, fingerprint, adb=self.adb)
            except Exception as error:
                LOG.info('Power Mode link was not removed (%s)', type(error).__name__)
            with self._lock:
                if self.pairing_state and self.pairing_state.get('fingerprint') == fingerprint: self.pairing_state = None
                if self.reply and self.reply['fingerprint'] == fingerprint: self.reply = None
            self.dispatch(lambda: removed(fingerprint))
            self.refresh()
        self.submit(work)

    def set_grants(self, fingerprint, incoming):
        if not isinstance(fingerprint, str) or len(fingerprint) != 64: raise ValueError('invalid device')
        if len(set(incoming)) != len(incoming) or not set(incoming) <= PHONE_TO_DESKTOP: raise ValueError('unsupported permissions')
        def work():
            companion.Registry(self.directory).set_incoming(fingerprint, set(incoming))
            self.refresh()
        self.submit(work)

    # ---- desktop to phone
    def invoke(self, fingerprint, capability, raw, done):
        """Validates on the caller's thread, calls on the worker, `done(receipt | None)` on the main loop."""
        payload = validate_invoke(capability, raw)
        def work():
            receipt = None
            try:
                receipt = self.call(self.directory, fingerprint, capability, payload)
                if capability == 'notifications.act':
                    with self._lock:
                        if self.reply and self.reply['fingerprint'] == fingerprint and self.reply['key'] == payload['key']:
                            self.reply = None
                    self.changed()
            except Exception as error:  # never return transport or phone error text
                LOG.info('Companion request was not delivered (%s)', type(error).__name__)
            self.dispatch(lambda: done(receipt))
        self.submit(work)

    def effect_call(self, fingerprint, capability, payload):
        """Phone actions requested by desktop owners (notification buttons, MPRIS)."""
        if capability not in INVOKE_CAPABILITIES or capability not in self.outgoing(fingerprint): return
        self.invoke(fingerprint, capability, json.dumps(payload), lambda receipt: None)

    def request_reply(self, fingerprint, request):
        with self._lock:
            self.reply = {**request, 'fingerprint': fingerprint, 'expires': int(self.now()) + REPLY_TTL}
            reply = dict(self.reply)
        self.changed()
        if self.reply_requested: self.dispatch(lambda: self.reply_requested(reply))

    def cancel_reply(self):
        with self._lock: self.reply = None
        self.changed()

    # ---- files
    def send_file(self, fingerprint, path):
        if 'files.write' not in self.outgoing(fingerprint): raise PermissionError('this phone does not accept files')
        if not isinstance(path, str) or not os.path.isabs(path): raise ValueError('absolute path required')
        name = _file_name(path)
        transfer = secrets.token_hex(16)
        with self._lock:
            self.transfers[transfer] = {'transfer': transfer, 'fingerprint': fingerprint, 'name': name, 'size': None,
                                        'sent': 0, 'state': 'queued'}
            finished = [key for key, row in self.transfers.items() if row['state'] not in {'queued', 'sending'}]
            for key in finished[:max(0, len(self.transfers) - 20)]: del self.transfers[key]
        self.changed()
        def work():
            last = [0.0]
            def progress(sent, size):
                with self._lock: self.transfers[transfer].update(sent=sent, size=size, state='sending')
                if time.monotonic() - last[0] >= .5 or sent == size:
                    last[0] = time.monotonic(); self.changed()
            state = 'failed'
            try:
                with self._lock: self.transfers[transfer]['state'] = 'sending'
                self.changed()
                from . import companion_streams
                try:
                    # luma-files/1: one hash, one stream, resumable; phones without streams use chunks.
                    companion_streams.send_file_stream(self.directory, fingerprint, path, progress,
                                                       lambda: transfer in self._cancelled, transfer=transfer, call=self.call)
                except companion_streams.StreamUnsupported:
                    send_file(lambda *args: self.call(self.directory, *args), fingerprint, path, transfer=transfer,
                              cancelled=lambda: transfer in self._cancelled, progress=progress)
                state = 'complete'
            except Cancelled:
                state = 'cancelled'
            except Exception as error:
                LOG.info('File was not sent (%s)', type(error).__name__)
            with self._lock:
                self.transfers[transfer]['state'] = state
                self._cancelled.discard(transfer)
            self.changed()
        self.submit_transfer(work)
        return transfer

    def cancel_transfer(self, transfer):
        with self._lock:
            row = self.transfers.get(transfer)
            if not row or row['state'] not in {'queued', 'sending'}: return False
            self._cancelled.add(transfer)
        return True

    def close(self):
        if self._media: self._media.close_all()
        self._cancel_pairing()
        with self._lock:
            self._cancelled.update(key for key, row in self.transfers.items() if row['state'] in {'queued', 'sending'})
            listener, self.listener = self.listener, None
        if listener: listener.stop()
        self._stop_publisher()
