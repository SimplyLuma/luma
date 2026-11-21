"""An application's saved passwords: its own entries in the keyring, no others.

A capsule used to be let through to `org.freedesktop.secrets` itself, and the
Secret Service has no notion of a caller: any application could list, read
and delete every saved password of the person and of every other application.

What an application reaches now is a broker that answers to the Secret
Service's name on the application's bus and forwards to the real service. It
keeps the application to its own entries:

* Every entry the application stores is given one more attribute naming the
  application (`ATTRIBUTE`), and every search is narrowed by it. The entries
  stay in the person's login keyring, encrypted and unlocked as before, so
  nothing new asks for a password.
* An entry is read, changed or deleted only when it carries the application's
  attribute. The attribute never shows in what the application reads back.
* The application sees one collection, the default one, holding only its own
  entries. It cannot lock the person's keyring, delete it or move its alias.

The session that carries secrets is opened by the application with the real
service, so its key exchange is end to end; the broker forwards the encrypted
values without being able to read them.

**Reaching the broker.** The filtered bus (xdg-dbus-proxy) cannot answer to a
name that another process owns on the session bus, so the broker owns a name
of its own there, exactly as long as `org.freedesktop.secrets`, and a relay in
front of the application's bus socket exchanges the two names in each message
that passes. Names are length-prefixed strings in D-Bus, so messages keep
their size and nothing is re-encoded. This covers every client, not only libsecret:
Chromium talks to the service directly.

**Entries saved before this.** An application installed earlier has entries
without the attribute. While its record allows it (`adopt`), a search that
finds nothing among the application's own entries looks for an older entry
whose attributes the search names in full, and marks that entry as the
application's. Searches from the session itself still find it.
"""
from __future__ import annotations

import array
import os
import socket
import struct
import threading
from collections.abc import Callable
from pathlib import Path

SERVICE = "org.freedesktop.secrets"
ROOT = "/org/freedesktop/secrets"
DEFAULT_ALIAS = ROOT + "/aliases/default"
ATTRIBUTE = "luma:application"
SCHEMA_ATTRIBUTE = "xdg:schema"

SERVICE_INTERFACE = "org.freedesktop.Secret.Service"
COLLECTION_INTERFACE = "org.freedesktop.Secret.Collection"
ITEM_INTERFACE = "org.freedesktop.Secret.Item"
SESSION_INTERFACE = "org.freedesktop.Secret.Session"
PROMPT_INTERFACE = "org.freedesktop.Secret.Prompt"
PROPERTIES = "org.freedesktop.DBus.Properties"
ITEM_ATTRIBUTES = ITEM_INTERFACE + ".Attributes"

NO_SUCH_OBJECT = "org.freedesktop.Secret.Error.NoSuchObject"
NOT_SUPPORTED = "org.freedesktop.DBus.Error.NotSupported"
ACCESS_DENIED = "org.freedesktop.DBus.Error.AccessDenied"
UNKNOWN_METHOD = "org.freedesktop.DBus.Error.UnknownMethod"

_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def bus_name(pair: tuple[int, int]) -> str:
    """The broker's session bus name for the application with this category pair.

    Exactly as long as the Secret Service's own name, so the relay can
    exchange one for the other without changing the size of a message.
    """
    def three(value: int) -> str:
        return _DIGITS[value // 1296 % 36] + _DIGITS[value // 36 % 36] + _DIGITS[value % 36]
    name = f"io.luma.Secrets.s{three(pair[0])}{three(pair[1])}"
    assert len(name) == len(SERVICE)
    return name


# -- The relay ---------------------------------------------------------------

_MAX_DESCRIPTORS = 253
_MAX_MESSAGE = 1 << 27


def _descriptors(ancillary: list[tuple[int, int, bytes]]) -> list[int]:
    found = array.array("i")
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            found.frombytes(data[:len(data) - len(data) % found.itemsize])
    return list(found)


def _send(sink: socket.socket, data: bytes, descriptors: list[int]) -> None:
    """Send `data`, with any descriptors on its first byte, and close them."""
    try:
        if descriptors:
            sent = sink.sendmsg([data], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                          array.array("i", descriptors).tobytes())])
            data = data[sent:]
        if data:
            sink.sendall(data)
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def _shut(source: socket.socket, sink: socket.socket) -> None:
    for end, how in ((sink, socket.SHUT_WR), (source, socket.SHUT_RD)):
        try:
            end.shutdown(how)
        except OSError:
            pass


