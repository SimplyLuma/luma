"""A capsule reaches its own keyring entries and no others."""
from __future__ import annotations

import os
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from luma_installer import capsule_secrets as secrets

try:
    from gi.repository import GLib
except ImportError:  # pragma: no cover - the package requires python3-gobject
    GLib = None

LOGIN = secrets.ROOT + "/collection/login"
OTHER = secrets.ROOT + "/collection/work"


class Names(unittest.TestCase):
    def test_the_broker_name_is_as_long_as_the_service_name(self):
        for pair in ((0, 1), (7, 8), (1022, 1023), (35, 36), (1295 % 1024, 1023)):
            name = secrets.bus_name(pair)
            self.assertEqual(len(name), len(secrets.SERVICE))
            self.assertRegex(name, r"^io\.luma\.Secrets\.s[0-9a-z]{6}$")
        self.assertNotEqual(secrets.bus_name((1, 36)), secrets.bus_name((36, 1)))
        self.assertEqual(len({secrets.bus_name((low, high)) for low in range(0, 1024, 37)
                              for high in range(low + 1, 1024, 41)}),
                         sum(1 for low in range(0, 1024, 37) for _ in range(low + 1, 1024, 41)))


def message(body: bytes, fields: bytes = b"", order: str = "<") -> bytes:
    """A D-Bus message as far as its framing goes."""
    mark = b"l" if order == "<" else b"B"
    padded = fields + b"\0" * (-len(fields) % 8)
    return mark + b"\1\0\1" + struct.pack(order + "III", len(body), 7, len(fields)) + padded + body


class Framing(unittest.TestCase):
    def test_a_message_is_as_long_as_its_header_says_in_either_byte_order(self):
        for order in ("<", ">"):
            whole = message(b"x" * 21, b"f" * 13, order)
            self.assertEqual(secrets.message_length(whole[:16]), len(whole))
            self.assertEqual(len(whole), 16 + 16 + 21)
        with self.assertRaises(ValueError):
            secrets.message_length(b"AUTH EXTERNAL 31")
        with self.assertRaises(ValueError):
            secrets.message_length(b"l\1\0\1" + struct.pack("<III", 1 << 27, 1, 0))


