# Copyright 2025 Alessandro Astone
# SPDX-License-Identifier: GPL-3.0-or-later
"""Luma's session notification transport, derived from Waydroid 1.6.3.

Preserves the existing Binder wire ABI. Callback transactions 2 through 4 and
service transactions 4/5 are additive and require matching Android framework
patches. Inline replies require native Shell capability negotiation. Old images
keep their existing action support. No APK receives the host session bus.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from types import SimpleNamespace

from .notification_bridge import Notification, NotificationBridge, escaped_body
from .notification_reply import ReplyError
from .notification_reply_dbus import CAPABILITY, PATH as REPLY_PATH, export_reply_endpoint
from .notification_reply_transport import ReplyCalls

LOGGER = logging.getLogger(__name__)
INTERFACE = 'lineageos.waydroid.INotifications'
CALLBACK = INTERFACE + '.INotificationCallback'
SYSTEM_UID = 1000


class Listener:
    def __init__(self, remote, bridge, origin_pid=None):
        import gbinder
        self.remote = remote
        self.client = gbinder.Client(remote, CALLBACK)
        self.bridge = bridge
        self.origin_pid = origin_pid
        self.reply_calls = None
        self.dead = False
        self.death = remote.add_death_handler(lambda *_: self.close())

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

    def reply(self, notification_id, action, request_id, text, done):
        if self.dead:
            raise ReplyError('NotReady')
        if self.reply_calls is None:
            import gbinder
            from gi.repository import GLib
            self.reply_calls = ReplyCalls(gbinder.Client, GLib.timeout_add, GLib.source_remove)
        self.reply_calls.send(self.remote, CALLBACK, notification_id, action, request_id, text, done)

    def close(self):
        self.dead = True
        self.bridge.unregister(self)
        if self.reply_calls is not None:
            self.reply_calls.close()



class Sink:
    def __init__(self, bus):
        import dbus
        self.dbus = dbus
        self.proxy = dbus.Interface(bus.get_object('org.freedesktop.Notifications',
            '/org/freedesktop/Notifications', introspect=False,
            follow_name_owner_changes=True),
            'org.freedesktop.Notifications')
        self.supports_inline_reply = False

    def refresh_capabilities(self):
        try:
            self.supports_inline_reply = CAPABILITY in self.proxy.GetCapabilities(timeout=5)
        except Exception:
            self.supports_inline_reply = False

    def notify(self, record, replacement, reply_token=""):
        dbus = self.dbus
        hints = {'desktop-entry': 'waydroid.' + record.package,
                 'resident': dbus.Boolean(record.resident),
                 'transient': dbus.Boolean(record.transient),
                 'urgency': dbus.Byte(record.urgency),
                 'suppress-sound': dbus.Boolean(record.suppress_sound)}
        if reply_token:
            if not self.supports_inline_reply:
                raise ValueError("Native Android inline reply is unavailable")
            hints['x-luma-inline-reply-path'] = dbus.ObjectPath(REPLY_PATH)
            hints['x-luma-inline-reply-token'] = reply_token
        if record.category:
            hints['category'] = record.category
        if record.image is not None:
            image = record.image
            hints['image-data'] = dbus.Struct((image.width, image.height, image.rowstride,
                image.has_alpha, 8, 4 if image.has_alpha else 3,
                dbus.ByteArray(image.data)))
        return self.proxy.Notify(record.app_name, dbus.UInt32(replacement), '',
            record.summary, escaped_body(record),
            dbus.Array([value for pair in record.actions if pair[0] != record.reply_action
                        for value in pair], signature='s'),
            dbus.Dictionary(hints, signature='sv'), record.timeout, timeout=5)

    def close(self, notification_id):
        self.proxy.CloseNotification(self.dbus.UInt32(notification_id), timeout=5)


class NotificationServerEpoch:
    """Track the server that owns notification IDs, including Luma's forwarder."""
    def __init__(self, bridge, endpoint):
        self.bridge = bridge
        self.endpoint = endpoint
        self.owners = {}
        self.epoch = None

    def changed(self, name, owner):
        self.owners[name] = owner or None
        if name == 'org.gnome.Shell':
            self.endpoint.set_owner(owner)
        if name == 'org.freedesktop.Notifications' and owner:
            self.bridge.sink.refresh_capabilities()
        # Keep the last negotiated capability across forwarder disappearance.
        # Its temporary absence does not destroy notifications held by Shell.
        authority = ('org.gnome.Shell' if self.bridge.sink.supports_inline_reply
                     else 'org.freedesktop.Notifications')
        current = authority, self.owners.get(authority)
        if current != self.epoch:
            self.epoch = current
            self.bridge.server_changed()


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
    # AIDL/Parcel.writeByte writes a padded int32. A raw gbinder read_byte
    # advances only one byte and corrupts the appended reply-action string.
    urgency = number()
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
                bridge.register(Listener(remote, bridge, request.sender_pid()))
            elif code in (2, 5):
                record, replacement = read_notification(reader)
                listener = None
                if code == 5:
                    if not bridge.sink.supports_inline_reply or not bridge.reply_endpoint.owner:
                        raise ValueError('Native Android inline reply is unavailable')
                    record = replace(record, reply_action=reader.read_string16())
                    if not record.reply_action:
                        raise ValueError('Missing Android reply action')
                    listener = next((item for item in bridge.listeners
                                     if item.origin_pid == request.sender_pid() and not item.dead), None)
                    if listener is None:
                        raise ValueError('No matching Android reply listener')
                notification_id = bridge.post(record, replacement, listener)
                reply.append_int32(0)
                reply.append_int32(notification_id)
                return reply, 0
            elif code == 4:
                reply.append_int32(0)
                capable = bridge.sink.supports_inline_reply and bridge.reply_endpoint.owner
                reply.append_int32(1 if capable else 0)
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
    from .app_settings_transport import SERVICE as SETTINGS_SERVICE, export as export_settings
    settings_local, settings_requests = export_settings(manager, GLib)
    args.luma_app_settings = (settings_local, settings_requests)
    loop = GLib.MainLoop()
    args.notificationLoop = loop

    def presence(*_):
        if stopped.is_set() or not manager.is_present():
            return
        notifications_ready = manager.add_service_sync('waydroidnotifications', local)
        settings_ready = manager.add_service_sync(SETTINGS_SERVICE, settings_local)
        if notifications_ready and settings_ready:
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
    endpoint = export_reply_endpoint(bus, bridge)
    bridge.reply_endpoint = endpoint
    epoch = NotificationServerEpoch(bridge, endpoint)
    owner = bus.watch_name_owner('org.freedesktop.Notifications',
                                lambda value: epoch.changed('org.freedesktop.Notifications', value))
    shell_owner = bus.watch_name_owner('org.gnome.Shell',
                                      lambda value: epoch.changed('org.gnome.Shell', value))
    stopped = threading.Event()

    def run():
        try:
            add_service(args, bridge, stopped)
        except Exception:
            LOGGER.exception('Android notification transport stopped')

    thread = threading.Thread(target=run, name='luma-android-notifications')
    args.notification_manager = thread
    args.luma_notifications = (stopped, signals, owner, bridge, shell_owner, endpoint)
    thread.start()


def stop(args):
    state = getattr(args, 'luma_notifications', None)
    if state is None:
        return
    stopped, signals, owner, bridge, shell_owner, endpoint = state
    stopped.set()
    owner.cancel()
    shell_owner.cancel()
    endpoint.close()
    for listener in tuple(bridge.listeners):
        listener.close()
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
    import tools.services.clipboard_manager as clipboard
    from tools.interfaces import IClipboard, IUserMonitor
    from luma_android.waydroid_hardware import install_session_guards, disable_legacy_clipboard
    install_session_guards(IClipboard, IUserMonitor)
    disable_legacy_clipboard(clipboard)
    notifications.start = start
    notifications.stop = stop
    sys.argv = ['waydroid', 'session', 'start']
    os.umask(0o022)
    return tools.main()
