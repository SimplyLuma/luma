# SPDX-License-Identifier: Apache-2.0
"""org.projectluma.Update1 on a real bus, read by Depot's real parser.

The unit tests exercise the engine directly; this one puts the interface on a
private bus, exactly as ``introspection_xml()`` declares it, answers it from a
real ``Engine`` over a fake root, and reads it back with the module Depot uses
(``luma_installer.depot_system_update``). It catches what neither side can see
alone: a property whose D-Bus type does not match its value, a method signature
Depot cannot call, and a new field Depot drops on the floor.

Skipped without PyGObject or a dbus-daemon (a Mac); it runs on the build
container and on the VM rig.
"""

from pathlib import Path
import sys
import time
import unittest

import fakes
from fakes import graph_doc, release

try:
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
except (ImportError, ValueError):  # no GObject introspection here
    Gio = None

DEPOT_MODEL = Path(__file__).resolve().parents[2] / "luma-installer"
if DEPOT_MODEL.is_dir() and str(DEPOT_MODEL) not in sys.path:
    sys.path.insert(0, str(DEPOT_MODEL))
try:
    from luma_installer import depot_system_update as su
except ImportError:
    su = None


@unittest.skipIf(Gio is None, "PyGObject is not available")
@unittest.skipIf(su is None, "Depot's reading module is not beside this package")
class Surface(unittest.TestCase):
    """One object, one bus, both sides of the contract."""

    def setUp(self):
        from luma_update.dbus_interface import BUS_NAME, INTERFACE, METHODS, OBJECT_PATH, introspection_xml
        from luma_update.status import DBUS_TYPES, dbus_name, snake_name
        self.rig = fakes.Rig()
        self.rig.publish(graph_doc([release("1.0.0", 1), release("1.0.1", 2)]))
        self.engine = self.rig.engine()
        self.engine.refresh()
        self.bus_name, self.interface, self.path, self.methods = BUS_NAME, INTERFACE, OBJECT_PATH, METHODS
        self.types, self.dbus_name, self.snake_name = DBUS_TYPES, dbus_name, snake_name

        self.test_bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
        self.test_bus.up()
        self.addCleanup(self.test_bus.down)
        self.address = self.test_bus.get_bus_address()
        self.connection = Gio.DBusConnection.new_for_address_sync(
            self.address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        self.addCleanup(self.rig.close)

        node = Gio.DBusNodeInfo.new_for_xml(introspection_xml())
        self.registration = self.connection.register_object(
            self.path, node.interfaces[0], self._method_call, self._get_property, None)
        # Own the real name on the private bus, so callers address it exactly as
        # Depot addresses the agent.
        self.connection.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "RequestName",
            GLib.Variant("(su)", (self.bus_name, 0)), GLib.VariantType("(u)"),
            Gio.DBusCallFlags.NONE, 5000, None)
        # No polkit on this bus: authorization is tested in test_daemon.
        self.calls = []

    def _variant(self, key, value):
        signature = self.types[key]
        if signature in ("t", "x"):
            value = int(value)
        elif signature == "d":
            value = float(value)
        elif signature == "as":
            value = [str(item) for item in value]
        elif signature == "b":
            value = bool(value)
        return GLib.Variant(signature, value)

    def _get_property(self, _connection, _sender, _path, _interface, name):
        key = self.snake_name(name)
        return self._variant(key, getattr(self.engine.status(), key)) if key in self.types else None

    def _method_call(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        args = parameters.unpack()
        self.calls.append((method, args))
        try:
            if method == "SetAutomaticDownload":
                self.engine.set_automatic_download(bool(args[0]))
            elif method == "IgnoreVersion":
                self.engine.ignore_version(args[0])
            elif method == "ClearIgnoredVersion":
                self.engine.clear_ignored_version()
            elif method == "Check":
                self.engine.check()
            invocation.return_value(None)
        except Exception as error:
            invocation.return_dbus_error("org.projectluma.Update1.Error.Failed", str(error))

    # ── Helpers ──────────────────────────────────────────────────────────

    def _pump(self, destination, path, interface, method, parameters, reply_type):
        """Call and wait by running the main context.

        The service is this process, on this thread: a synchronous call would
        wait for a reply the handler is never given a chance to send."""
        outcome = {}

        def done(connection, result):
            try:
                outcome["value"] = connection.call_finish(result)
            except Exception as error:  # re-raised below, on the test's thread
                outcome["error"] = error
        self.connection.call(destination, path, interface, method, parameters, reply_type,
                             Gio.DBusCallFlags.NONE, 5000, None, done)
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 10
        while not outcome and time.monotonic() < deadline:
            context.iteration(True)
        if "error" in outcome:
            raise outcome["error"]
        self.assertIn("value", outcome, f"{method} never answered")
        return outcome["value"]

    def properties(self):
        reply = self._pump(self.bus_name, self.path, "org.freedesktop.DBus.Properties", "GetAll",
                           GLib.Variant("(s)", (self.interface,)), GLib.VariantType("(a{sv})"))
        return reply.unpack()[0]

    def read(self):
        return su.from_values(self.properties(), "dbus")

    def call(self, method, args=None, signature=None):
        self._pump(self.bus_name, self.path, self.interface, method,
                   GLib.Variant(signature, args) if signature else None, None)

    # ── The contract ─────────────────────────────────────────────────────

    def test_every_published_property_is_readable_and_typed_as_declared(self):
        for name, signature in self.types.items():
            reply = self._pump(self.bus_name, self.path, "org.freedesktop.DBus.Properties", "Get",
                               GLib.Variant("(ss)", (self.interface, self.dbus_name(name))),
                               GLib.VariantType("(v)"))
            value = reply.get_child_value(0).get_variant()
            self.assertEqual(value.get_type_string(), signature, name)

    def test_depot_reads_every_field_it_draws(self):
        self.call("Check")
        state = self.read()
        self.assertTrue(state.service)
        self.assertEqual(state.booted_version, "1.0.0")
        self.assertEqual(state.available_version or state.staged_version, "1.0.1")
        self.assertTrue(state.automatic_download)
        self.assertTrue(state.signature_verified)
        self.assertTrue(state.graph_url.startswith("https://"))
        self.assertEqual(state.last_check_reason, "newest-eligible")
        self.assertEqual(state.channel, "stable")

    def test_turning_automatic_download_off_over_the_bus_is_read_back(self):
        self.call("SetAutomaticDownload", (False,), "(b)")
        self.assertFalse(self.read().automatic_download)
        self.call("SetAutomaticDownload", (True,), "(b)")
        self.assertTrue(self.read().automatic_download)

    def test_ignoring_a_version_over_the_bus_is_read_back_and_undone(self):
        self.call("Check")
        self.call("IgnoreVersion", ("1.0.1",), "(s)")
        state = self.read()
        self.assertEqual(state.ignored_version, "1.0.1")
        self.assertTrue(state.ignored)
        self.assertFalse(state.update_ready)
        self.call("ClearIgnoredVersion")
        self.assertEqual(self.read().ignored_version, "")

    def test_the_host_comes_from_the_image_not_from_depot(self):
        remotes = self.rig.paths.ostree_remotes_dir
        remotes.mkdir(parents=True, exist_ok=True)
        (remotes / "luma.conf").write_text('[remote "luma"]\nurl=https://os.elsewhere.example/repo\n')
        self.engine.refresh()
        self.assertEqual(self.read().host, "os.elsewhere.example")

    def test_depot_drops_nothing_the_agent_publishes(self):
        """Every D-Bus property Depot could read has a home in its model."""
        model = {field for field in su.SystemUpdate.__dataclass_fields__}
        published = {self.snake_name(self.dbus_name(name)) for name in self.types}
        missing = published - model - {"rolled_back_at", "staged_commit", "available_commit", "last_error_class"}
        self.assertEqual(missing, set())
        self.assertLessEqual(set(su.PROPERTIES), {self.dbus_name(name) for name in self.types})


if __name__ == "__main__":
    unittest.main()
