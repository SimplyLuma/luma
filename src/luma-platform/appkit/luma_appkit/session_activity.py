# SPDX-License-Identifier: Apache-2.0
"""Observe session visibility through the standard sandbox-safe portal.

One monitor is shared by all interested windows in a process. Monitoring does
not inhibit idle, suspend or logout. An unavailable portal leaves rendering
enabled; applications must never strand visible content after a service loss.
"""
from gi.repository import Gio, GLib
import uuid

_NAME = 'org.freedesktop.portal.Desktop'
_PATH = '/org/freedesktop/portal/desktop'
_INTERFACE = 'org.freedesktop.portal.Inhibit'


class SessionActivity:
    """Main-context observer; subscribe returns an idempotent disconnect hook."""

    def __init__(self):
        self.screensaver_active = False
        self._listeners = {}
        self._watch = 0
        self._bus = None
        self._subscriptions = []
        self._cancel = None
        self._session = None
        self._request = None

    def subscribe(self, callback):
        token = object()
        self._listeners[token] = callback
        callback(self.screensaver_active)
        if not self._watch:
            self._watch = Gio.bus_watch_name(Gio.BusType.SESSION, _NAME,
                Gio.BusNameWatcherFlags.NONE, self._appeared, self._vanished)

        def disconnect():
            self._listeners.pop(token, None)
            if not self._listeners:
                self._reset()
                if self._watch:
                    Gio.bus_unwatch_name(self._watch)
                    self._watch = 0
        return disconnect

    def _publish(self, active):
        if self.screensaver_active == active:
            return
        self.screensaver_active = active
        for callback in tuple(self._listeners.values()):
            callback(active)

    @staticmethod
    def _close(bus, path, interface):
        if path:
            bus.call(_NAME, path, interface, 'Close', None, None,
                     Gio.DBusCallFlags.NONE, 5000, None, None)

    def _reset(self):
        if self._cancel:
            self._cancel.cancel()
            self._cancel = None
        if self._bus:
            for subscription in self._subscriptions:
                self._bus.signal_unsubscribe(subscription)
            self._close(self._bus, self._request, 'org.freedesktop.portal.Request')
            self._close(self._bus, self._session, 'org.freedesktop.portal.Session')
        self._subscriptions = []
        self._bus = self._session = self._request = None
        self._publish(False)

    def _vanished(self, *_):
        self._reset()

    def _appeared(self, bus, _name, _owner):
        self._reset()
        self._bus = bus
        cancel = self._cancel = Gio.Cancellable()
        token = 'luma_activity_' + uuid.uuid4().hex
        sender = bus.get_unique_name().lstrip(':').replace('.', '_')
        self._request = f'{_PATH}/request/{sender}/{token}'
        self._session = f'{_PATH}/session/{sender}/{token}'

        def state_changed(_bus, _sender, _path, _interface, _signal, parameters):
            session, state = parameters.unpack()
            if cancel is not self._cancel or session != self._session:
                return
            active = state.get('screensaver-active')
            if isinstance(active, bool):
                self._publish(active)
            if state.get('session-state') == 2:
                bus.call(_NAME, _PATH, _INTERFACE, 'QueryEndResponse',
                         GLib.Variant('(o)', (session,)), None,
                         Gio.DBusCallFlags.NONE, 1000, cancel, None)

        def closed(*_):
            if cancel is self._cancel:
                self._reset()

        def responded(_bus, _sender, _path, _interface, _signal, parameters):
            if cancel is not self._cancel:
                return
            response, results = parameters.unpack()
            self._request = None
            if response != 0 or results.get('session_handle') != self._session:
                self._reset()

        for interface, signal, path, arg0, callback in (
            (_INTERFACE, 'StateChanged', _PATH, None, state_changed),
            ('org.freedesktop.portal.Session', 'Closed', self._session, None, closed),
            ('org.freedesktop.portal.Request', 'Response', self._request, None, responded),
        ):
            self._subscriptions.append(bus.signal_subscribe(_NAME, interface,
                signal, path, arg0, Gio.DBusSignalFlags.NONE, callback))

        def created(connection, result):
            try:
                (request,) = connection.call_finish(result).unpack()
            except GLib.Error:
                if cancel is self._cancel:
                    self._reset()
                return
            if cancel is not self._cancel:
                self._close(connection, request, 'org.freedesktop.portal.Request')
            elif self._request is not None and request != self._request:
                self._close(connection, request, 'org.freedesktop.portal.Request')
                self._reset()

        bus.call(_NAME, _PATH, _INTERFACE, 'CreateMonitor',
            GLib.Variant('(sa{sv})', ('', {
                'handle_token': GLib.Variant('s', token),
                'session_handle_token': GLib.Variant('s', token),
            })), GLib.VariantType.new('(o)'), Gio.DBusCallFlags.NONE,
            5000, cancel, created)


_activity = SessionActivity()


def observe_session_activity(callback):
    """Call callback(bool) on screensaver changes; return a disconnect hook.

    Use this for optional visual work, not to stop audio, downloads or agents.
    The callback runs immediately, then only on changes on the main context.
    """
    return _activity.subscribe(callback)
