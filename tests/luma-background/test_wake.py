# SPDX-License-Identifier: MPL-2.0
"""Wake-ups for alarms: names, limits, authorization and the timers systemd is asked for."""

import unittest

from luma_background import journal, wake
from luma_background.wake import (
    InvalidArgument, LimitReached, NotAuthorized, WakeManager, calendar, parse_timer_unit, timer_unit,
)

journal.send = lambda *args, **kwargs: None

NOW = 1_789_500_000  # 2026-09-15 19:20:00 UTC


class FakeSystemd:
    def __init__(self):
        self.units: dict[str, list] = {}
        self.calls = []
        self.refuse = False

    def list_timers(self, uid):
        return [name for name in self.units if name.startswith(f"luma-wake-u{uid}-")]

    def start_timer(self, timer, properties, service, service_properties):
        self.calls.append(("start", timer, service))
        if self.refuse:
            raise wake.WakeError("refused")
        self.units[timer] = properties
        self.units_service = service_properties

    def stop(self, unit):
        self.calls.append(("stop", unit))
        self.units.pop(unit, None)


class FakeAuthority:
    def __init__(self):
        self.uids = {":1.10": 1000, ":1.20": 1001}
        self.allowed = {":1.10", ":1.20"}

    def uid(self, sender):
        return self.uids[sender]

    def authorized(self, sender):
        return sender in self.allowed


class WakeManagerTests(unittest.TestCase):
    def setUp(self):
        self.systemd, self.authority = FakeSystemd(), FakeAuthority()
        self.manager = WakeManager(self.systemd, self.authority, clock=lambda: NOW)

    def test_a_wakeup_is_a_transient_waking_timer_that_runs_nothing(self):
        at = NOW + 3600
        self.manager.set(":1.10", "org.projectluma.Clock", at)
        unit = f"luma-wake-u1000-org.projectluma.Clock-{at}.timer"
        self.assertEqual(self.systemd.calls, [("start", unit, unit.replace(".timer", ".service"))])
        properties = {key: (signature, value) for key, signature, value in self.systemd.units[unit]}
        self.assertEqual(properties["WakeSystem"], ("b", True))
        self.assertEqual(properties["RemainAfterElapse"], ("b", False))
        self.assertEqual(properties["TimersCalendar"], ("a(ss)", [("OnCalendar", "2026-09-15 20:20:00 UTC")]))
        self.assertEqual(properties["PartOf"], ("as", ["user@1000.service"]))
        service = {key: value for key, _signature, value in self.systemd.units_service}
        self.assertEqual(service["ExecStart"], [("/usr/bin/true", ["/usr/bin/true"], False)])
        self.assertEqual(service["Type"], "oneshot")
        self.assertEqual(self.manager.list(":1.10"), [("org.projectluma.Clock", at)])

    def test_moving_a_wakeup_arms_the_new_one_before_stopping_the_old(self):
        first, second = NOW + 600, NOW + 900
        self.manager.set(":1.10", "org.projectluma.Clock", first)
        self.systemd.calls.clear()
        self.manager.set(":1.10", "org.projectluma.Clock", second)
        self.assertEqual([call[0] for call in self.systemd.calls], ["start", "stop"])
        self.assertEqual(self.manager.list(":1.10"), [("org.projectluma.Clock", second)])
        self.systemd.calls.clear()
        self.manager.set(":1.10", "org.projectluma.Clock", second)
        self.assertEqual(self.systemd.calls, [], "setting the same time again changes nothing")

    def test_people_cannot_see_or_clear_each_others_wakeups(self):
        self.manager.set(":1.10", "org.projectluma.Clock", NOW + 600)
        self.assertEqual(self.manager.list(":1.20"), [])
        self.assertFalse(self.manager.clear(":1.20", "org.projectluma.Clock"))
        self.assertEqual(len(self.manager.list(":1.10")), 1)
        self.assertTrue(self.manager.clear(":1.10", "org.projectluma.Clock"))
        self.assertEqual(self.manager.list(":1.10"), [])

    def test_only_an_authorized_session_may(self):
        self.authority.allowed.discard(":1.20")
        for call in (lambda: self.manager.set(":1.20", "org.projectluma.Clock", NOW + 600),
                     lambda: self.manager.clear(":1.20", "org.projectluma.Clock"),
                     lambda: self.manager.list(":1.20")):
            with self.assertRaises(NotAuthorized):
                call()
        self.assertEqual(self.systemd.calls, [])

    def test_times_and_names_are_checked(self):
        for at in (NOW - 60, NOW + 2, NOW + 32 * 24 * 3600):
            with self.assertRaises(InvalidArgument):
                self.manager.set(":1.10", "org.projectluma.Clock", at)
        for name in ("", "../x", "a b", "x" * 200, "org.example.App;rm", "é"):
            with self.assertRaises(InvalidArgument):
                self.manager.set(":1.10", name, NOW + 600)
        self.assertEqual(self.systemd.calls, [])

    def test_each_person_has_at_most_eight(self):
        for index in range(wake.MAX_PER_USER):
            self.manager.set(":1.10", f"org.example.App{index}", NOW + 600 + index)
        with self.assertRaises(LimitReached):
            self.manager.set(":1.10", "org.example.Ninth", NOW + 700)
        # Replacing one of your own is not a new one.
        self.manager.set(":1.10", "org.example.App0", NOW + 800)
        self.manager.set(":1.20", "org.example.Other", NOW + 800)

    def test_a_refusal_from_systemd_keeps_the_old_wakeup(self):
        self.manager.set(":1.10", "org.projectluma.Clock", NOW + 600)
        self.systemd.refuse = True
        with self.assertRaises(wake.WakeError):
            self.manager.set(":1.10", "org.projectluma.Clock", NOW + 900)
        self.assertEqual(self.manager.list(":1.10"), [("org.projectluma.Clock", NOW + 600)])


class NamingTests(unittest.TestCase):
    def test_unit_names_round_trip_and_never_split_on_a_dash(self):
        unit = timer_unit(1000, "org.example.my-app", NOW)
        self.assertEqual(unit, f"luma-wake-u1000-org.example.my\\x2dapp-{NOW}.timer")
        self.assertEqual(parse_timer_unit(unit), (1000, "org.example.my-app", NOW))
        for foreign in ("luma-wake-u1000-x.timer", "app-org.example-agent.timer", "luma-wake-uroot-a-1.timer"):
            self.assertIsNone(parse_timer_unit(foreign))

    def test_calendar_is_utc(self):
        self.assertEqual(calendar(0), "1970-01-01 00:00:00 UTC")


if __name__ == "__main__":
    unittest.main()
