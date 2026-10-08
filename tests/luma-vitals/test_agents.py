# SPDX-License-Identifier: Apache-2.0
"""Vitals restarts a background agent that keeps growing, and nothing else (ADR-026 §6)."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from luma_vitals import agents
from luma_vitals.agents import MIB, RunawayAgents, Systemd, escape_dashes, exec_argv, unescape
from luma_vitals.detectors import GIB
from luma_vitals.sampler import MachineSample, UnitSample


def sample(t, units, total=16 * GIB):
    return MachineSample(time=t, memory_total=total, memory_available=4 * GIB, swap_total=8 * GIB,
                         swap_free=2 * GIB, units=units)


def unit(name, memory, swap=0, slice_path="app.slice"):
    return UnitSample(name, 0, memory, memory, swap, 0, 0, slice_path)


class Recorder:
    def __init__(self, returncode=0, stdout=""):
        self.calls, self.returncode, self.stdout = [], returncode, stdout

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        return SimpleNamespace(returncode=self.returncode, stdout=self.stdout, stderr="")


class Environment(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        (root / "xdg/autostart").mkdir(parents=True)
        (root / "share/applications").mkdir(parents=True)
        (root / "xdg/autostart/org.projectluma.Messages.Agent.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Messages\nExec=prairie-messages --agent --autostart\n"
            "NoDisplay=true\nX-Luma-Background-Agent=org.projectluma.Messages.Agent\n")
        # An ordinary autostarted app with a window: never an agent.
        (root / "xdg/autostart/org.example.Chat.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Chat\nExec=chat --minimized %U\n")
        (root / "share/applications/org.projectluma.Messages.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Messages\nExec=prairie-messages\n")
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root / "home-config"),
                                               "XDG_CONFIG_DIRS": str(root / "xdg"),
                                               "XDG_DATA_HOME": str(root / "home-share"),
                                               "XDG_DATA_DIRS": str(root / "share")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.systemctl = Recorder(stdout="LUMA_BACKGROUND_APP_ID=org.projectluma.Messages LUMA_BACKGROUND_MANAGED=1\n")
        self.notifications = Recorder()
        self.watch = RunawayAgents(Systemd(self.systemctl), agents.Notifier(self.notifications))

    def grow(self, name, *, start, step, slice_path="app.slice", minutes=11, total=16 * GIB, swap=0):
        events = []
        for index in range(minutes * 4 + 1):
            memory = start + step * index
            events += self.watch.observe(sample(index * 15.0, [unit(name, memory, swap, slice_path)], total))
        return events


class Names(unittest.TestCase):
    def test_unit_escaping_and_exec(self):
        self.assertEqual(unescape("org.projectluma.Messages\\x2dbackground"), "org.projectluma.Messages-background")
        self.assertEqual(escape_dashes("org.example.my-app"), "org.example.my\\x2dapp")
        self.assertEqual(exec_argv("prairie-messages --agent %U"), ["prairie-messages", "--agent"])

    def test_threshold_is_the_smaller_of_two_gigabytes_and_fifteen_percent(self):
        self.assertEqual(RunawayAgents.threshold(16 * GIB), 2 * GIB)
        self.assertEqual(RunawayAgents.threshold(8 * GIB), int(8 * GIB * 0.15))


class Acting(Environment):
    def test_growing_managed_agent_is_restarted_and_the_person_told_once(self):
        name = "luma-background-org.projectluma.Messages.Agent.service"
        events = self.grow(name, start=2 * GIB, step=20 * MIB, slice_path="luma-background.slice/luma-background-essential.slice")
        self.assertEqual([e.kind for e in events], ["agent-restarted"])
        self.assertIn(["systemctl", "--user", "--quiet", "restart", name], self.systemctl.calls)
        self.assertEqual(len(self.notifications.calls), 1)
        call = self.notifications.calls[0]
        self.assertIn("Messages was restarted", call)
        self.assertTrue(any("kept growing" in part and "Luma restarted it" in part for part in call))
        # Back to the same size within the hour: reported once, not restarted or announced again.
        later = [self.watch.observe(sample(700 + i * 15.0, [unit(name, 3 * GIB + i * 20 * MIB, 0,
                  "luma-background.slice/luma-background-essential.slice")])) for i in range(90)]
        kinds = [e.kind for batch in later for e in batch]
        self.assertEqual(kinds, ["agent-still-growing"])
        self.assertEqual(sum(1 for c in self.systemctl.calls if "restart" in c), 1)
        self.assertEqual(len(self.notifications.calls), 1)

    def test_autostarted_agent_is_stopped_and_started_again_as_a_service(self):
        events = self.grow("app-gnome-org.projectluma.Messages.Agent-4242.scope", start=int(1.9 * GIB), step=16 * MIB,
                           swap=200 * MIB)
        self.assertEqual([e.kind for e in events], ["agent-restarted"])
        self.assertIn(["systemctl", "--user", "--quiet", "stop", "app-gnome-org.projectluma.Messages.Agent-4242.scope"],
                      self.systemctl.calls)
        run = next(c for c in self.systemctl.calls if c[0] == "systemd-run")
        self.assertEqual(run[run.index("--") + 1:], ["prairie-messages", "--agent", "--autostart"])
        self.assertIn("--slice=app.slice", run)
        self.assertTrue(any(a.startswith("--unit=app-gnome-org.projectluma.Messages.Agent-") for a in run))
        self.assertEqual(events[0].details["app_id"], "org.projectluma.Messages")

    def test_foreground_apps_and_unmarked_autostart_entries_are_never_touched(self):
        for name in ("app-gnome-org.mozilla.firefox-5530.scope", "app-gnome-org.example.Chat-77.scope",
                     "app-gnome-org.projectluma.Messages\\x2dbackground-12.scope", "org.gnome.Shell@wayland.service",
                     "libpod-0123456789ab.scope"):
            self.watch = RunawayAgents(Systemd(self.systemctl), agents.Notifier(self.notifications))
            self.assertEqual(self.grow(name, start=3 * GIB, step=50 * MIB), [], name)
        self.assertEqual([c for c in self.systemctl.calls if "restart" in c or "stop" in c or c[0] == "systemd-run"], [])
        self.assertEqual(self.notifications.calls, [])

    def test_large_but_steady_agent_is_left_alone(self):
        events = self.grow("app-gnome-org.projectluma.Messages.Agent-1.scope", start=3 * GIB, step=MIB)
        self.assertEqual(events, [])

    def test_small_growing_agent_is_left_to_its_limits(self):
        events = self.grow("app-gnome-org.projectluma.Messages.Agent-1.scope", start=100 * MIB, step=20 * MIB)
        self.assertEqual(events, [])

    def test_a_failed_restart_is_an_error_and_says_nothing_to_the_person(self):
        self.systemctl.returncode = 1
        events = self.grow("luma-background-x.service", start=2 * GIB, step=30 * MIB, slice_path="luma-background.slice")
        self.assertEqual([e.kind for e in events], ["agent-restart-failed"])
        self.assertEqual(self.notifications.calls, [])


class Sampling(unittest.TestCase):
    def test_units_carry_their_slice(self):
        from luma_vitals import sampler
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "user@1000.service"
            (root / "app.slice/app-gnome-x-1.scope").mkdir(parents=True)
            (root / "luma-background.slice/luma-background-essential.slice/luma-background-a.service").mkdir(parents=True)
            found = {u.unit: u.slice for u in sampler.units(root)}
        self.assertEqual(found["app-gnome-x-1.scope"], "app.slice")
        self.assertEqual(found["luma-background-a.service"], "luma-background.slice/luma-background-essential.slice")


if __name__ == "__main__":
    unittest.main()
