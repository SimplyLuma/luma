# SPDX-License-Identifier: Apache-2.0
"""Native mmsd-tng client. No daemon activation, APN changes, or modem ownership.

Interface reference: https://gitlab.com/kop316/mmsd/-/tree/master/doc
The application uses GetServices/GetMessages on discovery, then D-Bus signals.
Creating an MMS is queue acceptance; only the daemon's Status proves sending.
"""

from dataclasses import dataclass
from pathlib import Path
import re
import threading


@dataclass(frozen=True)
class MmsCapability:
    available: bool = False
    reason: str = "MMS attachments are unavailable on this device."
    service_path: str = ""
    max_bytes: int = 0
    max_attachments: int = 0


class MmsMessagingTransport:
    NAME = "org.ofono.mms"
    MANAGER = "/org/ofono/mms"

    def __init__(self, caller=None):
        self._caller = caller or self._call
        self._connection = None
        self._subscriptions = []
        self._watch = 0
        self._generation = 0
        self._messages = {}
        self._loading = False
        self._pending_signals = []
        self.capability = MmsCapability()

    @staticmethod
    def _call(path, interface, method, signature="()", values=()):
        from gi.repository import Gio, GLib
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        # Native service absence stays unavailable; launching a daemon is an
        # image/service lifecycle responsibility, not application bootstrap.
        result = connection.call_sync(
            MmsMessagingTransport.NAME, path, interface, method,
            GLib.Variant(signature, values), None,
            Gio.DBusCallFlags.NO_AUTO_START, 5000, None,
        ).unpack()
        return result[0] if result else None

    def inspect(self):
        try:
            services = self._caller(self.MANAGER, self.NAME + ".Manager", "GetServices")
            if len(services) != 1:
                return MmsCapability(reason="No single MMS service is ready.")
            path, _properties = services[0]
            if not isinstance(path, str) or not re.fullmatch(r"/org/ofono/mms/[A-Za-z0-9_/]+", path):
                return MmsCapability(reason="The MMS service returned an invalid identity.")
            properties = self._caller(path, self.NAME + ".Service", "GetProperties")
            max_bytes = int(properties.get("TotalMaxAttachmentSize", 0))
            max_parts = int(properties.get("MaxAttachments", 0))
            if max_bytes <= 0 or max_parts <= 0:
                return MmsCapability(reason="The MMS service has no valid attachment limits.")
            return MmsCapability(True, "MMS ready", path, max_bytes, max_parts)
        except Exception:
            return MmsCapability()

    def send(self, recipients, parts):
        """Queue files using the documented (as, v, a(sss)) SendMessage ABI."""
        from gi.repository import GLib
        from .messages_backend import normalize_address
        recipients = tuple(normalize_address(address) for address in recipients)
        if not recipients:
            raise ValueError("Choose an MMS recipient.")
        capability = self.capability if self.capability.available else self.inspect()
        if not capability.available:
            raise RuntimeError(capability.reason)
        self.validate_parts(capability, parts)
        path = self._caller(
            capability.service_path, self.NAME + ".Service", "SendMessage",
            "(asva(sss))", (list(recipients), GLib.Variant("a{sv}", {}), list(parts)),
        )
        if not isinstance(path, str) or not path.startswith(capability.service_path + "/"):
            raise RuntimeError("The MMS service did not return a valid message identity.")
        return path

    @staticmethod
    def validate_parts(capability, parts):
        if not parts or len(parts) > capability.max_attachments:
            raise ValueError("The draft exceeds the carrier's attachment count limit.")
        if sum(Path(part[2]).stat().st_size for part in parts) > capability.max_bytes:
            raise ValueError("The draft exceeds the carrier's MMS size limit.")

    def mark_read(self, path):
        self._caller(path, self.NAME + ".Message", "MarkRead")

    def delete(self, path):
        self._caller(path, self.NAME + ".Message", "Delete")

    def cached_message(self, path):
        properties = self._messages.get(path)
        return dict(properties) if properties is not None else None

    def start(self, on_message, on_capability):
        from gi.repository import Gio, GLib
        self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def publish(generation, capability, messages):
            if generation != self._generation:
                return False
            self.capability = capability
            self._messages = dict(messages)
            # Signals can arrive between GetMessages and its reply. Replay
            # them over the snapshot before exposing it to the application.
            for path, member, values in self._pending_signals:
                apply_signal(path, member, values)
            self._pending_signals.clear()
            self._loading = False
            on_capability(capability)
            for path, properties in self._messages.items():
                on_message(path, dict(properties))
            return False

        def discover(*_args):
            self._generation += 1
            generation = self._generation
            self._loading = True
            self._pending_signals.clear()

            def load():
                capability = self.inspect()
                messages = []
                if capability.available:
                    try:
                        messages = self._caller(capability.service_path, self.NAME + ".Service", "GetMessages")
                    except Exception:
                        capability = MmsCapability(reason="MMS history could not be loaded.")
                GLib.idle_add(publish, generation, capability, messages)
            threading.Thread(target=load, daemon=True).start()

        def vanished(*_args):
            self._generation += 1
            self._messages.clear()
            self._loading = False
            self._pending_signals.clear()
            self.capability = MmsCapability()
            on_capability(self.capability)

        def apply_signal(path, member, values):
            if member == "MessageAdded":
                message_path, properties = values
                self._messages[message_path] = dict(properties)
                return message_path
            elif member == "PropertyChanged" and path in self._messages:
                key, value = values
                self._messages[path][key] = value
                return path
            elif member == "MessageRemoved":
                self._messages.pop(values[0], None)

        def signal(_bus, _sender, path, interface, member, parameters, *_data):
            values = parameters.unpack()
            if member in {"ServiceAdded", "ServiceRemoved"}:
                discover()
            elif self._loading:
                self._pending_signals.append((path, member, values))
            elif changed := apply_signal(path, member, values):
                on_message(changed, dict(self._messages[changed]))

        self._subscriptions.append(self._connection.signal_subscribe(
            self.NAME, None, None, None, None, Gio.DBusSignalFlags.NONE, signal,
        ))
        self._watch = Gio.bus_watch_name_on_connection(
            self._connection, self.NAME, Gio.BusNameWatcherFlags.NONE, discover, vanished,
        )

    def stop(self):
        from gi.repository import Gio
        self._generation += 1
        self._loading = False
        self._pending_signals.clear()
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
            self._watch = 0
        if self._connection:
            for subscription in self._subscriptions:
                self._connection.signal_unsubscribe(subscription)
        self._subscriptions.clear()


def read_mms_part(filename: str, offset: int, length: int, *, root: Path, limit: int) -> bytes:
    """Read only a bounded attachment slice from the native daemon's store."""
    import os
    import stat
    path = Path(filename)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("MMS content is outside the native service's storage.")
    if offset < 0 or not 0 < length <= limit:
        raise ValueError("Invalid MMS attachment bounds.")
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or offset + length > info.st_size:
            raise ValueError("The MMS attachment is incomplete.")
        stream.seek(offset)
        data = stream.read(length)
        if len(data) != length:
            raise ValueError("The MMS attachment changed while it was read.")
        return data
