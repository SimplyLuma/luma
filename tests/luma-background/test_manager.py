# SPDX-License-Identifier: MPL-2.0
"""Policy, wake scheduling, Power Saver and crash handling, with fake ports."""

import unittest
from pathlib import Path
from unittest import mock

from luma_background import journal, units
from luma_background.cgroup import CpuSampler, Usage
from luma_background.manager import Manager, NotFound, Refused, UnitStatus
from luma_background.policy import PolicyStore, load_defaults
from luma_background.registry import Registry
from luma_background.state import StateStore
from helpers import Tree

journal.send = lambda *args, **kwargs: None


class FakeSystemd:
    def __init__(self):
        self.files, self.links = {}, {}
        self.calls = []
        self.states = {}
        self.frozen = set()
        self.limits = {}
        self.pid_units = {}

    def sync_units(self, files, links, owned=()):
        changed = files != self.files or links != self.links
        self.files, self.links = dict(files), dict(links)
        return changed

    def reload(self, done):
        self.calls.append(("reload",))
        done(True)

    def start(self, unit, done):
        self.calls.append(("start", unit))
        if unit.endswith("-agent.service"):
            if unit not in self.files:
                done(False, "unit not found")
                return
            self.states[unit] = UnitStatus(active_state="active", main_pid=4242)
        done(True, "")

    def stop(self, unit, done):
        self.calls.append(("stop", unit))
        if unit in self.frozen:
            done(False, "frozen")
            return
        self.states[unit] = UnitStatus(active_state="inactive", result="success")
        done(True, "")

    def reset_failed(self, unit):
        self.calls.append(("reset-failed", unit))

    def freeze(self, unit, done):
        self.calls.append(("freeze", unit))
        self.frozen.add(unit)
        done(True, "")

    def thaw(self, unit, done):
        self.calls.append(("thaw", unit))
        self.frozen.discard(unit)
        done(True, "")

    def status(self, unit):
        return self.states.get(unit, UnitStatus())

    def usage(self, status):
        return Usage(memory=10_000_000, memory_peak=12_000_000, cpu_usec=5_000)

    def process_unit(self, pid):
        return self.pid_units.get(pid, "")

    def set_limits(self, unit, high, maximum):
        self.limits[unit] = (high, maximum)


class FakeBus:
    def __init__(self):
        self.wakes = []

    def deliver_wake(self, agent, reason, details, done):
        self.wakes.append((agent, reason, dict(details)))
        done(True)


class FakePermissions:
    def __init__(self):
        self.values = {}

    def set(self, app, allowed):
        self.values[app] = "yes" if allowed else "no"


class FakePrompt:
    def __init__(self):
        self.asked = []
        self.dismissed = []

    def ask(self, record, reason, answer):
        self.asked.append((record.app_id, reason, answer))

    def dismiss(self, app):
        self.dismissed.append(app)


