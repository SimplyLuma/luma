# SPDX-License-Identifier: MPL-2.0
"""luma-vitals-power-guard against a fake UPower, profiles service and logind.

Every test runs the real guard as a process on a private dbus-daemon, and
reads the outcome the way the Quick Options toggle does: the ActiveProfile
property. None of these may be skipped: a missing dbus-daemon or PyGObject is
a failure, because a guard test that did not run is not a guard test that
passed.
"""
import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from gi.repository import Gio, GLib

HERE = Path(__file__).resolve().parent
# The package's %check has tests/ beside power-guard/; the repository keeps it in src/.
GUARD = Path(os.environ.get("LUMA_POWER_GUARD") or next(
    (p for p in (HERE.parent / "power-guard" / "luma-vitals-power-guard",
                 HERE.parents[1] / "src" / "luma-vitals" / "power-guard" / "luma-vitals-power-guard")
     if p.is_file()), HERE.parent / "power-guard" / "luma-vitals-power-guard"))
FAKE = HERE / "fake_power_bus.py"
PPD = ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles")
LEGACY = ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles")
TIMEOUT = 10.0


class PowerGuardTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(GUARD.is_file(), f"the guard under test is missing: {GUARD}")
        self.tmp = Path(tempfile.mkdtemp(prefix="power-guard-"))
        self.processes = []
        self.bus_daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                                           stdout=subprocess.PIPE, text=True)
        self.processes.append(self.bus_daemon)
        self.address = self.bus_daemon.stdout.readline().strip()
        self.assertTrue(self.address, "dbus-daemon printed no address")
        self.conn = Gio.DBusConnection.new_for_address_sync(
            self.address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        self.guard = None
        self.guard_log = self.tmp / "guard.log"

    def tearDown(self):
        for process in [self.guard] + self.processes[::-1]:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
        for process in self.processes:
            if process.stdout:
                process.stdout.close()

    # --- the world -------------------------------------------------------

    def start_fake(self, power, profile):
        fake = subprocess.Popen([sys.executable, str(FAKE), self.address, power, profile],
                                stdout=subprocess.PIPE, text=True)
        self.processes.append(fake)
        self.assertEqual(fake.stdout.readline().strip(), "ready")

    def start_guard(self):
        env = dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=self.address, STATE_DIRECTORY=str(self.tmp / "state"),
                   LUMA_VITALS_LOG="stderr")
        with open(self.guard_log, "a") as log:
            self.guard = subprocess.Popen([sys.executable, str(GUARD)], env=env,
                                          stderr=log, stdout=subprocess.DEVNULL)
        self.wait_log("Checked (started)")

    def stop_guard(self):
        self.guard.send_signal(signal.SIGTERM)
        self.assertEqual(self.guard.wait(5), 0, "the guard did not stop cleanly on SIGTERM")
        self.guard = None

    def fake_call(self, method, args=None, reply=None):
        return self.conn.call_sync("org.luma.Test", "/org/luma/Test", "org.luma.Test", method, args,
                                   GLib.VariantType(reply) if reply else None, Gio.DBusCallFlags.NONE, 5000, None)

    def unplug(self, quiet=False):
        self.fake_call("SetOnBattery", GLib.Variant("(bb)", (True, quiet)))

    def plug_in(self, quiet=False):
        self.fake_call("SetOnBattery", GLib.Variant("(bb)", (False, quiet)))

    def person_chooses(self, profile):
        """What the Quick Options toggle does: write ActiveProfile."""
        self.conn.call_sync(PPD[0], PPD[1], "org.freedesktop.DBus.Properties", "Set",
                            GLib.Variant("(ssv)", (PPD[0], "ActiveProfile", GLib.Variant("s", profile))),
                            None, Gio.DBusCallFlags.NONE, 5000, None)

    def active(self, target=PPD):
        reply = self.conn.call_sync(target[0], target[1], "org.freedesktop.DBus.Properties", "Get",
                                    GLib.Variant("(ss)", (target[0], "ActiveProfile")), GLib.VariantType("(v)"),
                                    Gio.DBusCallFlags.NONE, 5000, None)
        return reply.unpack()[0]

    def guard_sets(self):
        """Profiles the guard (not the test's own connection) wrote."""
        mine = self.conn.get_unique_name()
        return [p for sender, p in self.fake_call("Sets", None, "(a(ss))").unpack()[0] if sender != mine]

    def log_lines(self):
        return self.guard_log.read_text().splitlines() if self.guard_log.exists() else []

    def wait_log(self, text, count=1):
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            if sum(text in line for line in self.log_lines()) >= count:
                return
            if self.guard and self.guard.poll() is not None:
                self.fail(f"the guard exited ({self.guard.returncode}):\n" + "\n".join(self.log_lines()[-20:]))
            time.sleep(0.05)
        self.fail(f"the guard never logged {text!r} x{count}:\n" + "\n".join(self.log_lines()[-20:]))

    def wait_active(self, expected):
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            if self.active() == expected:
                break
            time.sleep(0.05)
        self.assertEqual(self.active(), expected)
        # Both names serve one state, as tuned-ppd does; the toggle may read either.
        self.assertEqual(self.active(LEGACY), expected)

    @contextlib.contextmanager
    def after(self, why):
        """Do something, then wait until the guard has looked at the world for it."""
        seen = sum(f"Checked ({why})" in line for line in self.log_lines())
        yield
        self.wait_log(f"Checked ({why})", seen + 1)

    # --- the cases -------------------------------------------------------

    def test_unplug_steps_performance_down_to_balanced(self):
        self.start_fake("ac", "performance")
        self.start_guard()
        self.assertEqual(self.active(), "performance")
        self.assertEqual(self.guard_sets(), [], "the guard switched while plugged in")
        self.unplug()
        self.wait_active("balanced")
        self.assertEqual(self.guard_sets(), ["balanced"])
        self.wait_log("Power mode performance → balanced: running on battery")

    def test_replug_restores_the_remembered_choice(self):
        self.start_fake("ac", "performance")
        self.start_guard()
        self.unplug()
        self.wait_active("balanced")
        self.plug_in()
        self.wait_active("performance")
        self.assertEqual(self.guard_sets(), ["balanced", "performance"])
        self.wait_log("Power mode balanced → performance: power returned")

    def test_balanced_on_mains_is_left_alone_and_never_raised(self):
        self.start_fake("ac", "performance")
        self.start_guard()
        self.person_chooses("balanced")
        self.wait_log("Power mode balanced chosen on mains power")
        with self.after("power source changed"):
            self.unplug()
        with self.after("power source changed"):
            self.plug_in()
        self.assertEqual(self.active(), "balanced")
        self.assertEqual(self.guard_sets(), [], "the guard switched a profile it had no reason to touch")

    def test_a_choice_on_battery_is_respected_until_plugged_in(self):
        self.start_fake("ac", "performance")
        self.start_guard()
        self.unplug()
        self.wait_active("balanced")
        self.person_chooses("performance")
        self.wait_log("Power mode performance chosen on battery")
        with self.after("resumed"):
            self.fake_call("Resume")
        self.assertEqual(self.active(), "performance", "the guard overrode a choice made on battery")
        self.assertEqual(self.guard_sets(), ["balanced"])
        # The next battery run starts fresh: Performance is stepped down again.
        with self.after("power source changed"):
            self.plug_in()
        self.unplug()
        self.wait_active("balanced")
        self.assertEqual(self.guard_sets(), ["balanced", "balanced"])

    def test_a_lower_choice_on_battery_ends_at_the_plug(self):
        self.start_fake("ac", "performance")
        self.start_guard()
        self.unplug()
        self.wait_active("balanced")
        self.person_chooses("power-saver")
        self.wait_log("Power mode power-saver chosen on battery")
        with self.after("resumed"):
            self.fake_call("Resume")
        self.assertEqual(self.active(), "power-saver")
        self.plug_in()
        self.wait_active("performance")

    def test_never_lowers_on_mains(self):
        self.start_fake("ac", "balanced")
        self.start_guard()
        with self.after("power source changed"):
            self.unplug()
        self.person_chooses("performance")  # chosen on battery, remembered choice on mains is Balanced
        self.wait_log("Power mode performance chosen on battery")
        self.plug_in()
        self.wait_log("Kept power mode performance on mains power")
        self.assertEqual(self.active(), "performance", "the guard lowered the profile on mains power")
        self.assertEqual(self.guard_sets(), [])

    def test_survives_a_restart(self):
        self.start_fake("ac", "performance")
        self.start_guard()
        self.unplug()
        self.wait_active("balanced")
        # The guard stops (update, crash, reboot) and power returns meanwhile.
        # The profile service still says Balanced: the guard's own switch
        # persisted there, which is exactly why the guard keeps the choice.
        self.stop_guard()
        self.plug_in()
        self.assertEqual(self.active(), "balanced")
        state = json.loads((self.tmp / "state" / "state.json").read_text())
        self.assertEqual(state["ac_choice"], "performance")
        self.start_guard()
        self.wait_active("performance")

    def test_resume_catches_an_unplug_it_did_not_see(self):
        self.start_fake("ac", "performance")
        self.start_guard()
        self.unplug(quiet=True)  # the charger came out while the machine slept
        self.fake_call("Resume")
        self.wait_active("balanced")

    def test_started_on_battery_in_performance(self):
        self.start_fake("battery", "performance")
        self.start_guard()
        self.wait_active("balanced")
        self.plug_in()
        self.wait_active("performance")

    def test_a_hold_is_not_a_choice_and_is_not_overridden(self):
        self.start_fake("ac", "balanced")
        self.start_guard()
        with self.after("power source changed"):
            self.unplug()
        with self.after("profile changed"):
            self.fake_call("Hold", GLib.Variant("(s)", ("performance",)))
        self.assertEqual(self.active(), "performance")
        self.assertEqual(self.guard_sets(), [], "the guard cancelled an application's hold")
        with self.after("profile changed"):
            self.fake_call("Release")
        with self.after("power source changed"):
            self.plug_in()
        self.assertEqual(self.active(), "balanced", "a hold was remembered as the person's choice")
        self.assertEqual(self.guard_sets(), [])


if __name__ == "__main__":
    unittest.main()
