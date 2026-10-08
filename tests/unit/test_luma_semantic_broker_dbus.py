# SPDX-License-Identifier: Apache-2.0
"""Real private-session-bus acceptance for the Semantic Broker adapter."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
BROKER = ROOT / "src/luma-platform/broker"
sys.path.insert(0, str(BROKER))

from luma_semantic_broker.core import BrokerCore  # noqa: E402
from luma_semantic_broker.client import SemanticPublisher  # noqa: E402
from luma_semantic_broker.identity import DBusCredentialResolver  # noqa: E402
from luma_semantic_broker.model import Identity, IdentityStrength  # noqa: E402
from luma_semantic_broker.service import (  # noqa: E402
    BROKER_INTERFACE,
    BUS_NAME,
    OBJECT_PATH,
    PROVIDER_INTERFACE,
    REQUEST_INTERFACE,
    REQUEST_ROOT,
    SemanticBrokerService,
)
from luma_semantic_broker.store import AuditStore, GrantStore  # noqa: E402
from luma_semantic_broker.variant import encode, vardict  # noqa: E402


FLAGS = (
    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
)


class PublisherRegressionTests(unittest.TestCase):
    def test_publisher_validates_id_without_shadowing_validator(self) -> None:
        class UnregisteredApplication:
            @staticmethod
            def get_dbus_connection():
                return None

        with self.assertRaisesRegex(RuntimeError, "registered before publishing"):
            SemanticPublisher(
                UnregisteredApplication(),
                "org.projectluma.Notes",
                "notes",
                lambda: {},
                {},
            )


def connection() -> Gio.DBusConnection:
    return Gio.DBusConnection.new_for_address_sync(
        os.environ["DBUS_SESSION_BUS_ADDRESS"], FLAGS, None, None
    )


def call(
    sender: Gio.DBusConnection,
    destination: str,
    path: str,
    interface: str,
    method: str,
    parameters: GLib.Variant,
    reply: str | None,
) -> GLib.Variant:
    loop = GLib.MainLoop()
    result = {}

    def finished(value: Gio.DBusConnection, asynchronous, _data=None) -> None:
        try:
            result["value"] = value.call_finish(asynchronous)
        except GLib.Error as error:
            result["error"] = error
        loop.quit()

    sender.call(
        destination,
        path,
        interface,
        method,
        parameters,
        GLib.VariantType.new(reply) if reply else None,
        Gio.DBusCallFlags.NONE,
        5_000,
        None,
        finished,
        None,
    )
    loop.run()
    if "error" in result:
        raise result["error"]
    return result["value"]


class FakeIdentityResolver:
    def __init__(self, values: dict[str, Identity]) -> None:
        self.values = values

    def resolve(self, sender: str) -> Identity:
        identity = self.values.get(sender)
        if identity is not None:
            return identity
        return Identity(
            f"transient:{os.getuid()}:{sender}",
            "",
            "Semantic Inspector acceptance",
            sender,
            os.getuid(),
            0,
            IdentityStrength.TRANSIENT_NATIVE,
        )

    def provider(self, sender: str, claimed: str) -> Identity:
        identity = self.resolve(sender)
        if identity.app_id != claimed:
            raise PermissionError("provider application identity does not match caller")
        return identity

    def require_shell_host(self, sender: str) -> Identity:
        return self.resolve(sender)


class AllowPrompt:
    def __init__(self) -> None:
        self.access = 0
        self.confirmations = 0

    def request_access(self, _client, _target, _scopes, _persistent, callback) -> None:
        self.access += 1
        GLib.idle_add(callback, True)

    def confirm_action(self, _client, _target, _action, _risk, callback) -> None:
        self.confirmations += 1
        GLib.idle_add(callback, True)


class HoldingPrompt:
    def request_access(
        self, _client, _target, _scopes, _persistent, callback
    ) -> None:
        self.callback = callback

    def confirm_action(self, _client, _target, _action, _risk, callback) -> None:
        self.callback = callback


def semantic_surface() -> dict:
    return {
        "schema_version": "0.1",
        "id": "notes",
        "kind": "application",
        "name": "Notes",
        "privacy": "public",
        "actions": [
            {
                "id": "notes.create",
                "label": "Create note",
                "risk": "low",
                "enabled": True,
            }
        ],
        "children": [
            {
                "schema_version": "0.1",
                "id": "note:one.md",
                "kind": "document",
                "name": "Private title",
                "privacy": "private",
                "actions": [
                    {
                        "id": "notes.delete",
                        "label": "Move note to Trash",
                        "risk": "destructive",
                        "enabled": True,
                    }
                ],
                "children": [],
            }
        ],
    }


def live_extension() -> dict:
    now = datetime.now(UTC)
    return {
        "schema_version": "0.1",
        "id": "notes.current-activity",
        "app_id": "org.projectluma.Notes",
        "category": "generic",
        "title": "Writing release notes",
        "subtitle": "Project Luma",
        "privacy": "private",
        "progress": -1.0,
        "actions": [],
        "starts_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }


class SessionBusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service_connection = connection()
        self.provider_connection = connection()
        self.agent_connection = connection()
        self.attacker_connection = connection()
        request_name = self.service_connection.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "RequestName",
            GLib.Variant("(su)", (BUS_NAME, 0)),
            GLib.VariantType.new("(u)"),
            Gio.DBusCallFlags.NONE,
            5_000,
            None,
        )
        self.assertEqual(request_name.unpack()[0], 1)
        provider_sender = self.provider_connection.get_unique_name()
        agent_sender = self.agent_connection.get_unique_name()
        attacker_sender = self.attacker_connection.get_unique_name()
        identities = {
            provider_sender: Identity(
                "native:org.projectluma.Notes",
                "org.projectluma.Notes",
                "Notes",
                provider_sender,
                os.getuid(),
                os.getpid(),
                IdentityStrength.MANAGED_NATIVE,
            ),
            agent_sender: Identity(
                "flatpak:org.example.Agent",
                "org.example.Agent",
                "Agent",
                agent_sender,
                os.getuid(),
                os.getpid(),
                IdentityStrength.SANDBOXED,
            ),
            attacker_sender: Identity(
                "flatpak:org.example.Attacker",
                "org.example.Attacker",
                "Attacker",
                attacker_sender,
                os.getuid(),
                os.getpid(),
                IdentityStrength.SANDBOXED,
            ),
        }
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.locked = False
        core = BrokerCore(
            grant_store=GrantStore(root / "grants.json"),
            audit_store=AuditStore(root / "audit.jsonl"),
            locked=lambda: self.locked,
        )
        self.prompt = AllowPrompt()
        self.service = SemanticBrokerService(
            self.service_connection,
            xml_path=ROOT / "src/luma-platform/broker/org.projectluma.SemanticBroker1.xml",
            prompt=self.prompt,
            core=core,
            identity=FakeIdentityResolver(identities),
        )
        self.provider_calls = []
        provider_info = next(
            item
            for item in self.service.node.interfaces
            if item.name == PROVIDER_INTERFACE
        )

        def provider_method(
            _connection,
            sender,
            _path,
            _interface,
            method,
            parameters,
            invocation,
        ) -> None:
            self.assertEqual(sender, self.service_connection.get_unique_name())
            self.assertEqual(method, "InvokeAction")
            object_id = parameters.get_child_value(0).get_string()
            action_id = parameters.get_child_value(1).get_string()
            self.provider_calls.append((object_id, action_id))
            invocation.return_value(
                GLib.Variant.new_tuple(
                    GLib.Variant.new_variant(GLib.Variant("a{sv}", {}))
                )
            )

        self.provider_path = "/org/projectluma/Notes/Semantics"
        self.provider_registration = self.provider_connection.register_object_with_closures2(
            self.provider_path, provider_info, provider_method, None, None
        )

    def tearDown(self) -> None:
        self.provider_connection.unregister_object(self.provider_registration)
        self.service.close()
        for item in (
            self.attacker_connection,
            self.agent_connection,
            self.provider_connection,
            self.service_connection,
        ):
            item.close_sync(None)
        self.temporary.cleanup()

    def broker_call(
        self,
        sender: Gio.DBusConnection,
        method: str,
        parameters: GLib.Variant,
        reply: str | None,
    ) -> GLib.Variant:
        return call(
            sender,
            BUS_NAME,
            OBJECT_PATH,
            BROKER_INTERFACE,
            method,
            parameters,
            reply,
        )

    def request(
        self,
        method: str,
        arguments: list[GLib.Variant],
        token: str,
    ) -> tuple[int, dict]:
        path = (
            f"{REQUEST_ROOT}/"
            f"{self.agent_connection.get_unique_name()[1:].replace('.', '_')}/{token}"
        )
        loop = GLib.MainLoop()
        response = {}

        def signaled(
            _connection,
            _sender,
            _path,
            _interface,
            _signal,
            parameters,
        ) -> None:
            response["value"] = parameters.unpack()
            loop.quit()

        subscription = self.agent_connection.signal_subscribe(
            None,
            REQUEST_INTERFACE,
            "Response",
            path,
            None,
            Gio.DBusSignalFlags.NONE,
            signaled,
        )
        returned = self.broker_call(
            self.agent_connection,
            method,
            GLib.Variant.new_tuple(*arguments),
            "(o)",
        )
        self.assertEqual(returned.unpack()[0], path)
        loop.run()
        self.agent_connection.signal_unsubscribe(subscription)
        code, results = response["value"]
        return code, results

    def test_identity_permissions_actions_lock_revoke_and_disconnect(self) -> None:
        uid, pid = DBusCredentialResolver(self.service_connection).credentials(
            self.agent_connection.get_unique_name()
        )
        self.assertEqual((uid, pid), (os.getuid(), os.getpid()))
        registration = GLib.Variant.new_tuple(
            GLib.Variant("s", "org.projectluma.Notes"),
            GLib.Variant("s", "notes"),
            vardict(semantic_surface()),
            GLib.Variant("o", self.provider_path),
        )
        publication = self.broker_call(
            self.provider_connection, "RegisterSurface", registration, "(s)"
        ).unpack()[0]

        with self.assertRaises(GLib.Error):
            self.broker_call(
                self.attacker_connection, "RegisterSurface", registration, "(s)"
            )

        code, result = self.request(
            "RequestAccess",
            [
                GLib.Variant("s", "org.projectluma.Notes"),
                GLib.Variant(
                    "as",
                    [
                        "observe-public",
                        "invoke-low-risk",
                    ],
                ),
                vardict(
                    {
                        "handle_token": "access1",
                        "duration": 3600,
                        "persistent": False,
                    }
                ),
            ],
            "access1",
        )
        self.assertEqual(code, 0)
        self.assertTrue(result["granted"])
        audit = self.broker_call(
            self.agent_connection,
            "GetAudit",
            GLib.Variant("(u)", (10,)),
            "(aa{sv})",
        ).unpack()[0]
        self.assertEqual(audit[-1]["operation"], "access-request")
        self.assertTrue(audit[-1]["allowed"])

        with self.assertRaises(GLib.Error):
            self.broker_call(
                self.agent_connection,
                "InvokeAction",
                GLib.Variant.new_tuple(
                    GLib.Variant("s", publication),
                    GLib.Variant("s", "notes"),
                    GLib.Variant("s", "notes.create"),
                    GLib.Variant.new_variant(GLib.Variant("s", "unexpected")),
                    vardict({"handle_token": "badparameter"}),
                ),
                "(o)",
            )
        listed = self.broker_call(
            self.agent_connection, "ListSurfaces", GLib.Variant("()", ()), "(aa{sv})"
        ).unpack()[0]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["surface"]["children"], [])

        code, result = self.request(
            "RequestAccess",
            [
                GLib.Variant("s", "org.projectluma.Notes"),
                GLib.Variant(
                    "as",
                    [
                        "observe-public",
                        "observe-private",
                        "invoke-low-risk",
                        "invoke-destructive",
                    ],
                ),
                vardict(
                    {
                        "handle_token": "access2",
                        "duration": 3600,
                        "persistent": False,
                    }
                ),
            ],
            "access2",
        )
        self.assertEqual(code, 0)
        self.assertTrue(result["granted"])

        code, result = self.request(
            "InvokeAction",
            [
                GLib.Variant("s", publication),
                GLib.Variant("s", "notes"),
                GLib.Variant("s", "notes.create"),
                GLib.Variant.new_variant(GLib.Variant("a{sv}", {})),
                vardict({"handle_token": "invoke1"}),
            ],
            "invoke1",
        )
        self.assertEqual((code, result["completed"]), (0, True))
        self.assertEqual(result["result"], {})
        self.assertEqual(self.provider_calls, [("notes", "notes.create")])

        code, result = self.request(
            "InvokeAction",
            [
                GLib.Variant("s", publication),
                GLib.Variant("s", "note:one.md"),
                GLib.Variant("s", "notes.delete"),
                GLib.Variant.new_variant(GLib.Variant("a{sv}", {})),
                vardict({"handle_token": "invoke2"}),
            ],
            "invoke2",
        )
        self.assertEqual((code, result["completed"]), (0, True))
        self.assertEqual(self.prompt.confirmations, 1)

        self.locked = True
        listed = self.broker_call(
            self.agent_connection, "ListSurfaces", GLib.Variant("()", ()), "(aa{sv})"
        ).unpack()[0]
        self.assertEqual(listed, [])
        self.locked = False

        self.broker_call(
            self.agent_connection,
            "RevokeAccess",
            GLib.Variant("(s)", ("org.projectluma.Notes",)),
            "()",
        )
        listed = self.broker_call(
            self.agent_connection, "ListSurfaces", GLib.Variant("()", ()), "(aa{sv})"
        ).unpack()[0]
        self.assertEqual(listed, [])

    def test_live_extension_provider_and_system_host_contract(self) -> None:
        registration = GLib.Variant.new_tuple(
            GLib.Variant("s", "org.projectluma.Notes"),
            GLib.Variant("s", "notes.current-activity"),
            vardict(live_extension()),
            GLib.Variant("o", "/org/projectluma/Notes/LiveExtension"),
        )
        publication = self.broker_call(
            self.provider_connection,
            "RegisterLiveExtension",
            registration,
            "(s)",
        ).unpack()[0]
        listed = self.broker_call(
            self.agent_connection,
            "ListLiveExtensions",
            GLib.Variant("()", ()),
            "(aa{sv})",
        ).unpack()[0]
        self.assertEqual(listed[0]["extension"]["title"], "Writing release notes")
        self.locked = True
        redacted = self.broker_call(
            self.agent_connection,
            "ListLiveExtensions",
            GLib.Variant("()", ()),
            "(aa{sv})",
        ).unpack()[0]
        self.assertEqual(redacted[0]["extension"]["title"], "Ongoing activity")
        self.assertNotIn("subtitle", redacted[0]["extension"])
        with self.assertRaises(GLib.Error):
            self.broker_call(
                self.attacker_connection,
                "UnregisterLiveExtension",
                GLib.Variant("(s)", (publication,)),
                "()",
            )
        self.broker_call(
            self.provider_connection,
            "UnregisterLiveExtension",
            GLib.Variant("(s)", (publication,)),
            "()",
        )

    def test_one_caller_cannot_stack_consent_prompts(self) -> None:
        self.service.prompt = HoldingPrompt()
        self.broker_call(
            self.provider_connection,
            "RegisterSurface",
            GLib.Variant.new_tuple(
                GLib.Variant("s", "org.projectluma.Notes"),
                GLib.Variant("s", "notes"),
                vardict(semantic_surface()),
                GLib.Variant("o", self.provider_path),
            ),
            "(s)",
        )
        options = vardict(
            {"handle_token": "held", "duration": 3600, "persistent": False}
        )
        returned = self.broker_call(
            self.agent_connection,
            "RequestAccess",
            GLib.Variant.new_tuple(
                GLib.Variant("s", "org.projectluma.Notes"),
                GLib.Variant("as", ["observe-public"]),
                options,
            ),
            "(o)",
        )
        request_path = returned.unpack()[0]
        with self.assertRaises(GLib.Error):
            self.broker_call(
                self.agent_connection,
                "RequestAccess",
                GLib.Variant.new_tuple(
                    GLib.Variant("s", "org.projectluma.Notes"),
                    GLib.Variant("as", ["observe-public"]),
                    vardict(
                        {
                            "handle_token": "second",
                            "duration": 3600,
                            "persistent": False,
                        }
                    ),
                ),
                "(o)",
            )
        call(
            self.agent_connection,
            BUS_NAME,
            request_path,
            REQUEST_INTERFACE,
            "Close",
            GLib.Variant("()", ()),
            None,
        )
        self.assertEqual(self.service.requests, {})

    def test_sdk_inspector_uses_the_real_broker_contract(self) -> None:
        publication = self.broker_call(
            self.provider_connection,
            "RegisterSurface",
            GLib.Variant.new_tuple(
                GLib.Variant("s", "org.projectluma.Notes"),
                GLib.Variant("s", "notes"),
                vardict(semantic_surface()),
                GLib.Variant("o", self.provider_path),
            ),
            "(s)",
        ).unpack()[0]

        code = f"""
