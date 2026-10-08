# SPDX-License-Identifier: Apache-2.0
"""Bounded, local-only GNOME Shell SearchProvider2 implementation."""
from __future__ import annotations

from gi.repository import Gio, GLib

from .store import MailStore


SEARCH_XML = """
<node>
  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet"><arg type="as" direction="in"/><arg type="as" direction="out"/></method>
    <method name="GetSubsearchResultSet"><arg type="as" direction="in"/><arg type="as" direction="in"/><arg type="as" direction="out"/></method>
    <method name="GetResultMetas"><arg type="as" direction="in"/><arg type="aa{sv}" direction="out"/></method>
    <method name="ActivateResult"><arg type="s" direction="in"/><arg type="as" direction="in"/><arg type="u" direction="in"/></method>
    <method name="LaunchSearch"><arg type="as" direction="in"/><arg type="u" direction="in"/></method>
  </interface>
</node>
"""


class SearchProvider:
    """Exports message search without persisting or logging query text."""

    OBJECT_PATH = "/org/projectluma/Charlie/SearchProvider"

    def __init__(self, application, store: MailStore) -> None:
        self.application = application
        self.store = store
        self.registration_id = 0

    def export(self, connection: Gio.DBusConnection) -> None:
        info = Gio.DBusNodeInfo.new_for_xml(SEARCH_XML).interfaces[0]
        self.registration_id = connection.register_object(
            self.OBJECT_PATH, info, self._method_call, None, None
        )

    def unexport(self, connection: Gio.DBusConnection | None) -> None:
        if connection is not None and self.registration_id:
            connection.unregister_object(self.registration_id)
            self.registration_id = 0

    def _method_call(self, _connection, _sender, _path, _interface, method, parameters, invocation) -> None:
        if method == "GetInitialResultSet":
            (terms,) = parameters.unpack()
            invocation.return_value(GLib.Variant("(as)", (self.store.search_message_ids(tuple(terms)),)))
            return
        if method == "GetSubsearchResultSet":
            _previous, terms = parameters.unpack()
            invocation.return_value(GLib.Variant("(as)", (self.store.search_message_ids(tuple(terms)),)))
            return
        if method == "GetResultMetas":
            (identifiers,) = parameters.unpack()
            messages = self.store.messages_by_ids(tuple(identifiers))
            metas = []
            for message in messages:
                metas.append({
                    "id": GLib.Variant("s", message.id),
                    "name": GLib.Variant("s", message.subject),
                    "description": GLib.Variant("s", message.sender_label),
                    "gicon": GLib.Variant("s", "org.projectluma.Charlie"),
                })
            invocation.return_value(GLib.Variant("(aa{sv})", (metas,)))
            return
        if method == "ActivateResult":
            identifier, _terms, _timestamp = parameters.unpack()
            self.application.activate()
            if self.application.window:
                self.application.window.open_message_id(identifier)
            invocation.return_value(None)
            return
        if method == "LaunchSearch":
            terms, _timestamp = parameters.unpack()
            self.application.activate()
            if self.application.window:
                self.application.window.search.set_text(" ".join(terms))
                self.application.window.search.grab_focus()
            invocation.return_value(None)
            return
        invocation.return_dbus_error("org.projectluma.Charlie.Error.UnknownMethod", "Unknown search method")
