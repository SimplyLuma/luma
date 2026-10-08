# SPDX-License-Identifier: Apache-2.0
"""Charlie protocol facade; the UI never retrieves stored credentials."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
import threading
import time
from gi.repository import Gio, GLib
from .engine import AuthenticationFailure
from .host_mail_wire import BUS, OBJECT, MAX_ATTACHMENTS, encode, decode


class Client:
    def __init__(self):
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.closed = threading.Event(); self._tokens = set(); self._lock = threading.Lock()

    def _call(self, method, signature, values, reply="(s)", timeout=10000):
        result = self.connection.call_sync(BUS, OBJECT, BUS, method, GLib.Variant(signature, values), GLib.VariantType.new(reply) if reply else None, Gio.DBusCallFlags.NONE, timeout, None)
        return result.unpack()[0] if reply else None

    def request(self, operation, value):
        if self.closed.is_set(): raise RuntimeError("Mail client closed.")
        token = self._call("Start", "(ss)", (operation, encode(value)))
        with self._lock: self._tokens.add(token)
        try:
            deadline = time.monotonic() + 360
            while time.monotonic() < deadline:
                if self.closed.is_set(): raise RuntimeError("Mail operation cancelled.")
                result = decode(self._call("Poll", "(s)", (token,)))
                if result.get("state") == "done": return result["value"]
                if result.get("state") == "error":
                    if result.get("kind") == "authentication": raise AuthenticationFailure("Sign in again.")
                    raise OSError("The mail service could not complete the request.")
                if result.get("state") != "running": raise ValueError("Invalid mail response.")
                self.closed.wait(0.2)
            raise TimeoutError("The mail operation timed out.")
        finally:
            with self._lock: self._tokens.discard(token)
            try: self._call("Cancel", "(s)", (token,), reply=None, timeout=500)
            except GLib.Error: pass

    def close(self):
        self.closed.set()
        with self._lock: tokens = tuple(self._tokens)
        for token in tokens:
            self.connection.call(BUS, OBJECT, BUS, "Cancel", GLib.Variant("(s)", (token,)), None, Gio.DBusCallFlags.NONE, 500, None, None, None)



class Engine:
    def __init__(self, client):
        self.client = client; self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="charlie-ui-mail")
        self.pending = 0; self.lock = threading.Lock()

    def _submit(self, operation, data, complete):
        with self.lock:
            if self.pending >= 8: raise RuntimeError("Mail service is busy.")
            self.pending += 1
        try: future = self.pool.submit(self.client.request, operation, data)
        except Exception:
            with self.lock: self.pending -= 1
            raise
        def done(value):
            with self.lock: self.pending -= 1
            try: result, error = value.result(), None
            except Exception as failure: result, error = None, failure
            if not self.client.closed.is_set(): complete(result, error)
        future.add_done_callback(done); return future

    def sync(self, account, config, complete):
        return self._submit("Sync", {"account_id": account.id}, lambda _value, error: complete(() if error is None else None, error))

    def mark_read(self, messages, read, configs=None):
        ids = [message.id if hasattr(message, "id") else message for message in messages]
        return self._submit("MarkRead", {"message_ids": ids, "read": read}, lambda *_a: None)

    def send(self, account, config, draft, complete):
        value = asdict(draft); attachments = []; total = 0
        for filename in draft.attachments:
            path = Path(filename)
            with path.open("rb") as stream: content = stream.read(MAX_ATTACHMENTS + 1)
            total += len(content)
            if total > MAX_ATTACHMENTS: raise ValueError("Attachments exceed the supported size.")
            attachments.append({"filename": path.name, "data": base64.b64encode(content).decode("ascii")})
        value["attachments"] = attachments
        return self._submit("Send", {"draft": value}, lambda _value, error: complete(error))

    def close(self):
        self.client.close(); self.pool.shutdown(wait=False, cancel_futures=True)