import json
from luma_sdk.semantic import SemanticClient

client = SemanticClient()
grant = client.request_access(
    "org.projectluma.Notes",
    ["observe-public", "observe-private", "invoke-low-risk"],
)
surfaces = client.list_surfaces()
created = client.invoke_action(
    {publication!r}, "notes", "notes.create", {{}},
)
audit = client.audit(50)
client.revoke_access("org.projectluma.Notes")
print(json.dumps({{
    "grant": grant,
    "surfaces": surfaces,
    "created": created,
    "audit": audit,
    "after_revoke": client.list_surfaces(),
}}))
"""
        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        )
        launcher.setenv(
            "PYTHONPATH",
            f"{ROOT / 'src/luma-platform/sdk'}:{BROKER}",
            True,
        )
        process = launcher.spawnv((sys.executable, "-c", code))
        loop = GLib.MainLoop()
        completed = {}

        def finished(value: Gio.Subprocess, result, _data=None) -> None:
            try:
                ok, stdout, stderr = value.communicate_utf8_finish(result)
                completed.update(ok=ok, stdout=stdout, stderr=stderr)
            except GLib.Error as error:
                completed["error"] = error
            loop.quit()

        process.communicate_utf8_async(None, None, finished, None)
        loop.run()
        self.assertNotIn("error", completed)
        self.assertTrue(completed["ok"], completed["stderr"])
        self.assertEqual(process.get_exit_status(), 0, completed["stderr"])
        report = json.loads(completed["stdout"])
        self.assertTrue(report["grant"]["granted"])
        self.assertEqual(len(report["surfaces"]), 1)
        self.assertEqual(
            report["surfaces"][0]["surface"]["children"][0]["name"],
            "Private title",
        )
        self.assertTrue(report["created"]["completed"])
        self.assertEqual(report["after_revoke"], [])
        self.assertTrue(any(item["operation"] == "invoke" for item in report["audit"]))


if __name__ == "__main__":
    unittest.main()