class Relaying(unittest.TestCase):
    AUTH = b"\0AUTH EXTERNAL 31303030\r\nNEGOTIATE_UNIX_FD\r\nBEGIN\r\n"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.upstream_path, self.listen_path = root / "up", root / "app"
        self.upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.upstream.bind(str(self.upstream_path))
        self.upstream.listen(4)
        self.addCleanup(self.upstream.close)
        self.relay = secrets.Relay(self.listen_path, self.upstream_path, "org.freedesktop.secrets",
                                   "io.luma.Secrets.s00700a")
        self.relay.start()
        self.addCleanup(self.relay.stop)
        self.client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.client.connect(str(self.listen_path))
        self.addCleanup(self.client.close)
        self.bus, _ = self.upstream.accept()
        self.addCleanup(self.bus.close)
        self.client.settimeout(5)
        self.bus.settimeout(5)

    def _read(self, end: socket.socket, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = end.recv(size - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def authenticate(self):
        self.client.sendall(self.AUTH)
        self.assertEqual(self._read(self.bus, len(self.AUTH)), self.AUTH)
        reply = b"OK 1234deadbeef\r\nAGREE_UNIX_FD\r\n"
        self.bus.sendall(reply)
        self.assertEqual(self._read(self.client, len(reply)), reply)

    def test_both_directions_exchange_the_name_in_messages(self):
        self.authenticate()
        call = message(b"s\0org.freedesktop.secrets\0", b"\6\1s\0\x17\0\0\0org.freedesktop.secrets\0")
        self.client.sendall(call + call)
        expected = call.replace(b"org.freedesktop.secrets", b"io.luma.Secrets.s00700a")
        self.assertEqual(self._read(self.bus, 2 * len(call)), expected + expected)
        signal = message(b"io.luma.Secrets.s00700a\0", order=">")
        self.bus.sendall(signal)
        self.assertEqual(self._read(self.client, len(signal)),
                         signal.replace(b"io.luma.Secrets.s00700a", b"org.freedesktop.secrets"))

    def test_a_client_that_sends_everything_at_once_is_understood(self):
        hello = message(b"", b"hello")
        reply = b"OK 1234deadbeef\r\nAGREE_UNIX_FD\r\n" + message(b":1.5\0")
        self.client.sendall(self.AUTH + hello)
        self.assertEqual(self._read(self.bus, len(self.AUTH) + len(hello)), self.AUTH + hello)
        self.bus.sendall(reply)
        self.assertEqual(self._read(self.client, len(reply)), reply)

    def test_a_file_descriptor_arrives_with_the_message_it_was_sent_with(self):
        self.authenticate()
        first, second = message(b"no descriptor here"), message(b"this one has a descriptor")
        reader, writer = os.pipe()
        self.addCleanup(os.close, reader)
        # Both are waiting before the relay reads: it must not take them as one.
        self.client.sendall(first)
        socket.send_fds(self.client, [second], [writer])
        os.close(writer)
        data, descriptors, _flags, _address = socket.recv_fds(self.bus, len(first), 4)
        self.assertEqual((data, descriptors), (first, []))
        data, descriptors, _flags, _address = socket.recv_fds(self.bus, len(second), 4)
        self.assertEqual(data, second)
        self.assertEqual(len(descriptors), 1)
        os.write(descriptors[0], b"through")
        os.close(descriptors[0])
        self.assertEqual(os.read(reader, 16), b"through")

    def test_closing_one_end_closes_the_other(self):
        self.authenticate()
        last = message(b"last words")
        self.client.sendall(last)
        self.client.shutdown(socket.SHUT_WR)
        self.assertEqual(self._read(self.bus, len(last)), last)
        self.assertEqual(self.bus.recv(16), b"")
        self.bus.close()
        self.assertEqual(self.client.recv(16), b"")

    def test_bytes_that_are_not_messages_end_the_connection(self):
        self.authenticate()
        self.client.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertEqual(self.bus.recv(16), b"")


class FakeService:
    """The parts of a Secret Service the broker forwards to, in memory."""

    def __init__(self, default: str = LOGIN):
        self.default = default
        self.items: dict[str, dict[str, str]] = {}
        self.secrets: dict[str, bytes] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.count = 0

    def add(self, attributes: dict[str, str], secret: bytes = b"s", collection: str = LOGIN) -> str:
        self.count += 1
        path = f"{collection}/{self.count}"
        self.items[path] = dict(attributes)
        self.secrets[path] = secret
        return path

    def __call__(self, path, interface, member, body):
        self.calls.append((path, interface, member))
        if path == secrets.DEFAULT_ALIAS:
            path = self.default
        if (interface, member) == (secrets.SERVICE_INTERFACE, "ReadAlias"):
            return GLib.Variant("(o)", (self.default or "/",))
        if (interface, member) == (secrets.SERVICE_INTERFACE, "SearchItems"):
            wanted = body.unpack()[0]
            return GLib.Variant("(aoao)", ([p for p, a in self.items.items()
                                           if all(a.get(k) == v for k, v in wanted.items())], []))
        if (interface, member) == (secrets.SERVICE_INTERFACE, "OpenSession"):
            return GLib.Variant("(vo)", (GLib.Variant("s", ""), secrets.ROOT + "/session/s1"))
        if (interface, member) == (secrets.SERVICE_INTERFACE, "GetSecrets"):
            items, session = body.unpack()
            return GLib.Variant("(a{o(oayays)})", ({p: (session, b"", self.secrets[p], "text/plain") for p in items},))
        if (interface, member) == (secrets.SERVICE_INTERFACE, "Unlock"):
            return GLib.Variant("(aoo)", (body.unpack()[0], secrets.ROOT + "/prompt/p1"))
        if (interface, member) == (secrets.COLLECTION_INTERFACE, "CreateItem"):
            properties, secret, _replace = body.unpack()
            created = self.add(properties[secrets.ITEM_ATTRIBUTES], bytes(secret[2]), path)
            return GLib.Variant("(oo)", (created, "/"))
        if (interface, member) == (secrets.ITEM_INTERFACE, "GetSecret"):
            return GLib.Variant("((oayays))", ((body.unpack()[0], b"", self.secrets[path], "text/plain"),))
        if (interface, member) == (secrets.ITEM_INTERFACE, "Delete"):
            del self.items[path]
            return GLib.Variant("(o)", ("/",))
        if (interface, member) == (secrets.PROPERTIES, "Get"):
            _interface, name = body.unpack()
            if name == "Attributes":
                return GLib.Variant("(v)", (GLib.Variant("a{ss}", self.items[path]),))
            if name == "Label":
                return GLib.Variant("(v)", (GLib.Variant("s", "Login"),))
        if (interface, member) == (secrets.PROPERTIES, "GetAll"):
            return GLib.Variant("(a{sv})", ({"Attributes": GLib.Variant("a{ss}", self.items[path]),
                                              "Label": GLib.Variant("s", "entry")},))
        if (interface, member) == (secrets.PROPERTIES, "Set"):
            _interface, name, value = body.unpack()
            if name == "Attributes":
                self.items[path] = dict(value)
            return GLib.Variant("()", ())
        if (interface, member) == (secrets.PROMPT_INTERFACE, "Prompt"):
            return GLib.Variant("()", ())
        raise AssertionError(f"unexpected call {path} {interface}.{member}")


@unittest.skipIf(GLib is None, "needs PyGObject")
class Rules(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.policy = secrets.Policy("app-one", self.service)
        self.mine = self.service.add({"application": "one", secrets.ATTRIBUTE: "app-one"}, b"mine")
        self.theirs = self.service.add({"application": "two", secrets.ATTRIBUTE: "app-two"}, b"theirs")
        self.session = self.service.add({"application": "wifi"}, b"session")

    def search(self, attributes: dict[str, str]) -> list[str]:
        unlocked, locked = self.policy.handle(secrets.ROOT, secrets.SERVICE_INTERFACE, "SearchItems",
                                              GLib.Variant("(a{ss})", (attributes,))).unpack()
        return [*unlocked, *locked]

    def refused(self, path: str, interface: str, member: str, body=None) -> str:
        with self.assertRaises(secrets.Refused) as raised:
            self.policy.handle(path, interface, member, body)
        return raised.exception.name

    def test_a_search_finds_only_the_applications_own_entries(self):
        self.assertEqual(self.search({}), [self.mine])
        self.assertEqual(self.search({"application": "two"}), [])
        self.assertEqual(self.search({"application": "wifi"}), [])
        self.assertEqual(self.search({secrets.ATTRIBUTE: "app-two"}), [self.mine])

    def test_another_applications_entry_cannot_be_read_changed_or_deleted(self):
        session = GLib.Variant("(o)", (secrets.ROOT + "/session/s1",))
        for path in (self.theirs, self.session, OTHER + "/1"):
            self.assertEqual(self.refused(path, secrets.ITEM_INTERFACE, "GetSecret", session), secrets.NO_SUCH_OBJECT)
            self.assertEqual(self.refused(path, secrets.ITEM_INTERFACE, "Delete"), secrets.NO_SUCH_OBJECT)
            self.assertEqual(self.refused(path, secrets.PROPERTIES, "Get",
                                          GLib.Variant("(ss)", (secrets.ITEM_INTERFACE, "Attributes"))),
                             secrets.NO_SUCH_OBJECT)
        self.assertIn(self.theirs, self.service.items)
        got = self.policy.handle(self.mine, secrets.ITEM_INTERFACE, "GetSecret", session).unpack()[0]
        self.assertEqual(bytes(got[2]), b"mine")

    def test_bulk_reads_drop_what_is_not_the_applications(self):
        reply = self.policy.handle(secrets.ROOT, secrets.SERVICE_INTERFACE, "GetSecrets", GLib.Variant(
            "(aoo)", ([self.mine, self.theirs, self.session], secrets.ROOT + "/session/s1"))).unpack()[0]
        self.assertEqual(list(reply), [self.mine])

    def test_a_stored_entry_is_marked_and_the_mark_is_never_shown(self):
        body = GLib.Variant("(a{sv}(oayays)b)", (
            {secrets.ITEM_ATTRIBUTES: GLib.Variant("a{ss}", {"account": "a", secrets.ATTRIBUTE: "app-two"}),
             "org.freedesktop.Secret.Item.Label": GLib.Variant("s", "A")},
            (secrets.ROOT + "/session/s1", b"", b"new", "text/plain"), True))
        created = self.policy.handle(secrets.DEFAULT_ALIAS, secrets.COLLECTION_INTERFACE, "CreateItem", body).unpack()[0]
        self.assertEqual(self.service.items[created], {"account": "a", secrets.ATTRIBUTE: "app-one"})
        shown = self.policy.handle(created, secrets.PROPERTIES, "Get",
                                   GLib.Variant("(ss)", (secrets.ITEM_INTERFACE, "Attributes"))).unpack()[0]
        self.assertEqual(shown, {"account": "a"})
        everything = self.policy.handle(created, secrets.PROPERTIES, "GetAll",
                                        GLib.Variant("(s)", (secrets.ITEM_INTERFACE,))).unpack()[0]
        self.assertEqual(everything["Attributes"], {"account": "a"})
        self.policy.handle(created, secrets.PROPERTIES, "Set", GLib.Variant(
            "(ssv)", (secrets.ITEM_INTERFACE, "Attributes", GLib.Variant("a{ss}", {"account": "b"}))))
        self.assertEqual(self.service.items[created], {"account": "b", secrets.ATTRIBUTE: "app-one"})

    def test_the_application_sees_one_collection_holding_its_entries(self):
        get = lambda path, interface, name: self.policy.handle(
            path, secrets.PROPERTIES, "Get", GLib.Variant("(ss)", (interface, name))).unpack()[0]
        self.assertEqual(get(secrets.ROOT, secrets.SERVICE_INTERFACE, "Collections"), [LOGIN])
        self.assertEqual(get(LOGIN, secrets.COLLECTION_INTERFACE, "Items"), [self.mine])
        self.assertEqual(get(secrets.DEFAULT_ALIAS, secrets.COLLECTION_INTERFACE, "Label"), "Login")
        self.assertEqual(self.refused(OTHER, secrets.PROPERTIES, "Get",
                                      GLib.Variant("(ss)", (secrets.COLLECTION_INTERFACE, "Items"))),
                         secrets.NO_SUCH_OBJECT)
        read = lambda name: self.policy.handle(secrets.ROOT, secrets.SERVICE_INTERFACE, "ReadAlias",
                                               GLib.Variant("(s)", (name,))).unpack()[0]
        self.assertEqual((read("default"), read("session")), (LOGIN, "/"))

    def test_the_keyring_itself_cannot_be_locked_deleted_or_moved(self):
        locked = self.policy.handle(secrets.ROOT, secrets.SERVICE_INTERFACE, "Lock",
                                    GLib.Variant("(ao)", ([LOGIN],))).unpack()
        self.assertEqual(locked, ([], "/"))
        self.assertNotIn((secrets.ROOT, secrets.SERVICE_INTERFACE, "Lock"), self.service.calls)
        self.assertEqual(self.refused(LOGIN, secrets.COLLECTION_INTERFACE, "Delete"), secrets.ACCESS_DENIED)
        self.assertEqual(self.refused(secrets.ROOT, secrets.SERVICE_INTERFACE, "SetAlias",
                                      GLib.Variant("(so)", ("default", OTHER))), secrets.ACCESS_DENIED)
        self.assertEqual(self.refused(LOGIN, secrets.PROPERTIES, "Set", GLib.Variant(
            "(ssv)", (secrets.COLLECTION_INTERFACE, "Label", GLib.Variant("s", "x")))), secrets.ACCESS_DENIED)
        created = self.policy.handle(secrets.ROOT, secrets.SERVICE_INTERFACE, "CreateCollection", GLib.Variant(
            "(a{sv}s)", ({"org.freedesktop.Secret.Collection.Label": GLib.Variant("s", "Mine")}, ""))).unpack()
        self.assertEqual(created, (LOGIN, "/"))

    def test_unlocking_reaches_only_the_default_keyring_and_own_entries(self):
        unlocked, prompt = self.policy.handle(secrets.ROOT, secrets.SERVICE_INTERFACE, "Unlock", GLib.Variant(
            "(ao)", ([LOGIN, OTHER, self.mine, self.theirs],))).unpack()
        self.assertEqual(unlocked, [LOGIN, self.mine])
        self.policy.handle(prompt, secrets.PROMPT_INTERFACE, "Prompt", GLib.Variant("(s)", ("",)))
        self.assertEqual(self.refused(secrets.ROOT + "/prompt/p9", secrets.PROMPT_INTERFACE, "Prompt",
                                      GLib.Variant("(s)", ("",))), secrets.NO_SUCH_OBJECT)

    def test_paths_outside_the_service_are_refused(self):
        for path in ("/", "/org/freedesktop", secrets.ROOT + "/collection", secrets.ROOT + "/collection/a/b/c",
                     secrets.ROOT + "x"):
            self.assertEqual(self.refused(path, secrets.SERVICE_INTERFACE, "SearchItems"), secrets.NO_SUCH_OBJECT)
        self.assertEqual(self.refused(secrets.ROOT, secrets.ITEM_INTERFACE, "Delete"), secrets.UNKNOWN_METHOD)

    def test_signals_are_passed_on_only_for_own_entries(self):
        item = lambda path: GLib.Variant("(o)", (path,))
        allowed = self.policy.signal_allowed
        self.assertTrue(allowed(LOGIN, secrets.COLLECTION_INTERFACE, "ItemChanged", item(self.mine)))
        self.assertFalse(allowed(LOGIN, secrets.COLLECTION_INTERFACE, "ItemCreated", item(self.theirs)))
        self.assertTrue(allowed(LOGIN, secrets.COLLECTION_INTERFACE, "ItemDeleted", item(self.mine)))
        self.assertFalse(allowed(LOGIN, secrets.COLLECTION_INTERFACE, "ItemDeleted", item(self.theirs)))
        self.assertFalse(allowed(secrets.ROOT + "/prompt/p1", secrets.PROMPT_INTERFACE, "Completed", None))

    def test_without_a_default_keyring_nothing_is_found_and_one_may_be_created(self):
        self.service.default = ""
        self.assertEqual(self.search({}), [])
        self.assertEqual(self.refused(secrets.ROOT, secrets.SERVICE_INTERFACE, "CreateCollection", GLib.Variant(
            "(a{sv}s)", ({}, "work"))), secrets.NOT_SUPPORTED)


@unittest.skipIf(GLib is None, "needs PyGObject")
class Adoption(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.old = self.service.add({"application": "chrome", "xdg:schema": "chrome_libsecret_os_crypt_password_v2"})
        self.wifi = self.service.add({"application": "chrome", "connection": "home"})
        self.taken = self.service.add({"application": "chrome", secrets.ATTRIBUTE: "app-two"})
        self.elsewhere = self.service.add({"application": "chrome"}, collection=OTHER)

    def search(self, policy, attributes):
        unlocked, locked = policy.handle(secrets.ROOT, secrets.SERVICE_INTERFACE, "SearchItems",
                                         GLib.Variant("(a{ss})", (attributes,))).unpack()
        return [*unlocked, *locked]

    def test_an_older_entry_named_in_full_becomes_the_applications(self):
        policy = secrets.Policy("app-one", self.service, adopt=True)
        self.assertEqual(self.search(policy, {"application": "chrome"}), [self.old])
        self.assertEqual(self.service.items[self.old][secrets.ATTRIBUTE], "app-one")
        self.assertEqual(self.service.items[self.old]["application"], "chrome")
        self.assertNotIn(secrets.ATTRIBUTE, self.service.items[self.wifi])
        self.assertEqual(self.service.items[self.taken][secrets.ATTRIBUTE], "app-two")
        self.assertNotIn(secrets.ATTRIBUTE, self.service.items[self.elsewhere])
        self.assertEqual(self.search(secrets.Policy("app-one", self.service), {"application": "chrome"}), [self.old])

    def test_a_search_for_everything_adopts_nothing(self):
        policy = secrets.Policy("app-one", self.service, adopt=True)
        self.assertEqual(self.search(policy, {}), [])
        self.assertNotIn(secrets.ATTRIBUTE, self.service.items[self.old])

    def test_nothing_is_adopted_once_the_record_no_longer_allows_it(self):
        self.assertEqual(self.search(secrets.Policy("app-one", self.service), {"application": "chrome"}), [])
        self.assertNotIn(secrets.ATTRIBUTE, self.service.items[self.old])


if __name__ == "__main__":
    unittest.main()
