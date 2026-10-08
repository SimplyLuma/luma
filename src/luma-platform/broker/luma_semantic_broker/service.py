# SPDX-License-Identifier: Apache-2.0
"""Authenticated per-user D-Bus adapter for the Luma Semantic Broker."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import re

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from .core import BrokerCore, BrokerError
from .desktop import desktop_application
from .identity import DBusCredentialResolver, IdentityError, IdentityResolver
from .lock import SessionLockMonitor
from .prompt import PromptRunner
from .store import AuditStore, GrantStore, StoreError
from .validation import ValidationError, application_id, scopes as validate_scopes
from .variant import encode, unbox, vardict


BUS_NAME = "org.projectluma.SemanticBroker1"
OBJECT_PATH = "/org/projectluma/SemanticBroker1"
BROKER_INTERFACE = BUS_NAME
REQUEST_INTERFACE = "org.projectluma.SemanticRequest1"
PROVIDER_INTERFACE = "org.projectluma.SemanticProvider1"
REQUEST_ROOT = "/org/projectluma/SemanticBroker1/request"
ERROR = "org.projectluma.SemanticBroker1.Error.Refused"
DEFAULT_XML = Path("/usr/share/dbus-1/interfaces/org.projectluma.SemanticBroker1.xml")
MAX_MESSAGE_BYTES = 512 * 1024
MAX_PENDING_REQUESTS = 8
HANDLE_TOKEN = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def _tuple_variant(*children: GLib.Variant) -> GLib.Variant:
    return GLib.Variant.new_tuple(*children)


def _array_of_dicts(values: tuple[dict, ...] | list[dict]) -> GLib.Variant:
    return GLib.Variant(
        "aa{sv}",
        [{key: encode(item) for key, item in value.items()} for value in values],
    )


class BrokerRequest:
    def __init__(
        self,
        service: "SemanticBrokerService",
        sender: str,
        path: str,
    ) -> None:
        self.service = service
        self.sender = sender
        self.path = path
        self.closed = False
        self.prompt_process: Gio.Subprocess | None = None
        self.registration = service.connection.register_object_with_closures2(
            self.path,
            service.request_info,
            self._method_call,
            None,
            None,
        )

    def _method_call(
        self,
        _connection,
        sender,
        _object_path,
        _interface_name,
        method_name,
        _parameters,
        invocation,
    ) -> None:
        if sender != self.sender or method_name != "Close":
            invocation.return_dbus_error(ERROR, "request belongs to another caller")
            return
        self.close()
        invocation.return_value(None)

    def respond(self, response: int, results: dict[str, Any]) -> None:
        if self.closed:
            return
        self.service.connection.emit_signal(
            self.sender,
            self.path,
            REQUEST_INTERFACE,
            "Response",
            _tuple_variant(GLib.Variant("u", response), vardict(results)),
        )
        self.close()

    def attach_prompt(self, process: Gio.Subprocess | None) -> None:
        self.prompt_process = process

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.prompt_process is not None:
            try:
                if not self.prompt_process.get_if_exited():
                    self.prompt_process.force_exit()
            except GLib.Error:
                pass
            self.prompt_process = None
        self.service.connection.unregister_object(self.registration)
        self.service.requests.pop(self.path, None)


class SemanticBrokerService:
    def __init__(
        self,
        connection: Gio.DBusConnection,
        *,
        xml_path: Path | None = None,
        prompt: PromptRunner | None = None,
        core: BrokerCore | None = None,
        identity: IdentityResolver | None = None,
    ) -> None:
        self.connection = connection
        selected_xml = xml_path or Path(os.environ.get("LUMA_SEMANTIC_BROKER_XML", DEFAULT_XML))
        self.node = Gio.DBusNodeInfo.new_for_xml(selected_xml.read_text(encoding="utf-8"))
        interfaces = {item.name: item for item in self.node.interfaces}
        self.broker_info = interfaces[BROKER_INTERFACE]
        self.request_info = interfaces[REQUEST_INTERFACE]
        credentials = DBusCredentialResolver(connection)
        self.identity = identity or IdentityResolver(credentials)
        monitor = SessionLockMonitor(connection)
        self.core = core or BrokerCore(
            grant_store=GrantStore(),
            audit_store=AuditStore(),
            locked=monitor.locked,
        )
        self.prompt = prompt or PromptRunner()
        self.requests: dict[str, BrokerRequest] = {}
        self.registration = connection.register_object_with_closures2(
            OBJECT_PATH,
            self.broker_info,
            self._method_call,
            None,
            None,
        )
        self.owner_subscription = connection.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            "/org/freedesktop/DBus",
            None,
            Gio.DBusSignalFlags.NONE,
            self._name_owner_changed,
        )

    def _name_owner_changed(
        self,
        _connection,
        _sender,
        _path,
        _interface,
        _signal,
        parameters,
    ) -> None:
        name, old_owner, new_owner = parameters.unpack()
        if name.startswith(":") and old_owner and not new_owner:
            had_live_extensions = any(
                item.owner_sender == name
                for item in self.core.live_extensions.values()
            )
            self.core.disconnect(name)
            if had_live_extensions:
                self._emit_live_extensions_changed()
            for request in tuple(self.requests.values()):
                if request.sender == name:
                    request.close()

    def _new_request(self, sender: str, token: str) -> BrokerRequest:
        if not isinstance(token, str) or not HANDLE_TOKEN.fullmatch(token):
            raise ValidationError("invalid or missing semantic request handle token")
        path = f"{REQUEST_ROOT}/{sender[1:].replace('.', '_')}/{token}"
        if path in self.requests:
            raise ValidationError("semantic request handle token is already active")
        if any(item.sender == sender for item in self.requests.values()):
            raise BrokerError("caller already has a pending semantic request")
        if len(self.requests) >= MAX_PENDING_REQUESTS:
            raise BrokerError("semantic broker is busy")
        request = BrokerRequest(self, sender, path)
        self.requests[request.path] = request
        return request

    @staticmethod
    def _options(value: Any, allowed: set[str]) -> dict[str, Any]:
        options = unbox(value)
        if not isinstance(options, dict) or set(options) - allowed:
            raise ValidationError("invalid semantic request options")
        return options

    def _target_label(self, app_id: str) -> str:
        found, label = desktop_application(app_id)
        return label if found else app_id

    def _emit_live_extensions_changed(self) -> None:
        self.connection.emit_signal(
            None,
            OBJECT_PATH,
            BROKER_INTERFACE,
            "LiveExtensionsChanged",
            None,
        )

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
        try:
            if parameters.get_size() > MAX_MESSAGE_BYTES:
                raise ValidationError("semantic broker message exceeds the size limit")
            if method_name == "RegisterSurface":
                app_id, surface_id, raw_surface, provider_path = parameters.unpack()
                provider = self.identity.provider(sender, app_id)
                publication = self.core.register_surface(
                    provider,
                    app_id,
                    surface_id,
                    unbox(raw_surface),
                    provider_path,
                )
                invocation.return_value(GLib.Variant("(s)", (publication.publication_id,)))
                return
            if method_name == "RegisterLiveExtension":
                app_id, extension_id, raw_extension, provider_path = parameters.unpack()
                provider = self.identity.provider(sender, app_id)
                publication = self.core.register_live_extension(
                    provider, app_id, extension_id, unbox(raw_extension), provider_path
                )
                invocation.return_value(GLib.Variant("(s)", (publication.publication_id,)))
                self._emit_live_extensions_changed()
                return
            client = self.identity.resolve(sender)
            if method_name == "UpdateLiveExtension":
                publication_id, raw_extension = parameters.unpack()
                self.core.update_live_extension(client, publication_id, unbox(raw_extension))
                invocation.return_value(None)
                self._emit_live_extensions_changed()
                return
            if method_name == "UnregisterLiveExtension":
                self.core.unregister_live_extension(client, parameters.unpack()[0])
                invocation.return_value(None)
                self._emit_live_extensions_changed()
                return
            if method_name == "ListLiveExtensions":
                self.identity.require_shell_host(sender)
                visible = self.core.list_live_extensions()
                invocation.return_value(_tuple_variant(_array_of_dicts(visible)))
                return
            if method_name == "UpdateSurface":
                publication_id, raw_surface = parameters.unpack()
                self.core.update_surface(client, publication_id, unbox(raw_surface))
                invocation.return_value(None)
                return
            if method_name == "UnregisterSurface":
                self.core.unregister_surface(client, parameters.unpack()[0])
                invocation.return_value(None)
                return
            if method_name == "ListSurfaces":
                visible = self.core.list_surfaces(client)
                invocation.return_value(_tuple_variant(_array_of_dicts(visible)))
                return
            if method_name == "GetSurface":
                visible = self.core.get_surface(client, parameters.unpack()[0])
                invocation.return_value(_tuple_variant(vardict(visible)))
                return
            if method_name == "RequestAccess":
                target, raw_scopes, raw_options = parameters.unpack()
                target = application_id(target)
                requested = validate_scopes(raw_scopes)
                options = self._options(
                    raw_options, {"duration", "persistent", "handle_token"}
                )
                duration = options.get("duration", 3600)
                persistent = options.get("persistent", False)
                if not isinstance(persistent, bool):
                    raise ValidationError("persistent access option must be boolean")
                if not any(item.application_id == target for item in self.core.surfaces.values()):
                    raise BrokerError("target application has no published semantic surface")
                request = self._new_request(sender, options.get("handle_token", ""))
                invocation.return_value(GLib.Variant("(o)", (request.path,)))

                def access_response(allowed: bool) -> None:
                    if request.closed:
                        return
                    if not allowed:
                        try:
                            self.core.audit_decision(
                                client, "access-request", target, False, "user denied"
                            )
                        except StoreError:
                            pass
                        request.respond(1, {"granted": False})
                        return
                    try:
                        grant = self.core.grant_access(
                            client,
                            target,
                            [scope.value for scope in requested],
                            lifetime_seconds=duration,
                            persistent=persistent,
                        )
                        try:
                            self.core.audit_decision(
                                client, "access-request", target, True, "user granted"
                            )
                        except StoreError:
                            try:
                                self.core.revoke_access(client, target)
                            except StoreError:
                                pass
                            request.respond(
                                2,
                                {
                                    "granted": False,
                                    "reason": "semantic audit is unavailable",
                                },
                            )
                            return
                        request.respond(
                            0,
                            {
                                "granted": True,
                                "scopes": [scope.value for scope in grant.scopes],
                                "expires_at": grant.expires_at.isoformat(),
                            },
                        )
                    except (BrokerError, StoreError, ValidationError) as error:
                        try:
                            self.core.audit_decision(
                                client, "access-request", target, False, str(error)
                            )
                        except StoreError:
                            pass
                        request.respond(2, {"granted": False, "reason": str(error)})

                request.attach_prompt(
                    self.prompt.request_access(
                        client,
                        self._target_label(target),
                        tuple(scope.value for scope in requested),
                        persistent,
                        access_response,
                    )
                )
                return
            if method_name == "RevokeAccess":
                self.core.revoke_access(client, parameters.unpack()[0])
                invocation.return_value(None)
                return
            if method_name == "GetAudit":
                limit = parameters.unpack()[0]
                events = tuple(
                    event
                    for event in self.core.audit_store.tail(limit)
                    if event.get("client_key") == client.key
                )
                invocation.return_value(_tuple_variant(_array_of_dicts(events)))
                return
            if method_name == "InvokeAction":
                publication_id = parameters.get_child_value(0).get_string()
                object_id = parameters.get_child_value(1).get_string()
                action_id = parameters.get_child_value(2).get_string()
                parameter = parameters.get_child_value(3).get_variant()
                options = self._options(
                    parameters.get_child_value(4), {"handle_token"}
                )
                decision, surface, action = self._authorize_invocation(
                    sender,
                    client,
                    publication_id,
                    object_id,
                    action_id,
                    confirmed=False,
                )
                self._validate_parameter(action, parameter)
                request = self._new_request(sender, options.get("handle_token", ""))
                invocation.return_value(GLib.Variant("(o)", (request.path,)))
                if decision.allowed:
                    self._invoke_provider(
                        request, surface, object_id, action, parameter
                    )
                elif decision.confirmation_required:
                    request.attach_prompt(
                        self.prompt.confirm_action(
                            client,
                            self._target_label(surface.application_id),
                            action["label"],
                            action["risk"],
                            lambda allowed: self._confirmation_response(
                                request,
                                sender,
                                client,
                                surface,
                                object_id,
                                action,
                                parameter,
                                allowed,
                            ),
                        )
                    )
                else:
                    request.respond(2, {"completed": False, "reason": decision.reason})
                return
            raise BrokerError(f"unknown Semantic Broker method: {method_name}")
        except (
            BrokerError,
            IdentityError,
            StoreError,
            ValidationError,
            GLib.Error,
            OSError,
            TypeError,
            ValueError,
        ) as error:
            invocation.return_dbus_error(ERROR, str(error))

    def _authorize_invocation(
        self,
        sender: str,
        client,
        publication_id: str,
        object_id: str,
        action_id: str,
        *,
        confirmed: bool,
    ) -> tuple:
        """Route an invocation to the namespace that owns the publication.

        Live Extensions are published for the system Shell to render, so the
        Shell is authenticated exactly as it is for ``ListLiveExtensions``
        before any control it drew may be invoked.  Surfaces keep their
        existing grant-mediated path untouched, and neither namespace can
        resolve the other's publication identifier.
        """

        if publication_id in self.core.live_extensions:
            shell = self.identity.require_shell_host(sender)
            return self.core.authorize_live_invocation(
                shell, publication_id, object_id, action_id, confirmed=confirmed
            )
        return self.core.authorize_invocation(
            client, publication_id, object_id, action_id, confirmed=confirmed
        )

    def _confirmation_response(
        self,
        request: BrokerRequest,
        sender: str,
        client,
        surface,
        object_id: str,
        action: dict,
        parameter: GLib.Variant,
        confirmed: bool,
    ) -> None:
        if request.closed:
            return
        if not confirmed:
            try:
                self.core.audit_decision(
                    client,
                    "action-confirmation",
                    action["id"],
                    False,
                    "user denied",
                )
            except StoreError:
                pass
            request.respond(1, {"completed": False})
            return
        try:
            self.core.audit_decision(
                client,
                "action-confirmation",
                action["id"],
                True,
                "user confirmed",
            )
        except StoreError:
            request.respond(
                2, {"completed": False, "reason": "semantic audit is unavailable"}
            )
            return
        try:
            decision, surface, _action = self._authorize_invocation(
                sender,
                client,
                surface.publication_id,
                object_id,
                action["id"],
                confirmed=True,
            )
        except (BrokerError, StoreError, ValidationError) as error:
            request.respond(2, {"completed": False, "reason": str(error)})
            return
        if not decision.allowed:
            request.respond(2, {"completed": False, "reason": decision.reason})
            return
        self._invoke_provider(request, surface, object_id, action, parameter)

    @staticmethod
    def _validate_parameter(action: dict, parameter: GLib.Variant) -> None:
        expected = action.get("parameter_type")
        if expected is None:
            if parameter.get_type_string() != "a{sv}" or parameter.n_children() != 0:
                raise ValidationError("action does not accept a parameter")
            return
        if parameter.get_type_string() != expected:
            raise ValidationError("action parameter does not match its declared type")

    def _invoke_provider(
        self,
        request: BrokerRequest,
        surface,
        object_id: str,
        action: dict,
        parameter: GLib.Variant,
    ) -> None:
        try:
            self._validate_parameter(action, parameter)
        except (TypeError, ValidationError) as error:
            request.respond(2, {"completed": False, "reason": str(error)})
            return
        arguments = _tuple_variant(
            GLib.Variant("s", object_id),
            GLib.Variant("s", action["id"]),
            GLib.Variant.new_variant(parameter),
        )

        def finished(connection: Gio.DBusConnection, result, _data=None) -> None:
            try:
                returned = connection.call_finish(result)
                if returned.get_size() > MAX_MESSAGE_BYTES:
                    raise ValidationError("provider result exceeds the size limit")
                provider_result = returned.get_child_value(0).get_variant()
                request.respond(
                    0, {"completed": True, "result": provider_result}
                )
            except (GLib.Error, ValidationError):
                request.respond(2, {"completed": False, "reason": "provider action failed"})

        self.connection.call(
            surface.owner_sender,
            surface.provider_path,
            PROVIDER_INTERFACE,
            "InvokeAction",
            arguments,
            GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE,
            30_000,
            None,
            finished,
            None,
        )

    def close(self) -> None:
        for request in tuple(self.requests.values()):
            request.close()
        self.connection.signal_unsubscribe(self.owner_subscription)
        self.connection.unregister_object(self.registration)


def main() -> int:
    loop = GLib.MainLoop()
    holder: dict[str, SemanticBrokerService] = {}
    status = {"code": 1}

    def acquired(connection, _name) -> None:
        try:
            holder["service"] = SemanticBrokerService(connection)
            status["code"] = 0
        except (OSError, StoreError, ValueError, GLib.Error):
            status["code"] = 1
            loop.quit()

    def lost(_connection, _name) -> None:
        status["code"] = 1
        loop.quit()

    owner = Gio.bus_own_name(
        Gio.BusType.SESSION,
        BUS_NAME,
        Gio.BusNameOwnerFlags.NONE,
        acquired,
        None,
        lost,
    )
    try:
        loop.run()
    finally:
        service = holder.get("service")
        if service is not None:
            service.close()
        Gio.bus_unown_name(owner)
    return status["code"]


if __name__ == "__main__":
    raise SystemExit(main())
