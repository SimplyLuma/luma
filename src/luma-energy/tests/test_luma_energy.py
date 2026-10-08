# SPDX-License-Identifier: MPL-2.0
"""What Luma Energy must never do, written as tests rather than as intentions."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from luma_energy import cgroups, policy  # noqa: E402
from luma_energy.claims import Claims, quiet  # noqa: E402
from luma_energy.manager import Manager  # noqa: E402

APP = "/user.slice/user-1000.slice/user@1000.service/app.slice/app-gnome-Example-1.scope"
SHELL = "/user.slice/user-1000.slice/session.slice/org.gnome.Shell@wayland.service"
AGENT = ("/user.slice/user-1000.slice/user@1000.service/luma-background.slice/"
         "luma-background-essential.slice/app-messages-agent.service")


class Recorder:
    """Stands in for the machine, so the rules can be tested without one."""

    def __init__(self) -> None:
        self.properties: dict[str, dict[str, int]] = {}
        self.uclamp: dict[str, int | None] = {}
        self.frozen: set[str] = set()
        self.cleared: list[str] = []

    def install(self, monkey: unittest.TestCase) -> None:
        originals = {name: getattr(cgroups, name) for name in
                     ("set_properties", "clear_properties", "set_uclamp",
                      "freeze", "thaw", "frozen", "exists")}
        monkey.addCleanup(lambda: [setattr(cgroups, k, v) for k, v in originals.items()])
        cgroups.set_properties = lambda unit, props: (
            self.properties.setdefault(unit, {}).update(props) or True)
        cgroups.clear_properties = lambda unit, keys: (
            self.cleared.append(unit) or self.properties.pop(unit, None) or True)
        cgroups.set_uclamp = lambda cgroup, percent: (
            self.uclamp.__setitem__(cgroup, percent) or True)
        cgroups.freeze = lambda unit: (self.frozen.add(unit) or True)
        cgroups.thaw = lambda unit: (self.frozen.discard(unit) or True)
        cgroups.frozen = lambda cgroup: cgroups.unit_of(cgroup) in self.frozen
        cgroups.exists = lambda cgroup: True


class TheForegroundIsNeverTouched(unittest.TestCase):
    def setUp(self):
        self.machine = Recorder()
        self.machine.install(self)

    def test_the_focused_application_is_left_entirely_alone(self):
        manager = Manager(on_battery=True)
        manager.set_states({APP: policy.FOCUSED})
        manager.tick(now=10_000)
        self.assertEqual(self.machine.properties, {})
        self.assertEqual(self.machine.uclamp, {})
        self.assertIsNone(policy.rung_for(policy.FOCUSED, on_battery=True))

    def test_a_visible_application_is_left_alone_too(self):
        manager = Manager(on_battery=True)
        manager.set_states({APP: policy.VISIBLE})
        manager.tick(now=10_000)
        self.assertEqual(self.machine.properties, {})


class NothingLuaEnergyMustNotReach(unittest.TestCase):
    def test_the_shell_and_the_essential_agents_are_out_of_reach(self):
        self.assertTrue(policy.protected(SHELL))
        self.assertTrue(policy.protected(AGENT))
        self.assertTrue(policy.protected("/system.slice/anything.service"))
        self.assertFalse(policy.protected(APP, cgroups.unit_of(APP)))

    def test_a_protected_cgroup_is_never_even_recorded(self):
        manager = Manager(on_battery=True)
        manager.set_states({SHELL: policy.HIDDEN, AGENT: policy.HIDDEN})
        self.assertEqual(manager.applications, {})

    def test_a_malformed_cgroup_is_refused_rather_than_guessed_at(self):
        self.assertTrue(policy.protected(""))
        self.assertTrue(policy.protected("not-a-path"))


class OnMains(unittest.TestCase):
    def setUp(self):
        self.machine = Recorder()
        self.machine.install(self)

    def test_nothing_is_clamped_and_nothing_is_ever_paused(self):
        self.assertIsNone(policy.ON_MAINS[policy.OCCLUDED].uclamp_max)
        self.assertIsNone(policy.ON_MAINS[policy.HIDDEN].uclamp_max)
        manager = Manager(freezing=True, on_battery=False)
        manager.set_states({APP: policy.HIDDEN})
        manager.tick(now=1_000_000)
        self.assertEqual(self.machine.frozen, set())

    def test_plugging_in_gives_everything_back_at_once(self):
        manager = Manager(freezing=True, on_battery=True)
        manager.set_states({APP: policy.HIDDEN})
        application = manager.applications[APP]
        application.since = 0.0
        application.cpu_mark, application.cpu_mark_at = 0, 1.0
        manager.tick(now=10_000)
        self.assertTrue(self.machine.properties)
        manager.set_on_battery(False)
        self.assertEqual(self.machine.frozen, set())
        self.assertEqual(self.machine.properties, {})


class Pausing(unittest.TestCase):
    def setUp(self):
        self.machine = Recorder()
        self.machine.install(self)

    def _hidden_and_quiet(self, **kwargs) -> Manager:
        manager = Manager(on_battery=True, **kwargs)
        manager.set_states({APP: policy.HIDDEN})
        application = manager.applications[APP]
        application.since = 0.0
        # Quiet: no processor time between marks.
        application.cpu_mark, application.cpu_mark_at = 0, 1.0
        return manager

    def test_pausing_is_off_unless_it_has_been_turned_on(self):
        manager = self._hidden_and_quiet()
        manager.tick(now=100_000)
        self.assertEqual(self.machine.frozen, set())

    def test_a_hidden_quiet_application_is_paused_once_it_is_turned_on(self):
        manager = self._hidden_and_quiet(freezing=True)
        manager.tick(now=100_000)
        self.assertIn(cgroups.unit_of(APP), self.machine.frozen)

    def test_becoming_visible_wakes_it_before_anything_else_is_decided(self):
        manager = self._hidden_and_quiet(freezing=True)
        manager.tick(now=100_000)
        self.assertTrue(self.machine.frozen)
        manager.set_states({APP: policy.VISIBLE})
        self.assertEqual(self.machine.frozen, set())

    def test_waking_everything_is_one_call(self):
        manager = self._hidden_and_quiet(freezing=True)
        manager.tick(now=100_000)
        self.assertEqual(manager.thaw_all(), 1)
        self.assertEqual(self.machine.frozen, set())

    def test_a_scope_that_did_not_actually_freeze_is_not_recorded_as_frozen(self):
        manager = self._hidden_and_quiet(freezing=True)
        cgroups.freeze = lambda unit: True          # claims success
        cgroups.frozen = lambda cgroup: False       # but it did not happen
        thawed: list[str] = []
        cgroups.thaw = lambda unit: thawed.append(unit) or True
        manager.tick(now=100_000)
        self.assertFalse(manager.applications[APP].frozen)
        self.assertEqual(thawed, [cgroups.unit_of(APP)])


class WorkThatMustNotBeInterrupted(unittest.TestCase):
    def setUp(self):
        self.machine = Recorder()
        self.machine.install(self)

    def test_an_application_still_doing_work_is_never_paused_or_limited(self):
        manager = Manager(freezing=True, on_battery=True)
        manager.set_states({APP: policy.HIDDEN})
        application = manager.applications[APP]
        application.since = 0.0
        # Busy: a whole core between marks.
        application.cpu_mark, application.cpu_mark_at = 0, 0.0
        manager.claims = Claims()
        # The manager imports cpu_usec by name, so that is what must be replaced.
        from luma_energy import manager as manager_module
        original = manager_module.cpu_usec
        self.addCleanup(lambda: setattr(manager_module, "cpu_usec", original))
        manager_module.cpu_usec = lambda path: 100_000_000
        manager.tick(now=100.0)
        self.assertEqual(self.machine.frozen, set())
        self.assertEqual(self.machine.properties, {})

    def test_the_busy_floor_catches_a_transfer_and_ignores_a_heartbeat(self):
        # A download moving bytes costs processor time and is above the floor.
        self.assertFalse(quiet(0, 2_000_000, seconds=60))
        # An application waking once a minute to do nothing is below it.
        self.assertTrue(quiet(0, 1_000, seconds=60))

    def test_the_person_outranks_every_measurement(self):
        manager = Manager(freezing=True, on_battery=True,
                          claims=Claims(keep_running={APP}))
        manager.set_states({APP: policy.HIDDEN})
        manager.applications[APP].since = 0.0
        manager.tick(now=100_000)
        self.assertEqual(self.machine.frozen, set())
        self.assertIn("the person asked for it", manager.applications[APP].reasons)


class LeavingNothingBehind(unittest.TestCase):
    def setUp(self):
        self.machine = Recorder()
        self.machine.install(self)

    def test_stopping_undoes_every_limit_and_wakes_everything(self):
        manager = Manager(freezing=True, on_battery=True)
        manager.set_states({APP: policy.HIDDEN})
        application = manager.applications[APP]
        application.since = 0.0
        application.cpu_mark, application.cpu_mark_at = 0, 1.0
        manager.tick(now=100_000)
        self.assertTrue(self.machine.properties or self.machine.frozen)
        manager.release_all()
        self.assertEqual(self.machine.frozen, set())
        self.assertEqual(self.machine.properties, {})
        self.assertEqual(manager.applications, {})
        self.assertIn(cgroups.unit_of(APP), self.machine.cleared)

    def test_the_clamp_is_removed_with_the_kernels_own_word_for_no_opinion(self):
        manager = Manager(on_battery=True)
        manager.set_states({APP: policy.OCCLUDED})
        application = manager.applications[APP]
        application.since = 0.0
        application.cpu_mark, application.cpu_mark_at = 0, 1.0
        manager.tick(now=10_000)
        self.assertEqual(self.machine.uclamp[APP], 60)
        manager.release_all()
        self.assertIsNone(self.machine.uclamp[APP])


class Naming(unittest.TestCase):
    def test_a_unit_is_read_off_the_cgroup_rather_than_guessed_from_an_app_id(self):
        self.assertEqual(cgroups.unit_of(APP), "app-gnome-Example-1.scope")
        self.assertEqual(cgroups.unit_of("/a/b/c.service"), "c.service")
        self.assertEqual(cgroups.unit_of("/a/b/notaunit"), "")

    def test_an_application_is_as_awake_as_its_most_awake_window(self):
        self.assertEqual(policy.most_awake([policy.HIDDEN, policy.FOCUSED]), policy.FOCUSED)
        self.assertEqual(policy.most_awake([policy.OCCLUDED, policy.HIDDEN]), policy.OCCLUDED)
        self.assertEqual(policy.most_awake([]), policy.HIDDEN)


if __name__ == "__main__":
    unittest.main()
