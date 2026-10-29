# Copyright 2025 Alessandro Astone
# SPDX-License-Identifier: GPL-3.0-or-later
"""Luma's session notification transport, derived from Waydroid 1.6.3.

Preserves the existing Binder wire ABI. Callback transactions 2 and 3 are
additive and require the matching Luma Android framework patch. Old images
keep their existing action support. No APK receives the host session bus.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from .notification_bridge import Notification, NotificationBridge, escaped_body

LOGGER = logging.getLogger(__name__)
INTERFACE = 'lineageos.waydroid.INotifications'
CALLBACK = INTERFACE + '.INotificationCallback'
SYSTEM_UID = 1000


class Listener:
    def __init__(self, remote, bridge):
        import gbinder
        self.remote = remote
        self.client = gbinder.Client(remote, CALLBACK)
        self.bridge = bridge
        self.death = remote.add_death_handler(lambda *_: bridge.unregister(self))

    def send(self, code, values):
        request = self.client.new_request()
        for kind, value in values:
            getattr(request, 'append_' + kind)(value)
        self.client.transact(code, 1, request, lambda *_: None, lambda *_: None)

    def action(self, notification_id, action, token):
        self.send(1, (('int32', notification_id), ('string16', action), ('string16', token)))

    def dismissed(self, notification_id):
        self.send(2, (('int32', notification_id),))

    def reset(self):
        self.send(3, ())


class Sink:
    def __init__(self, bus):
        import dbus
        self.dbus = dbus
        self.proxy = dbus.Interface(bus.get_object('org.freedesktop.Notifications',
            '/org/freedesktop/Notifications', introspect=False,
            follow_name_owner_changes=True),
            'org.freedesktop.Notifications')

    def notify(self, record, replacement):
        dbus = self.dbus
        hints = {'desktop-entry': 'waydroid.' + record.package,
                 'resident': dbus.Boolean(record.resident),
                 'transient': dbus.Boolean(record.transient),
                 'urgency': dbus.Byte(record.urgency),
                 'suppress-sound': dbus.Boolean(record.suppress_sound)}
        if record.category:
            hints['category'] = record.category
        if record.image is not None:
            image = record.image
            hints['image-data'] = dbus.Struct((image.width, image.height, image.rowstride,
                image.has_alpha, 8, 4 if image.has_alpha else 3,
                dbus.Array(image.data, signature='y')))
        return self.proxy.Notify(record.app_name, dbus.UInt32(replacement), '',
            record.summary, escaped_body(record),
            dbus.Array([value for pair in record.actions for value in pair], signature='s'),
            dbus.Dictionary(hints, signature='sv'), record.timeout, timeout=5)

    def close(self, notification_id):
        self.proxy.CloseNotification(self.dbus.UInt32(notification_id), timeout=5)


def admitted(request) -> bool:
    # Credentials come from the Binder kernel transaction, never the parcel.
    # This transport is for the current Waydroid user-zero/system_server ABI.
    # A future UID-remapped runtime requires a separately admitted transport.
    return request.sender_euid() == SYSTEM_UID


def read_notification(reader):
    def number(method='read_int32'):
        success, value = getattr(reader, method)()
        if not success:
            raise ValueError('Truncated Android notification')
        return value

    replacement = number()
    app, package, summary, body = (reader.read_string16() for _ in range(4))
    count = number()
    if not 0 <= count <= 32:
        raise ValueError('Too many Android notification actions')
    actions = []
    for _ in range(count):
        if not number():
            raise ValueError('Missing Android notification action')
        size = number()
        if size < 4:
            raise ValueError('Invalid Android notification action')
        actions.append((reader.read_string16(), reader.read_string16()))
    image = None
    if number():
        if number() < 4:
            raise ValueError('Invalid Android notification image')
        image = SimpleNamespace(width=number(), height=number(), rowstride=number(),
                                has_alpha=number('read_bool'), data=reader.read_byte_array())
    category = reader.read_string16()
    suppress = number('read_bool')
    timeout = number()
    resident, transient = number('read_bool'), number('read_bool')
    urgency = number('read_byte')
    if replacement < 0:
        raise ValueError('Invalid Android replacement ID')
    return Notification(package, app, summary, body, tuple(actions), category,
                        urgency, resident, transient, suppress, timeout, image), replacement


def add_service(args, bridge, stopped):
    import gbinder
    from gi.repository import GLib
    from tools import helpers
    helpers.drivers.loadBinderNodes(args)
    try:
        manager = gbinder.ServiceManager('/dev/' + args.BINDER_DRIVER,
            args.SERVICE_MANAGER_PROTOCOL, args.BINDER_PROTOCOL)
    except TypeError:
        manager = gbinder.ServiceManager('/dev/' + args.BINDER_DRIVER)

    def handle(request, code, flags):
        reply = local.new_reply()
        if not admitted(request):
            return reply, -1  # PERMISSION_DENIED, before reading any app data.
        try:
            reader = request.init_reader()
            if code == 1:
                remote = reader.read_object()
                if remote is None or len(bridge.listeners) >= 4:
                    return reply, -22
                bridge.register(Listener(remote, bridge))
            elif code == 2:
                record, replacement = read_notification(reader)
                notification_id = bridge.post(record, replacement)
                reply.append_int32(0)
                reply.append_int32(notification_id)
                return reply, 0
            elif code == 3:
                success, notification_id = reader.read_int32()
                if not success:
                    return reply, -22
                bridge.withdraw(notification_id)
            else:
                return reply, -38
        except Exception:
            # Notification content is private. Do not print app text/parcels.
            LOGGER.warning('Android notification operation failed (transaction %s)', code)
            return reply, -22
        reply.append_int32(0)
        return reply, 0

    local = manager.new_local_object(INTERFACE, handle)
    loop = GLib.MainLoop()
    args.notificationLoop = loop

    def presence(*_):
        if stopped.is_set() or not manager.is_present():
            return
        if manager.add_service_sync('waydroidnotifications', local):
            loop.quit()

    presence()
    handler = manager.add_presence_handler(presence)
    try:
        if handler and not stopped.is_set():
            loop.run()
    finally:
        if handler:
            manager.remove_handler(handler)


def start(args, session):
    import dbus
    import threading
    bus = dbus.SessionBus()
    bridge = NotificationBridge(Sink(bus))
    signals = []
    proxy = bridge.sink.proxy
    signals.append(proxy.connect_to_signal('ActivationToken', bridge.activation_token))
    signals.append(proxy.connect_to_signal('ActionInvoked', bridge.action))
    signals.append(proxy.connect_to_signal('NotificationClosed', bridge.closed))
    owner = bus.watch_name_owner('org.freedesktop.Notifications', lambda *_: bridge.server_changed())
    stopped = threading.Event()

    def run():
        try:
            add_service(args, bridge, stopped)
        except Exception:
            LOGGER.exception('Android notification transport stopped')

    thread = threading.Thread(target=run, name='luma-android-notifications')
    args.notification_manager = thread
    args.luma_notifications = (stopped, signals, owner, bridge)
    thread.start()


def stop(args):
    state = getattr(args, 'luma_notifications', None)
    if state is None:
        return
    stopped, signals, owner, bridge = state
    stopped.set()
    owner.cancel()
    for signal in signals:
        signal.remove()
    if getattr(args, 'notificationLoop', None):
        args.notificationLoop.quit()
    for notification_id in tuple(bridge.records):
        try:
            bridge.withdraw(notification_id)
        except Exception:
            LOGGER.warning('Could not retire an Android notification at session stop')


def main():
    import os
    import sys
    # Fedora's provider-owned Python implementation. Do not replace its files
    # or change the upstream global session; this adapter owns Luma sessions.
    sys.path.insert(0, '/usr/lib/waydroid')
    import tools
    import tools.services.notification_manager as notifications
    from tools.interfaces import IClipboard, IUserMonitor
    from luma_android.waydroid_hardware import install_session_guards
    install_session_guards(IClipboard, IUserMonitor)
    notifications.start = start
    notifications.stop = stop
    sys.argv = ['waydroid', 'session', 'start']
    os.umask(0o022)
    return tools.main()
