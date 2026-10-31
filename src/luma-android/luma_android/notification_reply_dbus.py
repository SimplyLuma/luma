# SPDX-License-Identifier: Apache-2.0
"""Export replies on the same session-bus connection that produces notifications."""
from .notification_reply import ReplyError

PATH = '/org/projectluma/Android/Notifications'
INTERFACE = 'org.projectluma.Android.NotificationReply1'
CAPABILITY = 'x-luma-android-inline-reply-v1'


def export_reply_endpoint(bus, bridge):
    import dbus
    import dbus.service

    class Endpoint(dbus.service.Object):
        def __init__(self):
            self.owner = None
            self.closed = False
            super().__init__(bus, PATH)

        def set_owner(self, owner):
            owner = owner or None
            if owner != self.owner and self.owner is not None:
                bridge.invalidate_reply_grants()
            self.owner = owner

        @dbus.service.method(INTERFACE, in_signature='usss', out_signature='ss',
                             sender_keyword='sender', async_callbacks=('returned', 'failed'))
        def Reply(self, notification_id, token, request_id, text,
                  returned=None, failed=None, sender=None):
            try:
                if self.closed:
                    raise ReplyError('Unauthorized')
                bridge.reply_ledger.request(sender, self.owner, int(notification_id),
                                            str(token), str(request_id), str(text), returned)
            except ReplyError as error:
                failed(dbus.exceptions.DBusException(error.code,
                       name=INTERFACE + '.Error.' + error.code))
            except Exception:
                # Never expose user text, tokens or transport exception strings.
                failed(dbus.exceptions.DBusException('TransportFailure',
                       name=INTERFACE + '.Error.TransportFailure'))

        def close(self):
            self.closed = True
            bridge.invalidate_reply_grants()
            self.remove_from_connection()

    return Endpoint()
