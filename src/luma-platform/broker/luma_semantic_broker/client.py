# SPDX-License-Identifier: Apache-2.0
"""Application-side semantic publisher with a broker-only action endpoint."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
import os

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .service import (
    BROKER_INTERFACE,
    BUS_NAME,
    DEFAULT_XML,
    OBJECT_PATH,
    PROVIDER_INTERFACE,
)
from .validation import application_id as validate_application_id, stable_id
from .variant import encode, unbox, vardict


class SemanticPublisher:
    def __init__(
        self,
        application: Gio.Application,
        application_id: str,
        surface_id: str,
        root: Callable[[], Any],
        actions: dict[str, Callable[[str, Any], Any]],
        *,
        xml_path: Path | None = None,
    ) -> None:
        self.application = application
        self.application_id = validate_application_id(application_id)
        self.surface_id = stable_id(surface_id, "surface identifier")
        self.root = root
        self.actions = dict(actions)
        self.connection = application.get_dbus_connection()
        if self.connection is None:
            raise RuntimeError("application must be registered before publishing semantics")
        self.provider_path = (
            "/org/projectluma/SemanticProvider/" + self.application_id.replace(".", "/")
        )
        selected_xml = xml_path or Path(os.environ.get("LUMA_SEMANTIC_BROKER_XML", DEFAULT_XML))
        node = Gio.DBusNodeInfo.new_for_xml(selected_xml.read_text(encoding="utf-8"))
        provider = next(item for item in node.interfaces if item.name == PROVIDER_INTERFACE)
        self.registration = self.connection.register_object_with_closures2(
            self.provider_path,
            provider,
            self._method_call,
            None,
            None,
        )
        self.publication_id = ""
        self.owner_subscription = self.connection.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            "/org/freedesktop/DBus",
            BUS_NAME,
            Gio.DBusSignalFlags.NONE,
            self._broker_owner_changed,
        )
        self.publish()

    def _surface(self) -> GLib.Variant:
        root = self.root()
        value = root.to_variant() if hasattr(root, "to_variant") else root
        unpacked = unbox(value)
        if not isinstance(unpacked, dict):
            raise RuntimeError("semantic root did not serialize to a dictionary")
        return vardict(unpacked)

    def _call(self, method: str, arguments: GLib.Variant, reply: str | None) -> GLib.Variant:
        result = self.connection.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            BROKER_INTERFACE,
            method,
            arguments,
            GLib.VariantType.new(reply) if reply else None,
            Gio.DBusCallFlags.NONE,
            10_000,
            None,
        )
        return result

    def publish(self) -> bool:
        try:
            arguments = GLib.Variant.new_tuple(
                GLib.Variant("s", self.application_id),
                GLib.Variant("s", self.surface_id),
                self._surface(),
                GLib.Variant("o", self.provider_path),
            )
            self.publication_id = self._call("RegisterSurface", arguments, "(s)").unpack()[0]
            return True
        except (GLib.Error, OSError, RuntimeError, TypeError, ValueError):
            self.publication_id = ""
            return False

    def update(self) -> bool:
        if not self.publication_id and not self.publish():
            return False
        try:
            self._call(
                "UpdateSurface",
                GLib.Variant.new_tuple(
                    GLib.Variant("s", self.publication_id), self._surface()
                ),
                "()",
            )
            return True
        except (GLib.Error, OSError, RuntimeError, TypeError, ValueError):
            return False

    def _broker_owner(self) -> str:
        try:
            result = self.connection.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetNameOwner",
                GLib.Variant("(s)", (BUS_NAME,)),
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE,
                2_000,
                None,
            )
            return result.unpack()[0]
        except GLib.Error:
            return ""

    def _method_call(
        self,
        _connection,
        sender,
        _object_path,
        _interface_name,
        method_name,
        parameters,
        invocation,
    ) -> None:
        if sender != self._broker_owner() or method_name != "InvokeAction":
            invocation.return_dbus_error(
                "org.projectluma.SemanticProvider1.Error.Refused",
                "semantic actions may only be invoked by the authenticated broker",
            )
            return
        object_id = parameters.get_child_value(0).get_string()
        action_id = parameters.get_child_value(1).get_string()
        parameter = unbox(parameters.get_child_value(2).get_variant())
        handler = self.actions.get(action_id)
        if handler is None:
            invocation.return_dbus_error(
                "org.projectluma.SemanticProvider1.Error.UnknownAction",
                "unknown semantic action",
            )
            return
        try:
            result = handler(object_id, parameter)
            invocation.return_value(
                GLib.Variant.new_tuple(GLib.Variant.new_variant(encode(result)))
            )
            GLib.idle_add(self.update)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            invocation.return_dbus_error(
                "org.projectluma.SemanticProvider1.Error.Failed", str(error)
            )

    def _broker_owner_changed(
        self,
        _connection,
        _sender,
        _path,
        _interface,
        _signal,
        parameters,
    ) -> None:
        _name, _old_owner, new_owner = parameters.unpack()
        if new_owner:
            GLib.idle_add(self.publish)
        else:
            self.publication_id = ""

    def close(self) -> None:
        if self.publication_id:
            try:
                self._call(
                    "UnregisterSurface",
                    GLib.Variant("(s)", (self.publication_id,)),
                    "()",
                )
            except GLib.Error:
                pass
        self.connection.signal_unsubscribe(self.owner_subscription)
        self.connection.unregister_object(self.registration)
        self.publication_id = ""


class LiveExtensionPublisher:
    """Publish one replaceable activity while keeping all rendering in Shell."""

    def __init__(
        self,
        application: Gio.Application,
        application_id: str,
        extension_id: str,
        extension: Callable[[], Any | None],
    ) -> None:
        self.application = application
        self.application_id = validate_application_id(application_id)
        self.extension_id = stable_id(extension_id, "Live Extension identifier")
        self.extension = extension
        self.connection = application.get_dbus_connection()
        if self.connection is None:
            raise RuntimeError("application must be registered before publishing activity")
        self.provider_path = (
            "/org/projectluma/LiveExtensionProvider/" + self.application_id.replace(".", "/")
        )
        self.publication_id = ""
        self.owner_subscription = self.connection.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            "/org/freedesktop/DBus",
            BUS_NAME,
            Gio.DBusSignalFlags.NONE,
            self._broker_owner_changed,
        )
        self.sync()

    def _call(self, method: str, arguments: GLib.Variant, reply: str | None) -> GLib.Variant:
        return self.connection.call_sync(
            BUS_NAME, OBJECT_PATH, BROKER_INTERFACE, method, arguments,
            GLib.VariantType.new(reply) if reply else None,
            Gio.DBusCallFlags.NONE, 10_000, None,
        )

    @staticmethod
    def _payload(extension: Any) -> GLib.Variant:
        value = extension.to_variant() if hasattr(extension, "to_variant") else extension
        unpacked = unbox(value)
        if not isinstance(unpacked, dict):
            raise RuntimeError("Live Extension did not serialize to a dictionary")
        return vardict(unpacked)

    def sync(self) -> bool:
        """Synchronize once and always remove a GLib idle source callback.

        Direct callers do not use the return value. Returning SOURCE_REMOVE is
        deliberate: this method is also scheduled after broker ownership
        changes, where a truthy success value would create an unbounded idle
        loop and repeatedly signal the system host.
        """
        try:
            extension = self.extension()
            if extension is None:
                if self.publication_id:
                    self._call(
                        "UnregisterLiveExtension",
                        GLib.Variant("(s)", (self.publication_id,)), "()",
                    )
                    self.publication_id = ""
                return GLib.SOURCE_REMOVE
            payload = self._payload(extension)
            if self.publication_id:
                self._call(
                    "UpdateLiveExtension",
                    GLib.Variant.new_tuple(GLib.Variant("s", self.publication_id), payload),
                    "()",
                )
            else:
                arguments = GLib.Variant.new_tuple(
                    GLib.Variant("s", self.application_id),
                    GLib.Variant("s", self.extension_id),
                    payload,
                    GLib.Variant("o", self.provider_path),
                )
                self.publication_id = self._call(
                    "RegisterLiveExtension", arguments, "(s)"
                ).unpack()[0]
            return GLib.SOURCE_REMOVE
        except (GLib.Error, OSError, RuntimeError, TypeError, ValueError):
            self.publication_id = ""
            return GLib.SOURCE_REMOVE

    def _broker_owner_changed(self, _connection, _sender, _path, _interface, _signal, parameters) -> None:
        _name, _old_owner, new_owner = parameters.unpack()
        if new_owner:
            GLib.idle_add(self.sync)
        else:
            self.publication_id = ""

    def close(self) -> None:
        if self.publication_id:
            try:
                self._call(
                    "UnregisterLiveExtension",
                    GLib.Variant("(s)", (self.publication_id,)), "()",
                )
            except GLib.Error:
                pass
        self.connection.signal_unsubscribe(self.owner_subscription)
        self.publication_id = ""
