# SPDX-License-Identifier: Apache-2.0
"""Bounded async reply ACKs over the existing python-gbinder API.

python-gbinder stores one callback pair per Client and passes raw Python userdata
to C. Each transaction therefore owns a separate, strongly retained Client until
its C destroy notification. A timeout/cancel is not proof that Android did not
execute the action. All methods and callbacks run on the GLib main context.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging

from .notification_reply import ReplyError

LOGGER = logging.getLogger(__name__)


@dataclass
class _Call:
    client: object
    done: object
    transaction: int = 0
    timer: int = 0
    notified: bool = False


class ReplyCalls:
    # C can retain userdata after a pool is closed. Global strong references
    # cover that lifetime and bound all pools in this process together.
    _live: dict[object, tuple['ReplyCalls', _Call]] = {}
    MAX_CALLS = 8
    TIMEOUT_MS = 15000

    def __init__(self, client_factory, schedule, unschedule):
        self.client_factory = client_factory
        self.schedule = schedule
        self.unschedule = unschedule
        self.closed = False

    def send(self, remote, descriptor, notification_id, action, request_id, text, done):
        if self.closed or len(self._live) >= self.MAX_CALLS:
            raise ReplyError('NotReady')
        # The caller validates text and capability before reaching transport.
        client = self.client_factory(remote, descriptor)
        request = client.new_request()
        request.append_int32(notification_id)
        for value in (action, request_id, text):
            request.append_string16(value)
        key = object()
        call = _Call(client, done)
        self._live[key] = self, call

        def notify(accepted):
            if call.notified:
                return
            call.notified = True
            if call.timer:
                self.unschedule(call.timer)
                call.timer = 0
            try:
                call.done(accepted)
            except Exception:
                LOGGER.warning('Could not complete an Android reply acknowledgement')

        def destroyed():
            notify(None)
            self._live.pop(key, None)
            # Break callback -> closure -> client cycles only after C releases
            # userdata. Dropping a Client at the UI timeout would be unsafe.
            client.reply_func = None
            client.destroy_notif = None

        def replied(reply, status):
            accepted = None
            if status == 0:
                try:
                    # RemoteReply convenience methods each start at byte zero.
                    # Use one cursor for the exception header and boolean body.
                    reader = reply.init_reader()
                    exception = reader.read_int32()
                    if exception == (True, 0):
                        result = reader.read_int32()
                        if result == (True, 1):
                            accepted = True
                except Exception:
                    pass
            notify(accepted)

        def expired():
            call.timer = 0
            notify(None)
            # libgbinder cancellation suppresses callbacks; its blocking worker
            # may still be running. Keep the Client until destroyed().
            client.cancel(call.transaction)
            return False

        began = False
        try:
            # Additive callback transaction4, synchronous wire semantics, async
            # host dispatch. Never use Listener's shared legacy action Client.
            began = True
            call.transaction = client.transact(4, 0, request, replied, destroyed)
            if not call.transaction:
                destroyed()
            elif key in self._live and not call.notified:
                call.timer = self.schedule(self.TIMEOUT_MS, expired)
        except Exception:
            notify(None)
            if call.transaction:
                client.cancel(call.transaction)
            elif not began:
                destroyed()
            # If the binding raised after entering C but before returning an
            # ID, completion is unknown. Keep userdata alive until C destroys
            # it; capacity still bounds these uncertain leases.

    def close(self):
        if self.closed:
            return
        self.closed = True
        for key, (pool, call) in tuple(self._live.items()):
            if pool is not self:
                continue
            if call.timer:
                self.unschedule(call.timer)
                call.timer = 0
            if not call.notified:
                call.notified = True
                try:
                    call.done(None)
                except Exception:
                    LOGGER.warning('Could not complete an Android reply acknowledgement')
            if key in self._live:
                call.client.cancel(call.transaction)
