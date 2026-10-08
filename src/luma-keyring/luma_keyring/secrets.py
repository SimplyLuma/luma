# SPDX-License-Identifier: MPL-2.0
"""The keyring itself, over D-Bus.

The Secret Service can unlock a keyring and read what is in it, but it has
no way to change the password of one. gnome-keyring's own interface has,
under a name that says how it feels about being used
(``InternalUnsupportedGuiltRiddenInterface``); Seahorse has changed keyring
passwords through it for years, and it is the only way to re-key a keyring
in place. Everything Luma does with it lives here, in three calls, so a
keyring daemon without it means "this repair is not available here" rather
than a failure halfway through.

No password reaches a log, a state file or a exception message: they are
passed straight into a D-Bus call and forgotten.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from gi.repository import Gio, GLib

__all__ = ("Keyring", "KeyringError", "LOGIN_PATH")

log = logging.getLogger("luma-keyring")

BUS_NAME = "org.freedesktop.secrets"
SERVICE_PATH = "/org/freedesktop/secrets"
LOGIN_PATH = "/org/freedesktop/secrets/collection/login"
SERVICE_IFACE = "org.freedesktop.Secret.Service"
COLLECTION_IFACE = "org.freedesktop.Secret.Collection"
ITEM_IFACE = "org.freedesktop.Secret.Item"
INTERNAL_IFACE = "org.gnome.keyring.InternalUnsupportedGuiltRiddenInterface"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

CALL_TIMEOUT_MS = 20_000


class KeyringError(Exception):
    """Something the person needs told in plain words."""


@dataclass(frozen=True)
class Collection:
    path: str
    locked: bool
    label: str


class Keyring:
    """The session's keyring daemon, or as much of it as is there."""

    def __init__(self, bus: Gio.DBusConnection | None = None) -> None:
        self._bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._session: str | None = None

    # -- plumbing ---------------------------------------------------------
    def _call(self, path: str, iface: str, method: str,
              params: GLib.Variant | None, reply: str) -> GLib.Variant:
        try:
            return self._bus.call_sync(
                BUS_NAME, path, iface, method, params,
                GLib.VariantType.new(reply) if reply else None,
                Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None)
        except GLib.Error as error:
            raise KeyringError(error.message) from None

    def running(self) -> bool:
        try:
            self._bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "NameHasOwner",
                GLib.Variant("(s)", (BUS_NAME,)), GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            return False
        answer = self._bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner",
            GLib.Variant("(s)", (BUS_NAME,)), GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE, 5000, None)
        return bool(answer.unpack()[0])

    def supports_repair(self) -> bool:
        """Whether this keyring daemon can change a keyring's password."""
        try:
            xml = self._call(SERVICE_PATH, "org.freedesktop.DBus.Introspectable",
                             "Introspect", None, "(s)").unpack()[0]
        except KeyringError:
            return False
        return INTERNAL_IFACE in xml

    def _open_session(self) -> str:
        if self._session:
            return self._session
        result = self._call(
            SERVICE_PATH, SERVICE_IFACE, "OpenSession",
            GLib.Variant("(sv)", ("plain", GLib.Variant("s", ""))), "(vo)")
        self._session = result.unpack()[1]
        return self._session

    def _secret(self, password: str) -> GLib.Variant:
        """A Secret Service secret: session, parameters, value, content type."""
        return GLib.Variant(
            "(oayays)",
            (self._open_session(), b"", password.encode("utf-8"), "text/plain"))

    # -- what is there ----------------------------------------------------
    def collection(self, path: str = LOGIN_PATH) -> Collection | None:
        try:
            locked = self._call(path, PROPERTIES_IFACE, "Get",
                                GLib.Variant("(ss)", (COLLECTION_IFACE, "Locked")),
                                "(v)").unpack()[0]
            label = self._call(path, PROPERTIES_IFACE, "Get",
                               GLib.Variant("(ss)", (COLLECTION_IFACE, "Label")),
                               "(v)").unpack()[0]
        except KeyringError:
            return None
        return Collection(path=path, locked=bool(locked), label=str(label))

    def items(self, path: str = LOGIN_PATH) -> list[str]:
        """What is stored, as far as the keyring will say.

        A locked keyring keeps its labels to itself; the caller says so
        plainly rather than pretending the keyring is empty.
        """
        try:
            paths = self._call(path, PROPERTIES_IFACE, "Get",
                               GLib.Variant("(ss)", (COLLECTION_IFACE, "Items")),
                               "(v)").unpack()[0]
        except KeyringError:
            return []
        labels = []
        for item in paths:
            try:
                labels.append(str(self._call(
                    item, PROPERTIES_IFACE, "Get",
                    GLib.Variant("(ss)", (ITEM_IFACE, "Label")), "(v)").unpack()[0]))
            except KeyringError:
                continue
        return labels

    # -- the three calls that matter --------------------------------------
    def unlock_with(self, password: str, path: str = LOGIN_PATH) -> None:
        """Open the keyring with a password, without asking the person again."""
        self._call(SERVICE_PATH, INTERNAL_IFACE, "UnlockWithMasterPassword",
                   GLib.Variant("(o(oayays))", (path, self._secret(password))), "")

    def lock(self, path: str = LOGIN_PATH) -> None:
        """Shut the keyring again (what a login that cannot unlock leaves)."""
        self._call(SERVICE_PATH, SERVICE_IFACE, "Lock",
                   GLib.Variant("(ao)", ([path],)), "(aoo)")

    def change_password(self, old: str, new: str, path: str = LOGIN_PATH) -> None:
        """Re-key the keyring, keeping everything in it."""
        self._call(SERVICE_PATH, INTERNAL_IFACE, "ChangeWithMasterPassword",
                   GLib.Variant("(o(oayays)(oayays))",
                                (path, self._secret(old), self._secret(new))), "")

    def create_login(self, password: str, label: str = "login") -> str:
        """A new login keyring, locked with the account password.

        The label is what names the file and the path, so "login" is what
        makes this the login keyring -- the one pam_gnome_keyring opens at
        the next login.
        """
        attributes = {
            "org.freedesktop.Secret.Collection.Label": GLib.Variant("s", label),
        }
        created = self._call(
            SERVICE_PATH, INTERNAL_IFACE, "CreateWithMasterPassword",
            GLib.Variant("(a{sv}(oayays))", (attributes, self._secret(password))),
            "(o)").unpack()[0]
        try:
            self._call(SERVICE_PATH, SERVICE_IFACE, "SetAlias",
                       GLib.Variant("(so)", ("login", created)), "")
        except KeyringError as error:
            # gnome-keyring only aliases 'default'; the name is what counts.
            log.info("the login alias was not set (%s)", error)
        return created
