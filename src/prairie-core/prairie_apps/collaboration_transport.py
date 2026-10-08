# SPDX-License-Identifier: Apache-2.0
"""Token-free sandbox transport through the actual host Connect service."""
import json
import threading
import time
from urllib.parse import urlsplit
from .connect_sync import ConnectError, DeviceIdentity, HubResponseError

BUS = 'org.projectluma.Connect1'
PATH = '/org/projectluma/Connect'


def call(method, values=()):
    from gi.repository import Gio, GLib
    try:
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = connection.call_sync(BUS, PATH, BUS, method,
            GLib.Variant('(sss)', values) if values else None,
            GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 20000, None)
        result = json.loads(reply.unpack()[0])
        if not isinstance(result, dict): raise ValueError('Invalid Connect response.')
        return result
    except (GLib.Error, ValueError):
        raise ConnectError('Connect collaboration is unavailable. Open Luma Connect and check your sign-in.') from None


class _MetadataCache:
    """Coalesce token-free display context; never cache operation authority.

    All BrokerHTTP operations still traverse the signed-caller broker and the
    Hub's current account/permission checks. Connect state and owner changes
    invalidate this short-lived metadata immediately.
    """
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.value = self.error = None
        self.until = 0.0

    def invalidate(self):
        with self.lock:
            self.value = self.error = None
            self.until = 0.0

    def get(self, fetch):
        with self.lock:
            now = self.clock()
            if now < self.until:
                if self.error is not None:
                    raise self.error
                return self.value
            try:
                value = fetch()
            except ConnectError as error:
                # Preserve the Hub's requested backoff; a failed refresh must
                # never retain the earlier identity as an authorized success.
                self.value = None
                self.error = error
                self.until = self.clock() + max(2.0, float(getattr(error, 'retry_after', 0) or 0))
                raise
            self.value, self.error = value, None
            self.until = self.clock() + 30.0
            return value


_metadata = _MetadataCache()
_invalidation_connection = None
_invalidation_subscriptions = []
_invalidation_lock = threading.Lock()


def _subscribe_invalidations():
    global _invalidation_connection
    from gi.repository import Gio
    with _invalidation_lock:
        if _invalidation_connection is not None:
            return
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        callback = lambda *_: _metadata.invalidate()
        subscriptions = []
        try:
            subscriptions.append(connection.signal_subscribe(BUS, BUS,
                'StateChanged', PATH, None, Gio.DBusSignalFlags.NONE, callback))
            subscriptions.append(connection.signal_subscribe('org.freedesktop.DBus',
                'org.freedesktop.DBus', 'NameOwnerChanged', '/org/freedesktop/DBus',
                BUS, Gio.DBusSignalFlags.NONE, callback))
        except Exception:
            for subscription in subscriptions:
                connection.signal_unsubscribe(subscription)
            raise
        _invalidation_subscriptions.extend(subscriptions)
        _invalidation_connection = connection


def _fresh_identity():
    value = call('GetCollaborationContext')
    if 'error' in value:
        error = value['error']
        if not isinstance(error, dict) or not isinstance(error.get('status'), int):
            raise ConnectError('Connect returned an invalid collaboration error.')
        raise HubResponseError(error['status'], 'Connect collaboration refused the request',
            error.get('detail', ''), error.get('retry_after') or 0, error.get('code', ''))
    if set(value) != {'device_id', 'hub', 'name', 'registered_at'}:
        raise ConnectError('Connect returned an invalid collaboration context.')
    return DeviceIdentity(str(value['device_id']), '', str(value['hub']), str(value['name']), str(value['registered_at']))


def identity():
    # If real lifecycle invalidation cannot be installed, use fresh metadata;
    # never silently retain a cache without its sign-out/owner-change hooks.
    try:
        _subscribe_invalidations()
    except Exception:
        _metadata.invalidate()
        return _fresh_identity()
    return _metadata.get(_fresh_identity)


class BrokerHTTP:
    def request(self, method, url, payload):
        target = urlsplit(url)
        if target.query or target.fragment:
            raise ConnectError('Unsupported collaboration request.')
        result = call('CollaborationRequest', (method, target.path, json.dumps(payload, allow_nan=False)))
        if 'error' in result:
            error = result['error']
            raise HubResponseError(error['status'], 'Connect collaboration refused the request',
                                   error.get('detail', ''), error.get('retry_after'), error.get('code', ''))
        value = result.get('result')
        if not isinstance(value, dict): raise ConnectError('Connect returned an invalid response.')
        return value

    def get_json(self, url, *, token='', timeout=None): return self.request('GET', url, {})
    def post_json(self, url, payload, *, token=''): return self.request('POST', url, payload)