def pump(source: socket.socket, sink: socket.socket) -> None:
    """Carry one direction of a connection as it comes, descriptors included.

    For a protocol whose receiver keeps descriptors in a queue of their own
    (X11). D-Bus ties a descriptor to the message it arrives with: pump_bus.
    """
    try:
        while True:
            data, ancillary, _flags, _address = source.recvmsg(65536, socket.CMSG_SPACE(_MAX_DESCRIPTORS * 4))
            if not data:
                break
            _send(sink, data, _descriptors(ancillary))
    except OSError:
        pass
    finally:
        _shut(source, sink)


def message_length(header: bytes) -> int:
    """The whole length of the D-Bus message that starts with these 16 bytes."""
    order = "<" if header[:1] == b"l" else ">" if header[:1] == b"B" else ""
    if not order or len(header) != 16:
        raise ValueError("not a D-Bus message")
    body, = struct.unpack_from(order + "I", header, 4)
    fields, = struct.unpack_from(order + "I", header, 12)
    total = 16 + (fields + 7) // 8 * 8 + body
    if total > _MAX_MESSAGE:
        raise ValueError("D-Bus message too long")
    return total


def _read(source: socket.socket, size: int, descriptors: list[int]) -> bytes:
    """Exactly `size` bytes, or fewer at the end of the stream."""
    data = b""
    while len(data) < size:
        chunk, ancillary, _flags, _address = source.recvmsg(size - len(data), socket.CMSG_SPACE(_MAX_DESCRIPTORS * 4))
        descriptors.extend(_descriptors(ancillary))
        if not chunk:
            break
        data += chunk
    return data


def _line(source: socket.socket) -> bytes:
    data = b""
    while not data.endswith(b"\n") and len(data) < 16384:
        byte = source.recv(1)
        if not byte:
            break
        data += byte
    return data


def pump_bus(source: socket.socket, sink: socket.socket, old: bytes, new: bytes, from_client: bool) -> None:
    """Carry one direction of a D-Bus connection, exchanging `old` for `new`.

    The connection opens with lines of text and goes on, after the client's
    BEGIN, as messages. Each message is read whole and never together with
    the next one: a file descriptor travels beside the bytes of the message
    it belongs to, and the filtered bus downstream holds a sender to that.
    Names are length-prefixed strings and the two are equally long, so a
    message keeps its size.
    """
    if len(old) != len(new) or not old:
        raise ValueError("names must have the same length")
    try:
        while True:
            if not from_client and source.recv(1, socket.MSG_PEEK) in (b"l", b"B", b""):
                break
            line = _line(source)
            if not line:
                return
            sink.sendall(line)
            if from_client and line.strip() == b"BEGIN":
                break
        while True:
            descriptors: list[int] = []
            header = _read(source, 16, descriptors)
            if len(header) < 16:
                for descriptor in descriptors:
                    os.close(descriptor)
                break
            rest = message_length(header) - 16
            body = _read(source, rest, descriptors)
            if len(body) < rest:
                for descriptor in descriptors:
                    os.close(descriptor)
                break
            _send(sink, (header + body).replace(old, new), descriptors)
    except (OSError, ValueError):
        pass
    finally:
        _shut(source, sink)


