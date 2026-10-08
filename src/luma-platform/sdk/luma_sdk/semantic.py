# SPDX-License-Identifier: Apache-2.0
"""Developer-only client for the authenticated Luma Semantic Broker."""

from __future__ import annotations

import json
import secrets
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from luma_semantic_broker.service import (
    BROKER_INTERFACE,
    BUS_NAME,
    OBJECT_PATH,
    REQUEST_INTERFACE,
)
from luma_semantic_broker.variant import encode, unbox, vardict


class SemanticClientError(RuntimeError):
    """A broker call or asynchronous request failed."""


class SemanticClient:
    """Small synchronous facade used by the SDK inspector.

    The broker still owns identity, grants, prompts, confirmation, redaction,
    rate limiting, and audit. This class deliberately contains no policy.
    """

    def __init__(self, connection: Gio.DBusConnection | None = None) -> None:
        self.connection = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def _call(
        self,
        method: str,
        parameters: GLib.Variant,
        reply: str | None,
    ) -> GLib.Variant:
        try:
            return self.connection.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                BROKER_INTERFACE,
                method,
                parameters,
                GLib.VariantType.new(reply) if reply else None,
                Gio.DBusCallFlags.NONE,
                10_000,
                None,
            )
        except GLib.Error as error:
            raise SemanticClientError(error.message) from error

    def list_surfaces(self) -> list[dict[str, Any]]:
        result = self._call("ListSurfaces", GLib.Variant("()", ()), "(aa{sv})")
        return unbox(result.get_child_value(0))

    def get_surface(self, publication_id: str) -> dict[str, Any]:
        result = self._call(
            "GetSurface", GLib.Variant("(s)", (publication_id,)), "(a{sv})"
        )
        return unbox(result.get_child_value(0))

    def request_access(
        self,
        application_id: str,
        scopes: list[str],
        *,
        duration: int = 3600,
    ) -> dict[str, Any]:
        return self._request(
            "RequestAccess",
            [
                GLib.Variant("s", application_id),
                GLib.Variant("as", scopes),
                vardict(
                    {
                        "handle_token": self._token(),
                        "duration": duration,
                        # Native developer tools intentionally receive only a
                        # session grant; durable grants require sandbox identity.
                        "persistent": False,
                    }
                ),
            ],
        )

    def revoke_access(self, application_id: str) -> None:
        self._call(
            "RevokeAccess", GLib.Variant("(s)", (application_id,)), "()"
        )

    def invoke_action(
        self,
        publication_id: str,
        object_id: str,
        action_id: str,
        parameter: Any,
    ) -> dict[str, Any]:
        return self._request(
            "InvokeAction",
            [
                GLib.Variant("s", publication_id),
                GLib.Variant("s", object_id),
                GLib.Variant("s", action_id),
                GLib.Variant.new_variant(encode(parameter)),
                vardict({"handle_token": self._token()}),
            ],
        )

    def audit(self, limit: int = 50) -> list[dict[str, Any]]:
        result = self._call(
            "GetAudit", GLib.Variant("(u)", (limit,)), "(aa{sv})"
        )
        return unbox(result.get_child_value(0))

    @staticmethod
    def _token() -> str:
        return "sdk_" + secrets.token_hex(12)

    def _request(
        self,
        method: str,
        arguments: list[GLib.Variant],
    ) -> dict[str, Any]:
        loop = GLib.MainLoop()
        response: dict[str, Any] = {}
        request_path = ""

        def signaled(
            _connection,
            _sender,
            path,
            _interface,
            _signal,
            parameters,
        ) -> None:
            # Ignore unrelated responses from a second tool using this same
            # bus connection. The broker also destination-addresses signals.
            if request_path and path != request_path:
                return
            code, results = unbox(parameters)
            response["code"] = code
            response["results"] = results
            loop.quit()

        subscription = self.connection.signal_subscribe(
            BUS_NAME,
            REQUEST_INTERFACE,
            "Response",
            None,
            None,
            Gio.DBusSignalFlags.NONE,
            signaled,
        )
        timeout = GLib.timeout_add_seconds(65, loop.quit)
        try:
            returned = self._call(
                method,
                GLib.Variant.new_tuple(*arguments),
                "(o)",
            )
            request_path = returned.unpack()[0]
            if "code" not in response:
                loop.run()
        finally:
            if GLib.MainContext.default().find_source_by_id(timeout) is not None:
                GLib.source_remove(timeout)
            self.connection.signal_unsubscribe(subscription)
        if "code" not in response:
            if request_path:
                try:
                    self.connection.call_sync(
                        BUS_NAME,
                        request_path,
                        REQUEST_INTERFACE,
                        "Close",
                        None,
                        None,
                        Gio.DBusCallFlags.NONE,
                        2_000,
                        None,
                    )
                except GLib.Error:
                    pass
            raise SemanticClientError("semantic request timed out")
        if response["code"] != 0:
            reason = response["results"].get("reason", "request was denied")
            raise SemanticClientError(str(reason))
        return response["results"]


def json_parameter(value: str) -> Any:
    """Parse a JSON action parameter with an actionable CLI error."""

    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise SemanticClientError(f"invalid JSON parameter: {error.msg}") from error