DECLARED = """
[background]
agent = "{app}.Agent"
exec = "{program} --agent"
category = "{category}"
wake = {wake}
"""


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)
        for patcher in (
            mock.patch.object(units, "NATIVE_SEARCH_PATH", (str(self.tree.bin),)),
            mock.patch.object(units, "_executable", lambda path, require_system: path.exists()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.now = 1_800_000_000.0
        self.defaults = self.tree.root / "defaults.toml"
        self.defaults.write_text('[apps]\n"org.projectluma.Messages" = "on"\n"org.projectluma.Weather" = "off"\n')

    def add_native(self, app, category="communication", wake='["login", "network", "resume", "schedule"]',
                   user=False):
        program = app.rsplit(".", 1)[-1].lower()
        path = self.tree.program(program)
        if not user:
            self.tree.system_paths.add(path)
        self.tree.desktop(app, program, user=user)
        self.tree.declaration(app, DECLARED.format(app=app, program=program, category=category, wake=wake), user=user)

    def make(self):
        self.systemd, self.bus = FakeSystemd(), FakeBus()
        self.permissions, self.prompt = FakePermissions(), FakePrompt()
        self.changed = []
        policy = PolicyStore(self.tree.root / "policy.json", load_defaults((self.defaults,)))
        state = StateStore(self.tree.root / "state.json")
        self.manager = Manager(Registry(self.tree.flatpak_layout(), is_system_path=self.tree.is_system),
                               policy, state, self.systemd, self.bus, self.permissions, self.prompt,
                               clock=lambda: self.now, on_changed=self.changed.append)
        return self.manager

    # -- Policy --

    def test_essential_runs_at_login_and_others_ask(self):
        self.add_native("org.projectluma.Messages")
        self.add_native("org.projectluma.Weather", category="widget-data")
        self.add_native("com.example.Usage", category="widget-data", user=True)
        manager = self.make()
        manager.refresh(login=True)
        self.assertIn("app-org.projectluma.Messages-agent.service", self.systemd.files)
        self.assertNotIn("app-org.projectluma.Weather-agent.service", self.systemd.files)
        self.assertNotIn("app-com.example.Usage-agent.service", self.systemd.files)
        self.assertIn(("start", "app-org.projectluma.Messages-agent.service"), self.systemd.calls)
        self.assertEqual(self.bus.wakes, [("org.projectluma.Messages.Agent", "login", {})])
        messages = manager.describe("org.projectluma.Messages")
        self.assertTrue(messages["essential"])
        self.assertEqual(messages["state"], "running")
        self.assertEqual(messages["explanation"], "Get messages and calls when Messages is closed")
        self.assertEqual(manager.describe("org.projectluma.Weather")["default"], "off")
        self.assertEqual(manager.describe("com.example.Usage")["default"], "ask")
        self.assertEqual(manager.describe("com.example.Usage")["state"], "off")

    def test_shipped_defaults_turn_lumas_communication_apps_on(self):
        here = Path(__file__).resolve().parent
        shipped = next(path for path in (here.parent / "data/defaults.toml",
                                         here.parents[1] / "src/luma-background/data/defaults.toml") if path.exists())
        defaults = load_defaults((shipped,))
        for app in ("Messages", "Phone", "Calendar", "Clock", "Connect", "Charlie"):
            self.assertEqual(defaults.get(f"org.projectluma.{app}"), "on", app)
        self.assertEqual(defaults.get("org.projectluma.Weather"), "off")

    def fallback_entry(self):
        return self.tree.autostart("org.projectluma.Messages.Agent", [
            "Name=Messages", "Exec=messages --agent --autostart",
            "X-Luma-Background-Agent=org.projectluma.Messages.Agent"])

    def test_the_apps_own_autostart_stays_masked_while_its_agent_is_managed(self):
        self.add_native("org.projectluma.Messages")
        self.fallback_entry()
        manager = self.make()
        manager.refresh(login=True)
        masked = {"app-org.projectluma.Messages.Agent@autostart.service": "/dev/null"}
        self.assertEqual(self.systemd.links, masked)
        # Turned off, the app must not come back through its autostart entry either.
        manager.set_allowed("org.projectluma.Messages", False, "settings")
        self.assertEqual(self.systemd.links, masked)
        self.assertNotIn("app-org.projectluma.Messages-agent.service", self.systemd.files)
        # Without its declaration (an older app, or the agent removed) the entry runs again.
        (self.tree.system / "luma/background/org.projectluma.Messages.toml").unlink()
        manager.refresh()
        self.assertEqual(self.systemd.links, {})

    def test_prepare_loads_units_and_masks_before_anything_starts(self):
        self.add_native("org.projectluma.Messages")
        self.fallback_entry()
        manager = self.make()
        seen = []
        manager.prepare(lambda: seen.append((list(self.systemd.calls), dict(self.systemd.links))))
        self.assertEqual(seen, [([("reload",)], {"app-org.projectluma.Messages.Agent@autostart.service": "/dev/null"})])
        self.assertIn("app-org.projectluma.Messages-agent.service", self.systemd.files)
        self.assertEqual(self.bus.wakes, [])
        manager.refresh(login=True)
        self.assertEqual(self.systemd.calls.count(("reload",)), 1, "nothing changed since prepare")
        self.assertEqual(self.bus.wakes, [("org.projectluma.Messages.Agent", "login", {})])

    def test_user_installs_cannot_claim_essential(self):
        self.add_native("org.projectluma.Messages", user=True)
        manager = self.make()
        manager.refresh(login=True)
        self.assertEqual(manager.describe("org.projectluma.Messages")["default"], "ask")
        self.assertEqual(self.bus.wakes, [])

    def test_turning_off_stops_and_removes_unit_and_is_remembered(self):
        self.add_native("org.projectluma.Messages")
        manager = self.make()
        manager.refresh(login=True)
        manager.set_allowed("org.projectluma.Messages", False, "settings")
        self.assertIn(("stop", "app-org.projectluma.Messages-agent.service"), self.systemd.calls)
        self.assertNotIn("app-org.projectluma.Messages-agent.service", self.systemd.files)
        described = manager.describe("org.projectluma.Messages")
        self.assertEqual((described["state"], described["decision"]), ("off", "deny"))
        self.assertEqual(described["consequence"], "You won’t get messages or calls while Messages is closed")
        again = self.make()
        again.refresh(login=True)
        self.assertEqual(self.bus.wakes, [])

    def test_request_prompts_once_and_answer_applies(self):
        self.add_native("com.example.Usage", category="widget-data", wake='["schedule"]', user=True)
        manager = self.make()
        manager.refresh(login=True)
        replies = []
        manager.request_background("com.example.Usage", reason="Show usage", autostart=True,
                                   reply=lambda response, results: replies.append((response, results)))
        manager.request_background("com.example.Usage", reason="again", autostart=True,
                                   reply=lambda response, results: replies.append((response, results)))
        self.assertEqual(len(self.prompt.asked), 1)
        self.prompt.asked[0][2]("allow")
        self.assertEqual(replies, [(0, {"background": True, "autostart": True})] * 2)
        self.assertIn("app-com.example.Usage-agent.service", self.systemd.files)
        self.assertIn(("com.example.Usage.Agent", "request", {}), self.bus.wakes)
        replies.clear()
        manager.request_background("com.example.Usage", reason="", autostart=False,
                                   reply=lambda response, results: replies.append(response))
        self.assertEqual(replies, [0])
        self.assertEqual(len(self.prompt.asked), 1)

    def test_dismissed_prompt_is_never_repeated(self):
        self.add_native("com.example.Usage", category="widget-data", user=True)
        manager = self.make()
        manager.refresh(login=True)
        replies = []
        manager.request_background("com.example.Usage", reason="", autostart=True,
                                   reply=lambda response, results: replies.append(response))
        self.prompt.asked[0][2](None)
        manager.request_background("com.example.Usage", reason="", autostart=True,
                                   reply=lambda response, results: replies.append(response))
        self.assertEqual(replies, [2, 2])
        self.assertEqual(len(self.prompt.asked), 1)

    def test_denied_request_is_answered_without_prompt(self):
        self.add_native("com.example.Usage", category="widget-data", user=True)
        manager = self.make()
        manager.refresh(login=True)
        manager.policy.set_decision("com.example.Usage", False, "settings")
        replies = []
        manager.request_background("com.example.Usage", reason="", autostart=True,
                                   reply=lambda response, results: replies.append((response, results)))
        self.assertEqual(replies, [(1, {"background": False, "autostart": False})])
        self.assertEqual(self.prompt.asked, [])

    def test_unknown_app(self):
        manager = self.make()
        manager.refresh()
        with self.assertRaises(NotFound):
            manager.describe("com.example.Nothing")

    # -- Wakes --

    def test_wakes_respect_declaration(self):
        self.add_native("org.projectluma.Messages", wake='["login", "network"]')
        manager = self.make()
        manager.refresh(login=True)
        self.bus.wakes.clear()
        manager.wake_all("network")
        manager.wake_all("resume")
        self.assertEqual(self.bus.wakes, [("org.projectluma.Messages.Agent", "network", {})])

    def test_stopped_agent_is_started_by_its_next_wake(self):
        self.add_native("org.projectluma.Messages")
        manager = self.make()
        manager.refresh(login=True)
        manager.stop_now("org.projectluma.Messages")
        self.assertEqual(manager.describe("org.projectluma.Messages")["last-exit"], "stopped")
        self.assertEqual(manager.describe("org.projectluma.Messages")["state"], "idle")
        self.systemd.calls.clear()
        manager.wake_all("resume")
        self.assertIn(("start", "app-org.projectluma.Messages-agent.service"), self.systemd.calls)

    def test_schedules(self):
        self.add_native("org.projectluma.Clock", category="alarms", wake='["schedule"]')
        self.defaults.write_text('[apps]\n"org.projectluma.Clock" = "on"\n')
        manager = self.make()
        manager.refresh(login=True)
        at = int(self.now) + 3600
        manager.schedule("org.projectluma.Clock", "alarm-1", at=at)
        timer = self.systemd.files["app-org.projectluma.Clock-agent-alarm-1.timer"]
        self.assertIn("AccuracySec=1s", timer)
        self.assertIn(("start", "app-org.projectluma.Clock-agent-alarm-1.timer"), self.systemd.calls)
        self.now = at + 5
        manager.scheduled_wake("org.projectluma.Clock", "alarm-1")
        self.assertEqual(self.bus.wakes[-1], ("org.projectluma.Clock.Agent", "schedule",
                                              {"schedule": "alarm-1", "missed": False}))
        self.assertNotIn("app-org.projectluma.Clock-agent-alarm-1.timer", self.systemd.files)
        self.assertEqual(manager.describe("org.projectluma.Clock")["schedules"], [])
        with self.assertRaises(ValueError):
            manager.schedule("org.projectluma.Clock", "interval", every=900)
        with self.assertRaises(ValueError):
            manager.schedule("org.projectluma.Clock", "fast", every=60)

    def test_missed_schedules_are_delivered_at_login(self):
        self.add_native("org.projectluma.Clock", category="alarms", wake='["schedule"]')
        self.defaults.write_text('[apps]\n"org.projectluma.Clock" = "on"\n')
        manager = self.make()
        manager.refresh(login=True)
        manager.schedule("org.projectluma.Clock", "alarm-1", at=int(self.now) + 60)
        self.now += 7200
        later = self.make()
        later.refresh(login=True)
        self.assertEqual(self.bus.wakes, [("org.projectluma.Clock.Agent", "schedule",
                                           {"schedule": "alarm-1", "missed": True})])

    def test_undeclared_schedule_is_refused(self):
        self.add_native("org.projectluma.Messages", wake='["login"]')
        manager = self.make()
        manager.refresh(login=True)
        with self.assertRaises(Refused):
            manager.schedule("org.projectluma.Messages", "x", every=600)

    # -- Power Saver --

    def test_power_saver_pauses_only_deferrable_agents(self):
        self.add_native("org.projectluma.Messages")
        self.add_native("org.projectluma.Weather", category="widget-data", wake='["login", "network"]')
        manager = self.make()
        manager.refresh(login=True)
        manager.set_allowed("org.projectluma.Weather", True, "settings")
        manager.set_power_saver(True)
        self.assertIn(("freeze", "app-org.projectluma.Weather-agent.service"), self.systemd.calls)
        self.assertNotIn(("freeze", "app-org.projectluma.Messages-agent.service"), self.systemd.calls)
        self.assertEqual(manager.describe("org.projectluma.Weather")["state"], "paused")
        self.assertEqual(manager.describe("org.projectluma.Messages")["state"], "running")
        self.bus.wakes.clear()
        manager.wake_all("network")
        self.assertEqual(self.bus.wakes, [("org.projectluma.Messages.Agent", "network", {})])
        manager.set_power_saver(False)
        self.assertIn(("thaw", "app-org.projectluma.Weather-agent.service"), self.systemd.calls)
        self.assertIn(("org.projectluma.Weather.Agent", "network", {}), self.bus.wakes)

    def test_stopping_a_paused_agent_thaws_first(self):
        self.add_native("org.projectluma.Weather", category="widget-data")
        manager = self.make()
        manager.refresh(login=True)
        manager.set_allowed("org.projectluma.Weather", True, "settings")
        manager.set_power_saver(True)
        manager.stop_now("org.projectluma.Weather")
        calls = self.systemd.calls
        self.assertLess(calls.index(("thaw", "app-org.projectluma.Weather-agent.service")),
                        calls.index(("stop", "app-org.projectluma.Weather-agent.service")))

    # -- Crashes --

    def test_crash_and_failure_are_recorded(self):
        self.add_native("org.projectluma.Messages")
        manager = self.make()
        manager.refresh(login=True)
        unit = "app-org.projectluma.Messages-agent.service"
        manager.unit_changed(unit)
        self.systemd.states[unit] = UnitStatus(active_state="activating", restarts=1, result="exit-code")
        manager.unit_changed(unit)
        self.assertEqual(manager.describe("org.projectluma.Messages")["last-exit"], "crashed")
        self.systemd.states[unit] = UnitStatus(active_state="failed", restarts=8, result="oom-kill")
        manager.unit_changed(unit)
        described = manager.describe("org.projectluma.Messages")
        self.assertEqual((described["state"], described["last-exit"]), ("failed", "out-of-memory"))
        self.systemd.calls.clear()
        manager.wake_all("network")
        self.assertIn(("reset-failed", unit), self.systemd.calls)
        self.assertIn(("start", unit), self.systemd.calls)

    # -- Flatpak --

    def add_flatpak(self, app, autostart=False, declared=True, category="communication"):
        self.tree.desktop(app, f"/usr/bin/flatpak run --command=chat {app}", flatpak=True)
        if declared:
            self.tree.declaration(app, DECLARED.format(app=app, program="chat", category=category,
                                                       wake='["login"]'), flatpak=True)
        if autostart:
            (self.tree.home / ".config/autostart" / f"{app}.desktop").write_text(
                f"[Desktop Entry]\nType=Application\nX-XDP-Autostart={app}\nX-Flatpak={app}\n"
                f"Exec=flatpak run --command=chat {app} --agent\n")

    def test_flatpak_scope_gets_limits(self):
        self.add_flatpak("com.example.Chat", category="widget-data")
        manager = self.make()
        manager.refresh(login=True)
        manager.set_allowed("com.example.Chat", True, "settings")
        self.assertEqual(self.permissions.values["com.example.Chat"], "yes")
        self.systemd.pid_units[4242] = "app-flatpak-com.example.Chat-99.scope"
        manager.unit_changed("app-com.example.Chat-agent.service")
        self.assertEqual(self.systemd.limits["app-flatpak-com.example.Chat-99.scope"], (64 * 2**20, 128 * 2**20))
        manager.set_power_saver(True)
        self.assertIn(("freeze", "app-flatpak-com.example.Chat-99.scope"), self.systemd.calls)

    def test_portal_first_request_prompts_and_autostart_is_masked(self):
        self.add_flatpak("com.example.Chat", autostart=True, declared=False)
        manager = self.make()
        manager.refresh(login=True)
        self.assertEqual(self.systemd.links, {"app-com.example.Chat@autostart.service": "/dev/null"})
        self.assertNotIn("app-com.example.Chat-agent.service", self.systemd.files)
        manager.portal_permission_changed("com.example.Chat", "yes", "")
        self.assertEqual(len(self.prompt.asked), 1)
        self.prompt.asked[0][2]("allow")
        self.assertIn("app-com.example.Chat-agent.service", self.systemd.files)
        self.assertIn('"--command=chat"', self.systemd.files["app-com.example.Chat-agent.service"])
        self.assertIn(("start", "app-com.example.Chat-agent.service"), self.systemd.calls)
        # Autostart entries are ordinary programs: no bus-name wake is sent.
        self.assertEqual(self.bus.wakes, [])

    def test_portal_tools_change_is_a_decision(self):
        self.add_flatpak("com.example.Chat")
        manager = self.make()
        manager.refresh(login=True)
        manager.portal_apps.add("com.example.Chat")
        manager.policy.set_decision("com.example.Chat", True, "settings")
        manager.portal_permission_changed("com.example.Chat", "no", "yes")
        self.assertEqual(manager.describe("com.example.Chat")["decision"], "deny")

    def test_notify_background(self):
        self.add_flatpak("com.example.Chat")
        manager = self.make()
        manager.refresh(login=True)
        answers = []
        manager.portal_notify_background("com.example.Chat", answers.append)
        self.prompt.asked[0][2]("deny")
        manager.portal_notify_background("com.example.Chat", answers.append)
        self.assertEqual(answers, [0, 0])
        self.assertEqual(self.permissions.values["com.example.Chat"], "no")


    def test_foreground_native_owner_works_with_background_off_without_timers_or_decision_change(self):
        app = 'org.projectluma.Messages'
        self.add_native(app)
        manager = self.make(); manager.refresh()
        manager.schedule(app, 'later', every=600)
        manager.set_allowed(app, False, 'settings')
        before = manager.policy.decision(app)
        replies = []; manager.acquire_foreground(app, ':1.31', replies.append)
        unit = manager.record(app).unit
        self.assertEqual(replies, [True])
        self.assertEqual(manager.policy.decision(app), before)
        self.assertIn(unit, self.systemd.files)
        self.assertFalse(any(name.endswith('.timer') for name in self.systemd.files))
        self.assertFalse(manager.wake(app, 'network', {}))
        described = manager.describe(app)
        self.assertFalse(described['allowed']); self.assertTrue(described['foreground'])
        self.assertEqual(described['state'], 'running')
        manager.release_foreground(':1.foreign')
        self.assertEqual(self.systemd.status(unit).active_state, 'active')
        manager.release_foreground(':1.31')
        self.assertEqual(self.systemd.status(unit).active_state, 'inactive')
        self.assertNotIn(unit, self.systemd.files)
        self.assertFalse(manager.describe(app)['foreground'])

    def test_foreground_last_window_and_person_deny_control_actual_unit_lifetime(self):
        app = 'org.projectluma.Messages'
        self.add_native(app); manager = self.make(); manager.refresh()
        manager.acquire_foreground(app, ':1.31', lambda ok: self.assertTrue(ok))
        manager.acquire_foreground(app, ':1.32', lambda ok: self.assertTrue(ok))
        manager.set_allowed(app, False, 'settings')
        unit = manager.record(app).unit
        self.assertEqual(self.systemd.status(unit).active_state, 'active')
        manager.release_foreground(':1.31')
        self.assertEqual(self.systemd.status(unit).active_state, 'active')
        manager.release_foreground(':1.32')
        self.assertEqual(self.systemd.status(unit).active_state, 'inactive')

    def test_foreground_release_keeps_allowed_background_owner_and_explicit_stop_revokes(self):
        app = 'org.projectluma.Messages'
        self.add_native(app); manager = self.make(); manager.refresh()
        manager.acquire_foreground(app, ':1.31', lambda ok: self.assertTrue(ok))
        manager.release_foreground(':1.31')
        unit = manager.record(app).unit
        self.assertEqual(self.systemd.status(unit).active_state, 'active')
        manager.acquire_foreground(app, ':1.31', lambda ok: self.assertTrue(ok))
        manager.stop_now(app)
        self.assertFalse(manager.describe(app)['foreground'])
        self.assertEqual(self.systemd.status(unit).active_state, 'inactive')
        manager.refresh()
        self.assertEqual(self.systemd.status(unit).active_state, 'inactive')

    def test_foreground_clients_are_bounded_and_cancelled_reload_never_starts(self):
        app = 'org.projectluma.Messages'
        self.add_native(app); manager = self.make(); manager.refresh()
        manager.set_allowed(app, False, 'settings')
        callbacks = []
        self.systemd.reload = callbacks.append
        replies = []; manager.acquire_foreground(app, ':1.31', replies.append)
        manager.release_foreground(':1.31')
        callbacks[0](True)
        self.assertEqual(replies, [False])
        self.assertNotEqual(self.systemd.status(manager.record(app).unit).active_state, 'active')
        self.systemd.reload = lambda done: done(True)
        for n in range(32):
            manager.acquire_foreground(app, f':1.{n}', lambda _ok: None)
        with self.assertRaises(Refused):
            manager.acquire_foreground(app, ':1.99', lambda _ok: None)
        manager.acquire_foreground(app, ':1.0', lambda ok: self.assertTrue(ok))

    def test_foreground_untrusted_native_and_flatpak_agents_are_not_leased(self):
        app = 'com.example.Owner'
        self.add_native(app, user=True)
        manager = self.make(); manager.refresh()
        with self.assertRaises(Refused):
            manager.acquire_foreground(app, ':1.31', lambda _ok: None)
        self.assertFalse(manager._foreground)

    def test_stale_last_owner_thaw_cannot_stop_a_new_foreground_owner(self):
        app = 'org.projectluma.Messages'
        self.add_native(app); manager = self.make(); manager.refresh()
        manager.acquire_foreground(app, ':1.old', lambda ok: self.assertTrue(ok))
        manager.set_allowed(app, False, 'settings')
        unit = manager.record(app).unit
        manager._frozen.add(unit); self.systemd.frozen.add(unit)
        thaws = []
        def thaw(unit, done):
            self.systemd.calls.append(('thaw', unit))
            thaws.append(lambda: (self.systemd.frozen.discard(unit), done(True, '')))
        self.systemd.thaw = thaw
        manager.release_foreground(':1.old')
        manager.acquire_foreground(app, ':1.new', lambda ok: self.assertTrue(ok))
        self.assertEqual(len(thaws), 2)
        thaws[1]()  # The new window is using its service now.
        stops = len([call for call in self.systemd.calls if call[0] == 'stop'])
        thaws[0]()  # Late completion of the old window's close.
        self.assertEqual(self.systemd.status(unit).active_state, 'active')
        self.assertEqual(len([call for call in self.systemd.calls if call[0] == 'stop']), stops)
        manager.stop_now(app)
        self.assertEqual(self.systemd.status(unit).active_state, 'inactive')

    def test_pending_power_saver_freeze_completion_cannot_leave_foreground_frozen(self):
        app = 'org.projectluma.Messages'
        self.add_native(app, category='widget-data')
        manager = self.make(); manager.refresh()
        unit = manager.record(app).unit
        freezes = []
        def freeze(unit, done):
            freezes.append(lambda: (self.systemd.frozen.add(unit), done(True, '')))
        self.systemd.freeze = freeze
        manager.set_power_saver(True)
        self.assertEqual(len(freezes), 1)
        manager.acquire_foreground(app, ':1.window', lambda ok: self.assertTrue(ok))
        freezes[0]()
        self.assertNotIn(unit, self.systemd.frozen)
        self.assertNotIn(unit, manager._frozen)
        self.assertEqual(manager.describe(app)['state'], 'running')


class SamplerTests(unittest.TestCase):
    def test_cpu_percent_per_reader(self):
        clock = [100.0]
        sampler = CpuSampler(lambda: clock[0])
        self.assertEqual(sampler.percent("a", "x", 1_000_000), 0.0)
        clock[0] = 102.0
        self.assertEqual(sampler.percent("a", "x", 2_000_000), 50.0)
        self.assertEqual(sampler.percent("b", "x", 2_000_000), 0.0)


if __name__ == "__main__":
    unittest.main()