class Relay:
    """The application's bus socket: the filtered bus with one name exchanged."""

    def __init__(self, listen: Path, upstream: Path, inner: str, outer: str):
        self.path, self.upstream = listen, upstream
        self.inner, self.outer = inner.encode(), outer.encode()
        self._server: socket.socket | None = None

    def start(self) -> None:
        self.path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.path))
        os.chmod(self.path, 0o600)
        server.listen(32)
        self._server = server
        threading.Thread(target=self._accept, args=(server,), daemon=True, name="luma-capsule-bus").start()

    def _accept(self, server: socket.socket) -> None:
        while True:
            try:
                client, _address = server.accept()
            except OSError:
                return
            try:
                bus = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                bus.connect(str(self.upstream))
            except OSError:
                client.close()
                continue
            self._serve(client, bus)

    def _serve(self, client: socket.socket, bus: socket.socket) -> None:
        def run(source: socket.socket, sink: socket.socket, old: bytes, new: bytes, from_client: bool,
                last: threading.Event, other: threading.Event) -> None:
            pump_bus(source, sink, old, new, from_client)
            last.set()
            if other.is_set():
                client.close()
                bus.close()
        up, down = threading.Event(), threading.Event()
        threading.Thread(target=run, args=(client, bus, self.inner, self.outer, True, up, down),
                         daemon=True, name="luma-capsule-bus-up").start()
        threading.Thread(target=run, args=(bus, client, self.outer, self.inner, False, down, up),
                         daemon=True, name="luma-capsule-bus-down").start()

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        self.path.unlink(missing_ok=True)


# -- The policy ----------------------------------------------------------------


class Refused(Exception):
    """A call the application may not make; answered with this D-Bus error."""

    def __init__(self, name: str, text: str):
        super().__init__(text)
        self.name, self.text = name, text


def _kind(path: str) -> str:
    if path == ROOT:
        return "service"
    parts = path[len(ROOT) + 1:].split("/") if path.startswith(ROOT + "/") else []
    if len(parts) == 2 and parts[0] in {"session", "prompt"}:
        return parts[0]
    if len(parts) == 2 and parts[0] in {"collection", "aliases"}:
        return "collection"
    if len(parts) == 3 and parts[0] in {"collection", "aliases"}:
        return "item"
    return ""


