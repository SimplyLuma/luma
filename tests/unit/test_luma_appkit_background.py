# SPDX-License-Identifier: Apache-2.0
"""luma_appkit.background on a private session bus: wakes, values, bindings, requests.

Run under dbus-run-session. Nothing here imports GTK, and the test asserts it.
"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/luma-platform/appkit"))
sys.path.insert(0, str(ROOT / "src/luma-platform/broker"))

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from luma_appkit import background  # noqa: E402

SERVICE_XML = """
<node><interface name="org.projectluma.Background1">
  <method name="RequestBackground">
    <arg name="parent_window" type="s" direction="in"/>
    <arg name="options" type="a{sv}" direction="in"/>
    <arg name="response" type="u" direction="out"/>
    <arg name="results" type="a{sv}" direction="out"/>
  </method>
  <method name="Schedule">
    <arg name="name" type="s" direction="in"/>
    <arg name="options" type="a{sv}" direction="in"/>
  </method>
</interface></node>
"""


def new_connection() -> Gio.DBusConnection:
    return Gio.DBusConnection.new_for_address_sync(
        os.environ["DBUS_SESSION_BUS_ADDRESS"],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)


def spin(milliseconds: int = 300, until=None) -> None:
    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + milliseconds * 1000
    while GLib.get_monotonic_time() < deadline:
        context.iteration(False)
        if until is not None and until():
            return


class Counter(background.Agent):
    app_id = "org.example.Counter"
    agent_id = "org.example.Counter.Agent"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.wakes = []
        self.began = False

    def on_start(self):
        self.began = True
        self.publish("count", 0)

    def on_wake(self, wake):
        self.wakes.append(wake)
        self.publish("count", self.values["count"] + 1)


@unittest.skipUnless(os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "needs a session bus")
class AgentOnTheBus(unittest.TestCase):
    def setUp(self):
        self.service = new_connection()
        self.other = new_connection()
        self.agent_connection = new_connection()
        self.agent = Counter(connection=self.agent_connection)
        self.agent.start()
        spin(1000, until=lambda: self.agent.started)
        self.assertTrue(self.agent.started)

    def tearDown(self):
        self.agent.stop()
        for connection in (self.service, self.other, self.agent_connection):
            connection.close_sync(None)

    def call_wake(self, connection, reason, details=None):
        """Asynchronously, so this process can serve the call it makes."""
        outcome = {}

        def done(bus, result):
            try:
                outcome["value"] = bus.call_finish(result)
            except GLib.Error as error:
                outcome["error"] = error

        connection.call(self.agent.agent_id, background.AGENT_PATH, background.AGENT_INTERFACE,
                        "Wake", GLib.Variant("(sa{sv})", (reason, details or {})), None,
                        Gio.DBusCallFlags.NONE, 5000, None, done)
        spin(6000, until=lambda: outcome)
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    def test_no_gtk_in_an_agent(self):
        # In a fresh interpreter: a combined run (every kit test module in one process) has already
        # imported GTK for the widget tests, which says nothing about the agent.
        import subprocess
        probe = ("import sys; sys.path[:0] = [%r, %r]; from luma_appkit import background; "
                 "print('gi.repository.Gtk' in sys.modules)") % (str(ROOT / "src/luma-platform/appkit"),
                                                                 str(ROOT / "src/luma-platform/broker"))
        found = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60)
        self.assertEqual(found.stdout.strip().splitlines()[-1:], ["False"], found.stderr[-2000:])

    def test_wakes_come_only_from_the_service(self):
        with self.assertRaises(GLib.Error):
            self.call_wake(self.other, "network")
        owner = Gio.bus_own_name_on_connection(self.service, background.SERVICE_NAME,
                                               Gio.BusNameOwnerFlags.NONE, None, None)
        spin(300)
        self.call_wake(self.service, "schedule", {"schedule": GLib.Variant("s", "refresh"),
                                                  "missed": GLib.Variant("b", True)})
        with self.assertRaises(GLib.Error):
            self.call_wake(self.service, "reboot")
        spin(300, until=lambda: self.agent.wakes)
        self.assertEqual(self.agent.wakes, [background.Wake("schedule", "refresh", True)])
        Gio.bus_unown_name(owner)

    def test_values_and_from_agent_binding(self):
        binding = background.AgentValue(self.agent.agent_id, "count", connection=self.other)
        seen = []
        binding.connect(lambda value: seen.append(value.value))
        spin(500, until=lambda: seen)
        self.assertEqual(seen[-1], 0)
        self.agent.publish("count", 41)
        self.agent.publish("count", 42)
        spin(500, until=lambda: seen and seen[-1] == 42)
        self.assertEqual(seen[-1], 42)
        # Only one PropertiesChanged per main-loop turn.
        self.assertNotIn(41, seen)
        # A stopped agent means nothing to show.
        self.agent.stop()
        spin(500, until=lambda: not binding.present)
        self.assertFalse(binding.present)
        binding.close()

    def test_publish_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            self.agent.publish("Bad Name", 1)
        with self.assertRaises(TypeError):
            self.agent.publish("thing", object())
        self.agent.publish("nested", {"a": 1, "b": ["x"]})
        self.assertEqual(self.agent.values["nested"], {"a": 1, "b": ["x"]})

    def test_live_extension_payload_follows_values(self):
        extension = background.LiveExtensionBinding(
            self.agent, extension_id="org.example.Counter.Count", category="generic",
            title="Counter", subtitle=lambda v: f"{v['count']} so far",
            progress=background.from_agent(self.agent.agent_id, "fraction"),
            expires_in=timedelta(minutes=5))
        self.assertIsNone(extension.payload())  # "fraction" not published yet
        self.agent.publish("fraction", 0.25)
        payload = extension.payload()
        self.assertEqual((payload["title"], payload["subtitle"], payload["progress"]),
                         ("Counter", "0 so far", 0.25))
        self.assertEqual(payload["app_id"], "org.example.Counter")
        self.agent.unpublish("fraction")
        self.assertIsNone(extension.payload())
        extension.close()

    def test_request_background_native(self):
        requests = []

        def handle(_c, sender, _p, _i, method, parameters, invocation):
            requests.append(parameters.unpack())
            invocation.return_value(GLib.Variant("(ua{sv})", (0, {
                "background": GLib.Variant("b", True), "autostart": GLib.Variant("b", True)})))

        node = Gio.DBusNodeInfo.new_for_xml(SERVICE_XML)
        registration = self.service.register_object(background.SERVICE_PATH, node.interfaces[0], handle, None, None)
        Gio.bus_own_name_on_connection(self.service, background.SERVICE_NAME, Gio.BusNameOwnerFlags.NONE, None, None)
        spin(300)
        results = []
        background.request_background("org.example.Counter", reason="Count things",
                                      callback=results.append, connection=self.other)
        spin(1000, until=lambda: results)
        self.assertEqual(results, [background.BackgroundResult(True, True, 0)])
        self.assertEqual(requests[0][1]["reason"], "Count things")
        self.service.unregister_object(registration)

    def test_run_agent_switch(self):
        self.assertIsNone(background.run_agent(Counter, ["app"]))
        with self.assertRaises(ValueError):
            class Bad(background.Agent):
                app_id = "org.example.Counter"
                agent_id = "org.other.Agent"
            Bad()


if __name__ == "__main__":
    unittest.main()
