# SPDX-License-Identifier: Apache-2.0
"""Targeted notification SMS replies, hosted by the existing Messages publisher."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import re
import sqlite3

from .messages_backend import MessageStore, ModemMessagingTransport


class ReplyError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate_reply(token, request_id, text):
    if (not re.fullmatch(r"[0-9a-f]{32}", token)
            or not re.fullmatch(r"[0-9a-f-]{32,36}", request_id)
            or not text.strip() or "\x00" in text or len(text.encode("utf-8")) > 4096):
        raise ReplyError("InvalidInput")


class ReplySender:
    """Durable deduplication; uncertain modem outcomes are never retried blindly."""
    def __init__(self, path, transport_factory=ModemMessagingTransport):
        self.path = path
        self.transport_factory = transport_factory

    def send(self, address, token, request_id, text, authorized=lambda: True, retry_of=None):
        validate_reply(token, request_id, text)
        if retry_of is not None:
            validate_reply(token, retry_of, text)
            if retry_of == request_id:
                raise ReplyError("InvalidInput")
            return self._retry(address, token, request_id, text.strip(), authorized, retry_of)
        text = text.strip()
        uid = "inline:" + hashlib.sha256((token + ":" + request_id).encode()).hexdigest()
        body_hash = hmac.new(bytes.fromhex(token), text.encode(), "sha256").hexdigest()
        store = MessageStore(self.path)
        try:
            if not authorized():
                raise ReplyError("Unauthorized")
            store._connection.execute("""CREATE TABLE IF NOT EXISTS notification_reply_requests
                (uid TEXT PRIMARY KEY, address TEXT NOT NULL, body_hash TEXT NOT NULL)""")
            store._connection.execute("""CREATE TABLE IF NOT EXISTS notification_reply_attempts
                (uid TEXT PRIMARY KEY, origin TEXT NOT NULL, outcome TEXT NOT NULL)""")
            store._connection.commit()
            address = store.canonical_address(address)

            def previous():
                if store._connection.execute(
                    "SELECT 1 FROM notification_reply_attempts WHERE uid=? AND origin!=uid", (uid,)
                ).fetchone():
                    raise ReplyError("RequestConflict")
                row = store._connection.execute(
                    "SELECT address,body_hash FROM notification_reply_requests WHERE uid=?", (uid,)
                ).fetchone()
                if row is None:
                    return None
                if row[0] != address or not hmac.compare_digest(row[1], body_hash):
                    raise ReplyError("RequestConflict")
                try:
                    message = store.message(uid)
                except KeyError:
                    raise ReplyError("NoLongerAvailable") from None
                return uid, "sent" if message.state == "sent" else "unknown"

            if result := previous():
                return result
            transport = self.transport_factory()
            if not transport.inspect().available:
                raise ReplyError("NotReady")
            if not authorized():
                raise ReplyError("Unauthorized")
            try:
                # MessageStore.add commits both inserts as one transaction.
                # The request record survives message deletion to prevent replay.
                store._connection.execute("BEGIN IMMEDIATE")
                if result := previous():
                    store._connection.rollback()
                    return result
                with store._connection:
                    store._connection.execute("INSERT INTO notification_reply_requests VALUES(?,?,?)",
                                              (uid, address, body_hash))
                    store._connection.execute("INSERT INTO notification_reply_attempts VALUES(?,?,'pending')",
                                              (uid, uid))
                    store.add(address, text, direction="outgoing", state="queued", uid=uid)
            except sqlite3.IntegrityError:
                result = previous()
                if result is None:
                    raise ReplyError("StorageFailure") from None
                return result
            store.update_state(uid, "sending")
            if not authorized():
                store.update_state(uid, "failed")
                store._connection.execute("UPDATE notification_reply_attempts SET outcome='cancelled' WHERE uid=?", (uid,))
                store._connection.commit()
                raise ReplyError("Unauthorized")
            try:
                transport.send(address, text)
            except Exception:
                # Send may have reached the modem. Retain queued content for
                # existing reconciliation; no fabricated delivery failure.
                store.update_state(uid, "queued")
                store.mark_send_uncertain(uid)
                store._connection.execute("UPDATE notification_reply_attempts SET outcome='unknown' WHERE uid=?", (uid,))
                store._connection.commit()
                return uid, "unknown"
            store.update_state(uid, "sent", f"mm-outgoing:v1:{uid}")
            store._connection.execute("UPDATE notification_reply_attempts SET outcome='sent' WHERE uid=?", (uid,))
            store._connection.commit()
            return uid, "sent"
        finally:
            store.close()

    def _retry(self, address, token, request_id, text, authorized, retry_of):
        origin = "inline:" + hashlib.sha256((token + ":" + retry_of).encode()).hexdigest()
        attempt = "inline:" + hashlib.sha256((token + ":" + request_id).encode()).hexdigest()
        body_hash = hmac.new(bytes.fromhex(token), text.encode(), "sha256").hexdigest()
        store = MessageStore(self.path)
        try:
            if not authorized():
                raise ReplyError("Unauthorized")
            address = store.canonical_address(address)
            store._connection.execute("""CREATE TABLE IF NOT EXISTS notification_reply_attempts
                (uid TEXT PRIMARY KEY, origin TEXT NOT NULL, outcome TEXT NOT NULL)""")
            store._connection.execute("""CREATE TABLE IF NOT EXISTS notification_reply_requests
                (uid TEXT PRIMARY KEY, address TEXT NOT NULL, body_hash TEXT NOT NULL)""")
            store._connection.commit()
            store._connection.execute("BEGIN IMMEDIATE")
            try:
                original = store._connection.execute(
                    "SELECT address,body_hash FROM notification_reply_requests WHERE uid=?", (origin,)
                ).fetchone()
                if original is None:
                    raise ReplyError("NoLongerAvailable")
                if original[0] != address or not hmac.compare_digest(original[1], body_hash):
                    raise ReplyError("RequestConflict")
                try:
                    record = store.message(origin)
                except KeyError:
                    raise ReplyError("NoLongerAvailable") from None
                if record.address != address or record.body != text:
                    raise ReplyError("RequestConflict")
                old = store._connection.execute(
                    "SELECT origin FROM notification_reply_attempts WHERE uid=?", (attempt,)
                ).fetchone()
                if old and old[0] != origin:
                    raise ReplyError("RequestConflict")
                if record.state == "sent" or old:
                    store._connection.rollback()
                    return origin, "sent" if record.state == "sent" else "unknown"
                # Never turn a previously used original request into a retry.
                if store._connection.execute(
                    "SELECT 1 FROM notification_reply_requests WHERE uid=?", (attempt,)
                ).fetchone():
                    raise ReplyError("RequestConflict")
                if record.send_status != "uncertain":
                    raise ReplyError("NotRetryable")
                if store._connection.execute(
                    "SELECT 1 FROM notification_reply_attempts WHERE origin=? AND outcome='pending'", (origin,)
                ).fetchone():
                    raise ReplyError("Busy")
                if not authorized():
                    raise ReplyError("Unauthorized")
                store._connection.execute("INSERT INTO notification_reply_attempts VALUES (?,?,'pending')",
                                          (attempt, origin))
                store._connection.commit()
            except Exception:
                store._connection.rollback()
                raise
            # Journal is durable before even constructing the transport.
            outcome = "unknown"
            try:
                transport = self.transport_factory()
                if not transport.inspect().available:
                    raise ReplyError("NotReady")
                if not authorized():
                    raise ReplyError("Unauthorized")
                transport.send(address, text)
            except Exception:
                store.mark_send_uncertain(origin)
            else:
                store.update_state(origin, "sent", f"mm-outgoing:v1:{origin}")
                outcome = "sent"
            store._connection.execute("UPDATE notification_reply_attempts SET outcome=? WHERE uid=?",
                                      (outcome, attempt))
            store._connection.commit()
            return origin, outcome
        finally:
            store.close()


class ReplyEndpoint:
    PATH = "/org/projectluma/Messages/Notifications"
    INTERFACE = "org.projectluma.Messages.NotificationReply1"
    XML = '''<node><interface name="org.projectluma.Messages.NotificationReply1">
      <method name="Reply"><arg type="u" direction="in"/><arg type="s" direction="in"/>
        <arg type="s" direction="in"/><arg type="s" direction="in"/>
        <arg type="s" direction="out"/><arg type="s" direction="out"/></method>
    </interface></node>'''

    def __init__(self, connection, path, resolve, invalidate=lambda: None):
        from gi.repository import Gio
        self.connection = connection
        self.resolve = resolve
        self.invalidate = invalidate
        self.sender = ReplySender(path)
        self.owner = None
        self.generation = 0
        self.closed = False
        self.pending = 0
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="messages-reply")
        self.registration = connection.register_object(
            self.PATH, Gio.DBusNodeInfo.new_for_xml(self.XML).interfaces[0], self._call, None, None)
        self.watch = Gio.bus_watch_name_on_connection(
            connection, "org.gnome.Shell", Gio.BusNameWatcherFlags.NONE,
            lambda _connection, _name, owner: self._owner_changed(owner),
            lambda *_args: self._owner_changed(None))

    def _owner_changed(self, owner):
        if self.owner is not None:
            self.invalidate()
        self.owner = owner
        self.generation += 1

    def _error(self, invocation, code):
        # Stable, content-free errors. Never return exception text from storage
        # or the transport: it can contain message content or recipient details.
        invocation.return_dbus_error(self.INTERFACE + ".Error." + code, code)

    def _call(self, _connection, sender, _path, _interface, method, parameters, invocation):
        from gi.repository import GLib
        if self.closed or not self.owner or sender != self.owner:
            self._error(invocation, "Unauthorized")
            return
        if method != "Reply":
            self._error(invocation, "InvalidInput")
            return
        notification_id, token, request_id, text = parameters.unpack()
        try:
            validate_reply(token, request_id, text)
            address = self.resolve(notification_id, token)
            if address is None:
                raise ReplyError("StaleNotification")
            if self.pending >= 8:
                raise ReplyError("Busy")
        except ReplyError as error:
            self._error(invocation, error.code)
            return
        generation = self.generation

        def authorized():
            return (not self.closed and self.generation == generation and self.owner == sender
                    and self.resolve(notification_id, token) == address)

        self.pending += 1
        future = self.worker.submit(self.sender.send, address, token, request_id, text, authorized)
        future.add_done_callback(lambda result: GLib.idle_add(self._complete, result, invocation, authorized))

    def _complete(self, future, invocation, authorized):
        from gi.repository import GLib
        self.pending -= 1
        if not authorized():
            self._error(invocation, "Unauthorized")
            return False
        try:
            result = future.result()
        except ReplyError as error:
            self._error(invocation, error.code)
        except Exception:
            self._error(invocation, "StorageFailure")
        else:
            invocation.return_value(GLib.Variant("(ss)", result))
        return False

    def close(self):
        from gi.repository import Gio
        self.closed = True
        self.generation += 1
        Gio.bus_unwatch_name(self.watch)
        self.connection.unregister_object(self.registration)
        self.worker.shutdown(wait=False, cancel_futures=True)