class Policy:
    """What one application may ask of the Secret Service, and how it is asked.

    `call(path, interface, member, body)` performs the call on the real
    service and returns its reply (a GLib.Variant tuple) or raises. Kept apart
    from the bus so the rules can be exercised without one.
    """

    def __init__(self, tag: str, call: Callable[[str, str, str, object], object], adopt: bool = False):
        from gi.repository import GLib
        self.GLib = GLib
        self.tag, self.call, self.adopt = tag, call, adopt
        self.own: set[str] = set()
        self.prompts: set[str] = set()

    # Helpers over the real service.

    def _variant(self, signature: str, value: object):
        return self.GLib.Variant(signature, value)

    def _get(self, path: str, interface: str, name: str):
        return self.call(path, PROPERTIES, "Get", self._variant("(ss)", (interface, name))).get_child_value(0).get_variant()

    def default_collection(self) -> str:
        path = self.call(ROOT, SERVICE_INTERFACE, "ReadAlias", self._variant("(s)", ("default",))).unpack()[0]
        return path if path != "/" else ""

    def _attributes(self, path: str) -> dict[str, str]:
        return dict(self._get(path, ITEM_INTERFACE, "Attributes").unpack())

    def is_own(self, path: str) -> bool:
        if path in self.own:
            return True
        default = self.default_collection()
        if not default or _kind(path) != "item" or path.rsplit("/", 1)[0] not in {default, DEFAULT_ALIAS}:
            return False
        try:
            mine = self._attributes(path).get(ATTRIBUTE) == self.tag
        except Exception:
            return False
        if mine:
            self.own.add(path)
        return mine

    def _require_item(self, path: str) -> None:
        if not self.is_own(path):
            raise Refused(NO_SUCH_OBJECT, "No such secret item.")

    def _require_collection(self, path: str) -> None:
        if _kind(path) != "collection" or path not in {DEFAULT_ALIAS, self.default_collection()}:
            raise Refused(NO_SUCH_OBJECT, "No such secret collection.")

    def _tagged(self, attributes: dict[str, str]) -> dict[str, str]:
        return {**{key: value for key, value in attributes.items() if key != ATTRIBUTE}, ATTRIBUTE: self.tag}

    def _remember(self, paths) -> list[str]:
        self.own.update(paths)
        return list(paths)

    def _adopt(self, attributes: dict[str, str]) -> tuple[list[str], list[str]]:
        """Mark as this application's the older entries this search names in full."""
        if not self.adopt or not attributes:
            return [], []
        unlocked, locked = self.call(ROOT, SERVICE_INTERFACE, "SearchItems",
                                     self._variant("(a{ss})", (attributes,))).unpack()
        default = self.default_collection()
        adopted: tuple[list[str], list[str]] = ([], [])
        for group, paths in enumerate((unlocked, locked)):
            for path in paths:
                if not default or path.rsplit("/", 1)[0] != default:
                    continue
                try:
                    found = self._attributes(path)
                    if ATTRIBUTE in found or any(attributes.get(key) != value for key, value in found.items()
                                                 if key != SCHEMA_ATTRIBUTE):
                        continue
                    self.call(path, PROPERTIES, "Set", self._variant(
                        "(ssv)", (ITEM_INTERFACE, "Attributes", self._variant("a{ss}", self._tagged(found)))))
                except Exception:
                    continue
                adopted[group].append(path)
        return adopted

    def _search(self, attributes: dict[str, str]) -> tuple[list[str], list[str]]:
        unlocked, locked = self.call(ROOT, SERVICE_INTERFACE, "SearchItems",
                                     self._variant("(a{ss})", (self._tagged(attributes),))).unpack()
        default = self.default_collection()
        unlocked = [path for path in unlocked if path.rsplit("/", 1)[0] == default]
        locked = [path for path in locked if path.rsplit("/", 1)[0] == default]
        if not unlocked and not locked:
            unlocked, locked = self._adopt({key: value for key, value in attributes.items() if key != ATTRIBUTE})
        return self._remember(unlocked), self._remember(locked)

    def _properties(self, body) -> dict[str, object]:
        """An a{sv} as a dict of variants, untouched inside."""
        found: dict[str, object] = {}
        for index in range(body.n_children()):
            entry = body.get_child_value(index)
            found[entry.get_child_value(0).get_string()] = entry.get_child_value(1).get_variant()
        return found

    def _shown(self, interface: str, name: str, value):
        """A property as the application may see it."""
        if interface == ITEM_INTERFACE and name == "Attributes":
            return self._variant("a{ss}", {k: v for k, v in value.unpack().items() if k != ATTRIBUTE})
        if interface == COLLECTION_INTERFACE and name == "Items":
            unlocked, locked = self._search({})
            return self._variant("ao", [*unlocked, *locked])
        if interface == SERVICE_INTERFACE and name == "Collections":
            default = self.default_collection()
            return self._variant("ao", [default] if default else [])
        return value

    # The calls.

    def handle(self, path: str, interface: str, member: str, body):
        """Answer one call from the application: a reply body, or Refused."""
        kind = _kind(path)
        if not kind:
            raise Refused(NO_SUCH_OBJECT, "No such object.")
        if interface == "org.freedesktop.DBus.Peer" and member == "Ping":
            return None
        if interface == "org.freedesktop.DBus.Introspectable":
            raise Refused(NOT_SUPPORTED, "Introspection is not available here.")
        if interface == PROPERTIES:
            return self._handle_properties(kind, path, member, body)
        handler = getattr(self, f"_{kind}_{member}", None)
        expected = {"service": SERVICE_INTERFACE, "collection": COLLECTION_INTERFACE, "item": ITEM_INTERFACE,
                    "session": SESSION_INTERFACE, "prompt": PROMPT_INTERFACE}[kind]
        if handler is None or interface not in {expected, "", None}:
            raise Refused(UNKNOWN_METHOD, f"{member} is not available here.")
        return handler(path, body)

    def _handle_properties(self, kind: str, path: str, member: str, body):
        if kind == "item":
            self._require_item(path)
        elif kind == "collection":
            self._require_collection(path)
        elif kind == "prompt":
            raise Refused(UNKNOWN_METHOD, "A prompt has no properties.")
        if member == "Get":
            interface, name = body.unpack()
            if (interface, name) in {(COLLECTION_INTERFACE, "Items"), (SERVICE_INTERFACE, "Collections")}:
                return self._variant("(v)", (self._shown(interface, name, None),))
            value = self.call(path, PROPERTIES, "Get", body).get_child_value(0).get_variant()
            return self._variant("(v)", (self._shown(interface, name, value),))
        if member == "GetAll":
            interface = body.unpack()[0]
            values = self._properties(self.call(path, PROPERTIES, "GetAll", body).get_child_value(0))
            return self._variant("(a{sv})", ({name: self._shown(interface, name, value)
                                              for name, value in values.items()},))
        if member == "Set":
            interface, name = body.get_child_value(0).get_string(), body.get_child_value(1).get_string()
            value = body.get_child_value(2).get_variant()
            if kind != "item" or interface != ITEM_INTERFACE or name not in {"Attributes", "Label"}:
                raise Refused(ACCESS_DENIED, "This property cannot be changed by an application.")
            if name == "Attributes":
                value = self._variant("a{ss}", self._tagged(dict(value.unpack())))
            return self.call(path, PROPERTIES, "Set", self._variant("(ssv)", (interface, name, value)))
        raise Refused(UNKNOWN_METHOD, f"{member} is not available here.")

    def _service_OpenSession(self, path: str, body):
        return self.call(path, SERVICE_INTERFACE, "OpenSession", body)

    def _service_SearchItems(self, path: str, body):
        return self._variant("(aoao)", self._search(dict(body.unpack()[0])))

    def _service_ReadAlias(self, path: str, body):
        name = body.unpack()[0]
        return self._variant("(o)", ((self.default_collection() or "/") if name == "default" else "/",))

    def _service_SetAlias(self, path: str, body):
        raise Refused(ACCESS_DENIED, "An application cannot move a keyring alias.")

    def _service_CreateCollection(self, path: str, body):
        default = self.default_collection()
        if default:
            # The one collection an application has already exists.
            return self._variant("(oo)", (default, "/"))
        if body.get_child_value(1).get_string() != "default":
            raise Refused(NOT_SUPPORTED, "An application keeps its entries in the default keyring.")
        reply = self.call(path, SERVICE_INTERFACE, "CreateCollection", body)
        self._note_prompt(reply.get_child_value(1).get_string())
        return reply

    def _service_Unlock(self, path: str, body):
        default = self.default_collection()
        objects = [item for item in body.unpack()[0]
                   if item in {DEFAULT_ALIAS, default} or (_kind(item) == "item" and self.is_own(item))]
        reply = self.call(path, SERVICE_INTERFACE, "Unlock", self._variant("(ao)", (objects,)))
        self._note_prompt(reply.get_child_value(1).get_string())
        return reply

    def _service_Lock(self, path: str, body):
        # The keyring is the person's and every application's; one application
        # does not get to lock it for the rest.
        return self._variant("(aoo)", ([], "/"))

    def _service_GetSecrets(self, path: str, body):
        items, session = body.unpack()
        return self.call(path, SERVICE_INTERFACE, "GetSecrets",
                         self._variant("(aoo)", ([item for item in items if self.is_own(item)], session)))

    def _collection_SearchItems(self, path: str, body):
        self._require_collection(path)
        unlocked, locked = self._search(dict(body.unpack()[0]))
        return self._variant("(ao)", ([*unlocked, *locked],))

    def _collection_CreateItem(self, path: str, body):
        self._require_collection(path)
        properties = self._properties(body.get_child_value(0))
        current = properties.get(ITEM_ATTRIBUTES)
        properties[ITEM_ATTRIBUTES] = self._variant("a{ss}", self._tagged(dict(current.unpack()) if current else {}))
        reply = self.call(path, COLLECTION_INTERFACE, "CreateItem", self.GLib.Variant.new_tuple(
            self._variant("a{sv}", properties), body.get_child_value(1), body.get_child_value(2)))
        item, prompt = reply.unpack()
        if item != "/":
            self.own.add(item)
        self._note_prompt(prompt)
        return reply

    def _collection_Delete(self, path: str, body):
        raise Refused(ACCESS_DENIED, "An application cannot delete the keyring.")

    def _item_Delete(self, path: str, body):
        self._require_item(path)
        reply = self.call(path, ITEM_INTERFACE, "Delete", body)
        self._note_prompt(reply.get_child_value(0).get_string())
        return reply

    def _item_GetSecret(self, path: str, body):
        self._require_item(path)
        return self.call(path, ITEM_INTERFACE, "GetSecret", body)

    def _item_SetSecret(self, path: str, body):
        self._require_item(path)
        return self.call(path, ITEM_INTERFACE, "SetSecret", body)

    def _session_Close(self, path: str, body):
        return self.call(path, SESSION_INTERFACE, "Close", body)

    def _prompt_Prompt(self, path: str, body):
        if path not in self.prompts:
            raise Refused(NO_SUCH_OBJECT, "No such prompt.")
        return self.call(path, PROMPT_INTERFACE, "Prompt", body)

    def _prompt_Dismiss(self, path: str, body):
        if path not in self.prompts:
            raise Refused(NO_SUCH_OBJECT, "No such prompt.")
        return self.call(path, PROMPT_INTERFACE, "Dismiss", body)

    def _note_prompt(self, path: str) -> None:
        if path and path != "/":
            self.prompts.add(path)

    def signal_allowed(self, path: str, interface: str, member: str, body) -> bool:
        """Whether a signal from the real service is the application's to see."""
        if interface == PROMPT_INTERFACE:
            return path in self.prompts
        if interface == COLLECTION_INTERFACE and member in {"ItemCreated", "ItemChanged", "ItemDeleted"}:
            item = body.unpack()[0]
            if member == "ItemDeleted":
                known = item in self.own
                self.own.discard(item)
                return known
            return self.is_own(item)
        if interface == SERVICE_INTERFACE and member in {"CollectionCreated", "CollectionChanged"}:
            return body.unpack()[0] == self.default_collection()
        return False


