#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""ADR-033 background agents without a session: contract files, decisions, no GTK.

Run inside a D-Bus session (dbus-run-session) for the Notifier and publisher
cases; everything else needs nothing.
"""

from __future__ import annotations

import configparser
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data" / "agents"
AGENT_APPS = ("Messages", "Phone", "Calendar")


def desktop(path: Path) -> dict:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(path)
    return dict(parser[parser.sections()[0]])


def unit(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False, empty_lines_in_values=False)
    parser.optionxform = str
    parser.read_string(path.read_text())
    return parser


class ContractFileTests(unittest.TestCase):
    def test_every_agent_declares_the_same_contract_in_each_file(self):
        from prairie_apps.background_agent import CATEGORIES, WAKE_EVENTS
        for app in AGENT_APPS + ("Clock",):
            app_id = f"org.projectluma.{app}"
            with self.subTest(app=app):
                manifest = tomllib.loads((DATA / f"{app_id}.toml").read_text())
                background = manifest["background"]
                self.assertEqual(manifest["application"]["id"], app_id)
                self.assertEqual(background["agent"], f"{app_id}.Agent")
                self.assertIn(background["category"], CATEGORIES)
                self.assertTrue(background["wake"] and set(background["wake"]) <= set(WAKE_EVENTS))
                if app in ("Messages", "Phone"):
                    self.assertIn("badge", background["publishes"], "the dock reads only agents that declare badge")
                # Settings lists an app under Notifications, with its per-app
                # switches including Badge App Icon, only when its desktop file
                # says it uses notifications. Every agent here notifies.
                entry = desktop(HERE.parent / "data" / f"{app_id}.desktop")
                self.assertEqual(entry.get("X-GNOME-UsesNotifications"), "true",
                                 f"{app} notifies, so its desktop file must declare it")
                for name in background["publishes"]:
                    self.assertRegex(name, r"^(live-extension:org\.projectluma\.[A-Za-z.]+|[a-z][a-z0-9._-]{0,63})$")
                desktop_exec = desktop(HERE.parent / "data" / f"{app_id}.desktop")["Exec"].split()[0]
                self.assertEqual(background["exec"].split()[0], desktop_exec,
                                 "the agent's program is the app's own (luma-background requires it)")
                self.assertEqual(background["exec"].split()[1:], ["--agent"])
                if app == "Clock":
                    continue
                entry = desktop(DATA / f"{app_id}.Agent.desktop")
                self.assertEqual(entry["Exec"], background["exec"] + " --autostart")
                self.assertEqual(entry["X-Luma-Background-Agent"], f"{app_id}.Agent")
                self.assertEqual(entry["NoDisplay"], "true")
                # systemd's autostart generator skips entries that set a GNOME phase.
                self.assertNotIn("X-GNOME-Autostart-Phase", entry)

    def test_apps_ship_no_agent_units_or_activation_files(self):
        # luma-background generates units for allowed apps; a shipped unit or a
        # D-Bus activation file naming one would let an agent run against the
        # person's decision.
        self.assertEqual(sorted(path.name for path in DATA.glob("*.service")), [])
        self.assertEqual(sorted(path.name for path in DATA.glob("*.path")), [])

    def test_clock_portal_entry_keeps_the_spelling_every_release_understands(self):
        from prairie_apps.clock_alarms import autostart_command
        self.assertEqual(autostart_command(True), ["prairie-clock", "--gapplication-service"])

    def test_the_old_hidden_window_autostart_is_gone(self):
        self.assertFalse((HERE.parent / "data" / "org.projectluma.Messages-background.desktop").exists())

    def test_evolution_alarm_notify_is_held_off_while_the_calendar_agent_is_installed(self):
        text = (DATA / "50-luma-calendar-reminders.conf").read_text()
        self.assertIn("ConditionPathExists=!/usr/share/luma/background/org.projectluma.Calendar.toml", text)

    def test_sync_timer_counts_time_asleep(self):
        timer = unit(HERE.parent / "data" / "luma-connect-sync.timer")["Timer"]
        self.assertEqual(timer["Persistent"], "true")
        self.assertIn("OnCalendar", timer)
        self.assertNotIn("OnUnitActiveSec", timer)


class NoGtkTests(unittest.TestCase):
    def test_agents_load_no_gtk(self):
        for module in ("prairie_apps.messages_agent", "prairie_apps.phone_agent", "prairie_apps.calendar_agent"):
            with self.subTest(module=module):
                code = (f"import sys, {module}; bad=[m for m in sys.modules if m.startswith(('gi.repository.Gtk',"
                        "'gi.repository.Adw','gi.repository.Gdk'))]; print(bad); sys.exit(1 if bad else 0)")
                result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                                        env={**os.environ}, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class InfoTests(unittest.TestCase):
    def test_names_come_from_the_app_id(self):
        from prairie_apps.background_agent import AgentInfo
        info = AgentInfo("org.projectluma.Messages", "Messages", "communication", ("prairie-messages", "--agent"))
        self.assertEqual(info.agent_id, "org.projectluma.Messages.Agent")
        self.assertEqual(info.unit, "app-org.projectluma.Messages-agent.service")
        with self.assertRaises(ValueError):
            AgentInfo("not an id", "x", "communication", ())
        with self.assertRaises(ValueError):
            AgentInfo("org.example.App", "x", "gaming", ())
        with self.assertRaises(ValueError):
            AgentInfo("org.example.App", "x", "other", (), wake=("reboot",))

    def test_values_are_plain_data(self):
        from prairie_apps.background_agent import variant
        self.assertEqual(variant(3).get_type_string(), "x")
        self.assertEqual(variant(True).get_type_string(), "b")
        self.assertEqual(variant("idle").get_type_string(), "s")
        self.assertEqual(variant({"a": 1}).get_type_string(), "a{sv}")
        with self.assertRaises(TypeError):
            variant(object())


class CalendarWordsTests(unittest.TestCase):
    def test_reminder_times_read_like_a_person_wrote_them(self):
        from prairie_apps.calendar_agent import describe_time
        now = 1_800_000_000.0
        self.assertTrue(describe_time(int(now + 10), int(now + 1800), now, all_day=False).startswith("Now · "))
        self.assertTrue(describe_time(int(now + 600), int(now + 2400), now, all_day=False).startswith("In 10 minutes · "))
        self.assertTrue(describe_time(int(now + 60), 0, now, all_day=False).startswith("In 1 minute · "))
        self.assertTrue(describe_time(int(now - 1500), int(now + 300), now, all_day=False).startswith("Started 25 minutes ago"))
        self.assertTrue(describe_time(int(now - 7200), int(now - 3600), now, all_day=False).startswith("Ended at"))


class FakeNotifier:
    def __init__(self):
        self.shown, self.withdrawn = {}, []

    def notify(self, key, summary, body="", **options):
        self.shown[key] = (summary, body, options)
        return len(self.shown)

    def withdraw(self, key):
        self.withdrawn.append(key)
        self.shown.pop(key, None)

    def keys(self):
        return list(self.shown)

    def close(self):
        pass


class FakeRing:
    def __init__(self):
        self.ringing = False
        self.starts = 0

    def start(self):
        self.ringing = True
        self.starts += 1

    def stop(self):
        self.ringing = False


class FakeCalls:
    def __init__(self):
        self.rows = []
        self.accepted, self.declined = [], []
        self.listener = None

    def start(self, listener):
        self.listener = listener

    def calls(self):
        return tuple(self.rows)

    def publish(self, *rows):
        self.rows = list(rows)
        self.listener({"calls": [], "dial_token": None}, True)

    def accept(self, call_id):
        self.accepted.append(call_id)

    def decline(self, call_id):
        self.declined.append(call_id)

    def invalidate(self):
        pass

    def close(self):
        pass


class PhoneAgentTests(unittest.TestCase):
    def setUp(self):
        from prairie_apps.phone_agent import PhoneAgent
        from prairie_apps.phone_backend import CallPhase, NativeCall
        self.Phase, self.Call = CallPhase, NativeCall
        self.home = tempfile.TemporaryDirectory()
        self.selection = Path(self.home.name) / "calls-phone.json"
        self.selection.write_text('{"version": 1, "phone": {"label": "Pixel"}}')
        self.calls = FakeCalls()
        self.ring = FakeRing()
        self.agent = PhoneAgent(provider_factory=lambda _dispatch: self.calls, ring=self.ring,
                                selection=self.selection, contacts=lambda: ())
        self.agent.notifier = FakeNotifier()
        self.agent._reload_provider()

    def tearDown(self):
        self.home.cleanup()

    def call(self, phase, call_id="c1"):
        return self.Call(call_id, "+15550101011", "incoming", phase)

    def test_an_incoming_call_rings_and_answer_opens_phone(self):
        self.calls.publish(self.call(self.Phase.INCOMING))
        self.assertTrue(self.ring.ringing)
        summary, body, options = self.agent.notifier.shown["incoming:c1"]
        from prairie_apps.phone_backend import format_phone_number
        self.assertEqual(summary, format_phone_number("+15550101011"))
        self.assertEqual(body, "Incoming call on Pixel")
        self.assertEqual(options["category"], "call.incoming")
        self.assertEqual({action for action, _label in options["actions"]}, {"default", "answer", "decline"})
        self.calls.publish(self.call(self.Phase.INCOMING))
        self.assertEqual(self.ring.starts, 1, "a second snapshot of the same call does not ring again")
        self.calls.publish(self.call(self.Phase.ACTIVE))
        self.assertFalse(self.ring.ringing)
        self.assertIn("incoming:c1", self.agent.notifier.withdrawn)
        self.calls.publish()
        self.assertNotIn("missed:c1", self.agent.notifier.shown)

    def test_a_call_that_stops_ringing_unanswered_is_missed(self):
        self.calls.publish(self.call(self.Phase.INCOMING))
        self.calls.publish()
        self.assertFalse(self.ring.ringing)
        summary, _body, options = self.agent.notifier.shown["missed:c1"]
        self.assertEqual(summary, "Missed call")
        self.assertEqual({action for action, _label in options["actions"]}, {"default", "message", "call-back"})
        self.assertEqual(self.agent.missed, 1)

    def test_missed_calls_badge_the_dock_until_seen(self):
        published = {}
        self.agent.agent = type("Publisher", (), {"publish": lambda _self, name, value: published.__setitem__(name, value)})()
        self.calls.publish(self.call(self.Phase.INCOMING))
        self.calls.publish()
        self.assertEqual(published["missed-calls"], 1)
        self.assertEqual(published["badge"].get_type_string(), "u")
        self.assertEqual(published["badge"].get_uint32(), 1)
        _summary, _body, options = self.agent.notifier.shown["missed:c1"]
        from unittest import mock
        with mock.patch("prairie_apps.phone_agent.launch_uri") as launch:
            options["on_action"]("message")
        launch.assert_called_once_with("sms:+15550101011")
        self.assertEqual(published["badge"].get_uint32(), 0, "acting on the missed call clears the badge")

    def test_decline_is_not_a_missed_call(self):
        self.calls.publish(self.call(self.Phase.INCOMING))
        self.agent._action("c1", "decline")
        deadline = GLib.get_monotonic_time() + 2_000_000
        while not self.calls.declined and GLib.get_monotonic_time() < deadline:
            GLib.usleep(10_000)
        self.assertEqual(self.calls.declined, ["c1"])
        self.calls.publish()
        self.assertNotIn("missed:c1", self.agent.notifier.shown)

    def test_with_the_phone_window_open_the_agent_stays_quiet(self):
        self.agent._window_changed(True)
        self.calls.publish(self.call(self.Phase.INCOMING))
        self.assertFalse(self.ring.ringing)
        self.assertNotIn("incoming:c1", self.agent.notifier.shown)

    def test_no_phone_chosen_means_no_provider(self):
        self.selection.unlink()
        self.agent._reload_provider()
        self.assertIsNone(self.agent.provider)


class MessagesAgentTests(unittest.TestCase):
    def setUp(self):
        from prairie_apps.messages_accounts import Account, Accounts
        from prairie_apps.messages_agent import MessagesAgent
        self.home = tempfile.TemporaryDirectory()
        self.accounts = Accounts(Path(self.home.name) / "accounts")
        self.agent = MessagesAgent(accounts=self.accounts, secrets_store=object(), helper_dir=Path(self.home.name),
                                   native_store=Path(self.home.name) / "messages.db")
        self.agent.notifier = FakeNotifier()

        class Provider:
            reaction_emoji = ("👍",)
            account = Account("gmessages-0123456789abcdef", "gmessages")

            def close(self):
                pass

            def conversation_seen(self, address):
                self.seen.append(address)
        self.provider = Provider()
        self.provider.seen = []
        self.provider.store_path = Path(self.home.name) / 'account.db'
        self.agent.services["gmessages-0123456789abcdef"] = self.provider

    def tearDown(self):
        self.agent.stop()
        self.home.cleanup()

    def test_unread_count_reads_existing_native_and_account_mailboxes(self):
        from prairie_apps.messages_backend import MessageStore
        from prairie_apps.messages_accounts import AccountStore
        for cls, path in ((MessageStore, self.agent._native_store_path()),
                          (AccountStore, self.provider.store_path)):
            store = cls(path)
            try: store.add('+12025550123', 'unread', direction='incoming')
            finally: store.close()
            before = path.stat().st_mtime_ns
            self.assertEqual(self.agent._unread(path), (1, {'+12025550123': 1}))
            self.assertEqual(path.stat().st_mtime_ns, before)
        self.agent._count_unread()

    def test_an_arrival_is_announced_with_reply_reaction_and_mark_read(self):
        self.agent._arrived("gmessages-0123456789abcdef", "chat.one", "Sam", "Dinner?")
        key = "account:gmessages-0123456789abcdef|chat.one"
        summary, body, options = self.agent.notifier.shown[key]
        self.assertEqual((summary, body), ("Sam", "Dinner?"))
        self.assertEqual([action for action, _ in options["actions"]], ["default", "react", "reply", "mark-read"])
        self.assertEqual(options["public_summary"], "Messages")
        self.assertNotIn("Sam", options["public_body"])
        token = options["hints"]["x-luma-inline-reply-token"].get_string()
        self.agent.notifier.key_for = lambda identifier: key if identifier == 7 else None
        self.assertEqual(self.agent._reply_target(7, token), ("gmessages-0123456789abcdef", "chat.one"))
        self.assertIsNone(self.agent._reply_target(7, "0" * 32))
        self.assertIsNone(self.agent._reply_target(8, token))

    def test_a_conversation_on_screen_is_not_announced_and_is_marked_seen(self):
        self.agent.viewing[":1.42"] = ("gmessages-0123456789abcdef", "chat.one")
        self.agent._arrived("gmessages-0123456789abcdef", "chat.one", "Sam", "Here now")
        self.assertEqual(self.agent.notifier.shown, {})
        self.assertEqual(self.provider.seen, ["chat.one"])


class ClockAgentTests(unittest.TestCase):
    def test_alarm_buttons_reach_the_service_actions(self):
        from prairie_apps.clock_agent import AgentApplication, AgentNotifications
        from prairie_apps.clock_alarms import AlarmNotification

        class Agent:
            connection = None

            def quit(self):
                pass
        application = AgentApplication(Agent())
        received = []
        for name in ("clock-stop", "clock-snooze"):
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", lambda _a, target, name=name: received.append((name, target.get_string())))
            application.add_action(action)
        notifier = FakeNotifier()
        AgentNotifications(notifier, application, lambda: None).post("alarm-a1", AlarmNotification(
            "Wake up", "07:00", "urgent", "alarm.ringing", (("Snooze 9 min", "clock-snooze", "a1"), ("Stop", "clock-stop", "a1")),
            ("clock-open", "alarm")))
        summary, body, options = notifier.shown["alarm-a1"]
        self.assertEqual((summary, body), ("Wake up", "07:00"))
        self.assertEqual([label for _key, label in options["actions"]], ["Open", "Snooze 9 min", "Stop"])
        self.assertEqual((options["urgency"], options["resident"], options["public_body"]), (2, True, "Alarm"))
        stop_key = next(key for key, label in options["actions"] if label == "Stop")
        options["on_action"](stop_key)
        self.assertEqual(received, [("clock-stop", "a1")])

    def test_a_clock_without_a_bus_never_defers(self):
        from prairie_apps.clock_alarms import AlarmService
        from prairie_apps.clock_backend import ClockStore

        class Application:
            def add_action(self, _action):
                pass

            def get_dbus_connection(self):
                return None
        with tempfile.TemporaryDirectory() as home:
            service = AlarmService(Application(), store=ClockStore(Path(home) / "state.json"), sandboxed=False)
            self.assertFalse(service._agent_elsewhere())


class PublisherTests(unittest.TestCase):
    """Needs a session bus. Runs the built-in implementation and, when installed, the kit's."""

    def setUp(self):
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            self.skipTest("no session bus")

    def exercise(self, kit):
        from prairie_apps.background_agent import AgentInfo, AgentPublisher
        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        second = Gio.DBusConnection.new_for_address_sync(
            os.environ["DBUS_SESSION_BUS_ADDRESS"],
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        info = AgentInfo("org.projectluma.UnitTest", "Unit", "other", ("x",))
        events, lost = [], []
        first = AgentPublisher(info, wake=events.append, kit=kit)
        other = AgentPublisher(info, lost=lost.append, kit=kit)
        context = GLib.MainContext.default()
        first.own(connection)
        deadline = GLib.get_monotonic_time() + 5_000_000
        while not first.owned and GLib.get_monotonic_time() < deadline:
            context.iteration(False)
        self.assertTrue(first.owned)
        if not first.managed:
            self.assertEqual(events, ["login"], "unmanaged, the agent gives itself its login wake")
        other.own(second)
        while not lost and GLib.get_monotonic_time() < deadline:
            context.iteration(False)
        self.assertEqual(lost, [False], "a second agent does not queue for the name")
        first.publish("unread-count", 3)
        with self.assertRaises(ValueError):
            first.publish("UnreadCount", 3)
        import threading
        result = {}

        def read(method, *args):
            result[method] = subprocess.run(
                ["gdbus", "call", "--session", "--dest", info.agent_id, "--object-path",
                 "/org/projectluma/BackgroundAgent1", "--method", method, *args],
                capture_output=True, text=True, timeout=20)
        for method, args in (("org.freedesktop.DBus.Properties.GetAll", ("org.projectluma.BackgroundAgent1",)),
                             ("org.projectluma.BackgroundAgent1.Wake", ("resume", "{}"))):
            reader = threading.Thread(target=read, args=(method, *args))
            reader.start()
            while reader.is_alive():
                context.iteration(False)
                GLib.usleep(5_000)
        output = result["org.freedesktop.DBus.Properties.GetAll"].stdout
        self.assertIn("'Values': <{'unread-count': <int64 3>}>", output)
        self.assertIn("'AppId': <'org.projectluma.UnitTest'>", output)
        refused = result["org.projectluma.BackgroundAgent1.Wake"]
        self.assertNotEqual(refused.returncode, 0, "only luma-background may wake an agent")
        self.assertIn("NotAuthorized", refused.stderr)
        first.release()
        second.close_sync(None)

    def test_built_in(self):
        self.exercise(False)

    def test_kit(self):
        from prairie_apps.background_agent import load_kit
        kit = load_kit()
        if kit is None:
            self.skipTest("luma_appkit.background is not installed")
        self.exercise(kit)


if __name__ == "__main__":
    unittest.main(verbosity=2)