# -- The broker ----------------------------------------------------------------


def _remote_error(error: Exception) -> tuple[str, str]:
    text = getattr(error, "message", None) or str(error)
    if text.startswith("GDBus.Error:"):
        name, _, rest = text[len("GDBus.Error:"):].partition(": ")
        if name:
            return name, rest or name
    return "org.freedesktop.DBus.Error.Failed", text


class Broker:
    """Owns the application's Secret Service name on the session bus."""

    def __init__(self, name: str, tag: str, adopt: bool = False, address: str | None = None):
        self.name, self.tag, self.adopt = name, tag, adopt
        self.address = address or os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
        self._loop = None
        self._connection = None
        self.policy: Policy | None = None

    def start(self, timeout: float = 5.0) -> bool:
        """Connect and take the name. False when there is no service to stand in front of."""
        if not self.address:
            return False
        ready, result = threading.Event(), [False]
        threading.Thread(target=self._run, args=(ready, result), daemon=True, name="luma-capsule-secrets").start()
        ready.wait(timeout)
        return result[0]

    def _run(self, ready: threading.Event, result: list[bool]) -> None:
        try:
            from gi.repository import Gio, GLib
            context = GLib.MainContext.new()
            context.push_thread_default()
            connection = Gio.DBusConnection.new_for_address_sync(
                self.address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
                Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
            connection.set_exit_on_close(False)
            self._connection = connection

            def call(path: str, interface: str, member: str, body):
                reply = connection.call_sync(SERVICE, path, interface, member, body, None,
                                             Gio.DBusCallFlags.NONE, 30000, None)
                return reply if reply is not None else GLib.Variant("()", ())

            self.policy = policy = Policy(self.tag, call, self.adopt)

            def answer(message) -> bool:
                try:
                    try:
                        body = policy.handle(message.get_path() or "", message.get_interface() or "",
                                             message.get_member() or "", message.get_body())
                        reply = Gio.DBusMessage.new_method_reply(message)
                        if body is not None and body.n_children():
                            reply.set_body(body)
                    except Refused as refused:
                        reply = Gio.DBusMessage.new_method_error_literal(message, refused.name, refused.text)
                    except Exception as error:  # the real service's own refusal, passed on as it is
                        reply = Gio.DBusMessage.new_method_error_literal(message, *_remote_error(error))
                    if not message.get_flags() & Gio.DBusMessageFlags.NO_REPLY_EXPECTED:
                        connection.send_message(reply, Gio.DBusSendMessageFlags.NONE)
                except Exception:
                    pass
                return False

            def incoming(_connection, message, is_incoming, *_data):
                if (is_incoming and message.get_message_type() == Gio.DBusMessageType.METHOD_CALL
                        and (message.get_path() or "").startswith(ROOT)):
                    source = GLib.idle_source_new()
                    source.set_callback(lambda *_a: answer(message))
                    source.attach(context)
                    return None
                return message

            def forward(_connection, _sender, path, interface, member, body, *_data):
                try:
                    if policy.signal_allowed(path, interface, member, body):
                        connection.emit_signal(None, path, interface, member, body)
                except Exception:
                    pass

            connection.add_filter(incoming)
            for interface in (COLLECTION_INTERFACE, PROMPT_INTERFACE, SERVICE_INTERFACE):
                connection.signal_subscribe(SERVICE, interface, None, None, None,
                                            Gio.DBusSignalFlags.NONE, forward)
            taken = connection.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                         "RequestName", GLib.Variant("(su)", (self.name, 0)), None,
                                         Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
            # 1: owner now. 2: in the queue behind another window of the same
            # application, which answers until it closes.
            result[0] = taken in (1, 2, 4)
            self._loop = GLib.MainLoop.new(context, False)
        except Exception:
            ready.set()
            return
        ready.set()
        self._loop.run()

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.quit()
        if self._connection is not None:
            try:
                self._connection.close_sync(None)
            except Exception:
                pass


def forget(tag: str, address: str | None = None) -> int:
    """Delete every entry of a removed application. Returns how many."""
    from gi.repository import Gio, GLib
    address = address or os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if not address or not tag:
        return 0
    connection = Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
    connection.set_exit_on_close(False)
    removed = 0
    try:
        unlocked, locked = connection.call_sync(
            SERVICE, ROOT, SERVICE_INTERFACE, "SearchItems", GLib.Variant("(a{ss})", ({ATTRIBUTE: tag},)),
            None, Gio.DBusCallFlags.NONE, 30000, None).unpack()
        for path in (*unlocked, *locked):
            try:
                connection.call_sync(SERVICE, path, ITEM_INTERFACE, "Delete", None, None,
                                     Gio.DBusCallFlags.NONE, 30000, None)
                removed += 1
            except GLib.Error:
                continue
    except GLib.Error:
        pass
    finally:
        connection.close_sync(None)
    return removed
